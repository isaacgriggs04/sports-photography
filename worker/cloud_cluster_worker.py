#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
import threading
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

try:
    import boto3
except ImportError:
    boto3 = None

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
SQS_CLUSTER_QUEUE_URL = os.getenv("SQS_CLUSTER_QUEUE_URL", "").strip()
S3_UPLOADS_BUCKET_RAW = os.getenv("S3_UPLOADS_BUCKET", "").strip()
API_INTERNAL_BASE = os.getenv("API_INTERNAL_BASE", "http://127.0.0.1:8080").rstrip("/")
WORKER_SHARED_SECRET = os.getenv("WORKER_SHARED_SECRET", "").strip()
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", ".")).resolve()
CLUSTER_SCRIPT_PATH = os.getenv("CLUSTER_SCRIPT_PATH", "update_web_clusters_combined.py").strip()
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolo26n.pt").strip()


def _parse_s3_bucket_name(raw_value):
    value = (raw_value or "").strip()
    if not value:
        return ""
    if value.startswith("s3://"):
        parsed = urlparse(value)
        bucket = (parsed.netloc or "").strip()
        if bucket:
            return bucket
        path_parts = [p for p in parsed.path.split("/") if p]
        return path_parts[0] if path_parts else ""
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        host = (parsed.netloc or "").strip()
        path_parts = [p for p in parsed.path.split("/") if p]
        if host.startswith("s3.") or host.startswith("s3-"):
            return path_parts[0] if path_parts else ""
        if ".s3." in host:
            return host.split(".s3.", 1)[0]
        if host.endswith(".amazonaws.com") and path_parts:
            return path_parts[0]
        return host
    return value


S3_UPLOADS_BUCKET = _parse_s3_bucket_name(S3_UPLOADS_BUCKET_RAW )


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def _start_health_server():
    port_raw = os.getenv("PORT", "").strip()
    if not port_raw:
        return
    try:
        port = int(port_raw)
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"Worker health server listening on 0.0.0.0:{port}", flush=True)
    except Exception as exc:
        print(f"Failed to start worker health server on PORT={port_raw}: {exc}", flush=True)


def _require_env(name, value):
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")


def _notify_job(job_id, status, payload=None):
    if not WORKER_SHARED_SECRET:
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {WORKER_SHARED_SECRET}",
    }
    body = {"status": status}
    if payload:
        body.update(payload)
    try:
        requests.post(f"{API_INTERNAL_BASE}/api/internal/jobs/{job_id}", headers=headers, json=body, timeout=15)
    except Exception as exc:
        print(f"Job status callback failed for {job_id}: {exc}")


def _download_job_images(s3, files, workdir):
    local_files = []
    seen = set()
    for f in files:
        key = f.get("key")
        if not key:
            continue
        filename = Path(f.get("filename") or Path(key).name).name
        if not filename:
            filename = Path(key).name
        if filename in seen:
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            filename = f"{stem}_{int(time.time())}_{len(seen)}{suffix}"
        seen.add(filename)
        local_path = Path(workdir) / filename
        s3.download_file(S3_UPLOADS_BUCKET, key, str(local_path))
        local_files.append(str(local_path))
    return local_files


def _run_clustering(local_files, workdir):
    if not local_files:
        raise RuntimeError("No local files downloaded for clustering")

    images_dir = Path(workdir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for src in local_files:
        src_path = Path(src)
        dst_path = images_dir / src_path.name
        src_path.replace(dst_path)

    output_json = Path(workdir) / "incremental_groups.json"
    output_crops = Path(workdir) / "incremental_crops"
    script_path = (PROJECT_ROOT / CLUSTER_SCRIPT_PATH).resolve()
    if not script_path.exists():
        raise RuntimeError(f"Cluster script not found: {script_path}")

    cmd = [
        sys.executable,
        str(script_path),
        "--images-dir",
        str(images_dir),
        "--output-json",
        str(output_json),
        "--output-crops-dir",
        str(output_crops),
    ]
    if YOLO_MODEL_PATH:
        cmd.extend(["--yolo-model", YOLO_MODEL_PATH])

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-2000:]
        stdout_tail = (result.stdout or "")[-2000:]
        raise RuntimeError(
            f"Clustering command failed (code={result.returncode}). "
            f"stdout_tail={stdout_tail!r} stderr_tail={stderr_tail!r}"
        )
    if not output_json.exists():
        raise RuntimeError("Clustering finished but output JSON was not created")

    stats = {}
    try:
        with output_json.open("r", encoding="utf-8") as f:
            out = json.load(f)
        stats = out.get("stats") if isinstance(out, dict) else {}
    except Exception:
        stats = {}

    return {
        "processed_files": len(local_files),
        "output_json_path": str(output_json),
        "stats": stats or {},
    }


def _upload_result_json(s3, job_id, output_json_path):
    key = f"cluster_results/{job_id}/incremental_groups.json"
    s3.upload_file(output_json_path, S3_UPLOADS_BUCKET, key, ExtraArgs={"ContentType": "application/json"})
    return key


def main():
    _require_env("SQS_CLUSTER_QUEUE_URL", SQS_CLUSTER_QUEUE_URL)
    _require_env("S3_UPLOADS_BUCKET", S3_UPLOADS_BUCKET)
    _require_env("API_INTERNAL_BASE", API_INTERNAL_BASE)

    if boto3 is None:
        raise RuntimeError("boto3 is required for cloud worker")

    sqs = boto3.client("sqs", region_name=AWS_REGION)
    s3 = boto3.client("s3", region_name=AWS_REGION)
    _start_health_server()

    print("Cloud cluster worker started")
    while True:
        resp = sqs.receive_message(
            QueueUrl=SQS_CLUSTER_QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=900,
            MessageAttributeNames=["All"],
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            continue

        for msg in msgs:
            receipt = msg["ReceiptHandle"]
            try:
                payload = json.loads(msg.get("Body") or "{}")
                job_id = payload.get("id")
                files = payload.get("files") or []
                if not job_id:
                    raise RuntimeError("Missing job id")

                _notify_job(job_id, "running")
                with TemporaryDirectory(prefix="cloud_cluster_job_") as workdir:
                    local_files = _download_job_images(s3, files, workdir)
                    result = _run_clustering(local_files, workdir)
                    result_key = _upload_result_json(s3, job_id, result["output_json_path"])
                    result_payload = {
                        "processed_files": result.get("processed_files", len(local_files)),
                        "stats": result.get("stats", {}),
                        "result_bucket": S3_UPLOADS_BUCKET,
                        "incremental_groups_s3_key": result_key,
                    }
                _notify_job(job_id, "completed", {"result": result_payload})

                sqs.delete_message(QueueUrl=SQS_CLUSTER_QUEUE_URL, ReceiptHandle=receipt)
            except Exception as exc:
                print(f"Worker error: {exc}")
                try:
                    payload = json.loads(msg.get("Body") or "{}")
                    job_id = payload.get("id")
                    if job_id:
                        _notify_job(job_id, "failed", {"error": str(exc)})
                except Exception:
                    pass


if __name__ == "__main__":
    main()
