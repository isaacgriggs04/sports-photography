import json
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from functools import wraps
from tempfile import TemporaryDirectory
from pathlib import Path
from json import JSONDecodeError

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import jwt
import requests
import stripe
from PIL import Image, ImageOps
try:
    import boto3
except ImportError:  # Optional in local dev
    boto3 = None
from athlete_workflow_prototype import (
    schools,
    view_schedule,
)

load_dotenv()

try:
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401
    print(f"Runtime versions: numpy={np.__version__}, cv2={cv2.__version__}", flush=True)
except Exception as _ver_err:
    print(f"Runtime version probe failed: {_ver_err}", flush=True)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
app.secret_key = "sports-photo-prototype-secret"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
CLERK_JWKS_URL = None  # Will be set from token issuer
_CLERK_JWKS_CACHE = {"keys": None, "fetched_at": 0}
_CLERK_JWK_CLIENTS = {}

# Stripe configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def _get_clerk_jwks(issuer):
    """Fetch and cache Clerk's JWKS for token verification."""
    now = time.time()
    # Cache for 1 hour
    if _CLERK_JWKS_CACHE["keys"] and (now - _CLERK_JWKS_CACHE["fetched_at"]) < 3600:
        return _CLERK_JWKS_CACHE["keys"]

    try:
        jwks_url = f"{issuer}/.well-known/jwks.json"
        resp = requests.get(jwks_url, timeout=10)
        resp.raise_for_status()
        _CLERK_JWKS_CACHE["keys"] = resp.json()
        _CLERK_JWKS_CACHE["fetched_at"] = now
        return _CLERK_JWKS_CACHE["keys"]
    except Exception as e:
        print(f"Failed to fetch Clerk JWKS: {e}")
        return None


def _verify_clerk_token(token):
    """Verify a Clerk JWT token and return the decoded payload."""
    try:
        # Decode without verification to get issuer
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified_payload.get("iss", "")
        if not issuer:
            print("Token verification error: missing issuer")
            return None

        if not hasattr(jwt, "PyJWKClient"):
            print("Token verification error: PyJWKClient unavailable in installed jwt package")
            return None

        # Resolve and cache JWKS client per issuer
        jwks_url = f"{issuer}/.well-known/jwks.json"
        jwk_client = _CLERK_JWK_CLIENTS.get(jwks_url)
        if jwk_client is None:
            jwk_client = jwt.PyJWKClient(jwks_url)
            _CLERK_JWK_CLIENTS[jwks_url] = jwk_client
        signing_key = jwk_client.get_signing_key_from_jwt(token)

        # Verify the token
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk doesn't always set aud
        )
        return payload

    except jwt.ExpiredSignatureError:
        print("Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"Invalid token: {e}")
        return None
    except Exception as e:
        print(f"Token verification error: {e}")
        return None


def require_auth(f):
    """Decorator to require Clerk authentication for an endpoint."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header.split(" ", 1)[1]
        payload = _verify_clerk_token(token)

        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        # Add user info to request context
        request.clerk_user_id = payload.get("sub")
        return f(*args, **kwargs)

    return decorated


def require_auth_or_anon_upload(f):
    """Allow unauthenticated upload testing when ALLOW_ANON_UPLOADS=true."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if os.getenv("ALLOW_ANON_UPLOADS", "false").strip().lower() == "true":
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                request.clerk_user_id = "anon-upload"
                return f(*args, **kwargs)
            token = auth_header.split(" ", 1)[1]
            payload = _verify_clerk_token(token)
            if not payload:
                request.clerk_user_id = "anon-upload"
                return f(*args, **kwargs)
            request.clerk_user_id = payload.get("sub") or "anon-upload"
            return f(*args, **kwargs)
        return require_auth(f)(*args, **kwargs)

    return decorated
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR))).resolve()
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"
PHOTO_DIR = DATA_DIR / "game_photos"
THUMB_DIR = DATA_DIR / "thumbnails"

# Ensure persistent storage dirs exist (Railway volume can be mounted at DATA_DIR, e.g. /data)
DATA_DIR.mkdir(parents=True, exist_ok=True)
PHOTO_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)
GROUPS_JSON = DATA_DIR / "athlete_groups.json"
UPLOADS_JSON = DATA_DIR / "uploads_manifest.json"
PURCHASES_JSON = DATA_DIR / "purchases.json"
USER_PROFILES_JSON = DATA_DIR / "user_profiles.json"
NOTIFICATIONS_JSON = DATA_DIR / "notifications.json"
PACKAGES_JSON = DATA_DIR / "packages.json"
CARTS_JSON = DATA_DIR / "carts.json"
CLUSTER_JOBS_JSON = DATA_DIR / "cluster_jobs.json"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
CLERK_PUBLISHABLE_KEY = os.getenv("VITE_CLERK_PUBLISHABLE_KEY", "")
INSTAGRAM_APP_ID = os.getenv("INSTAGRAM_APP_ID", "")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET", "")
INSTAGRAM_REDIRECT_URI = os.getenv("INSTAGRAM_REDIRECT_URI", "")  # e.g. http://localhost:8080/api/instagram/callback
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
S3_UPLOADS_BUCKET = os.getenv("S3_UPLOADS_BUCKET", "").strip()
S3_THUMBNAILS_BUCKET = os.getenv("S3_THUMBNAILS_BUCKET", "").strip()
SQS_CLUSTER_QUEUE_URL = os.getenv("SQS_CLUSTER_QUEUE_URL", "").strip()
CLOUDFRONT_DOMAIN = os.getenv("CLOUDFRONT_DOMAIN", "").strip()
WORKER_SHARED_SECRET = os.getenv("WORKER_SHARED_SECRET", "").strip()
DISABLE_HEAVY_CLUSTERING = os.getenv("DISABLE_HEAVY_CLUSTERING", "false").strip().lower() == "true"
ENABLE_CLOUD_UPLOADS = os.getenv("ENABLE_CLOUD_UPLOADS", "false").strip().lower() == "true"
THUMBNAIL_SIZE = (300, 300)  # Max dimensions for thumbnails
MAX_UPLOAD_LONG_EDGE = int(os.getenv("MAX_UPLOAD_LONG_EDGE", "2048"))

CLUSTER_GAME_ID = 101
CLUSTER_SCHOOL = "Homewood Flossmoor"
CLUSTER_SPORT = "Softball"
CLUSTER_STATE_LOCK = threading.Lock()
CLUSTER_DATA_LOCK = threading.Lock()
CLUSTER_STATE = {
    "running": False,
    "last_started_unix": None,
    "last_finished_unix": None,
    "last_success": None,
    "last_error": None,
}
CLUSTER_QUEUE = []

# Cached ML models (loaded once, reused)
_ML_MODELS = {
    "yolo": None,
    "face_app": None,
    "body_model": None,
    "body_preprocess": None,
    "device": None,
}
_ML_MODELS_LOCK = threading.Lock()

# Precomputed cluster embeddings cache
_CLUSTER_EMBEDDINGS_CACHE = {
    "data_hash": None,  # Hash of athlete_groups.json to detect changes
    "embeddings": {},   # cluster_id -> list of embeddings
}
_CLUSTER_EMB_LOCK = threading.Lock()


def _get_ml_models():
    """Lazily load and cache ML models for embedding extraction."""
    with _ML_MODELS_LOCK:
        if _ML_MODELS["yolo"] is None:
            import torch
            from ultralytics import YOLO
            from face_body_cluster_pipeline import _build_body_model, _build_face_app

            _ML_MODELS["device"] = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            _ML_MODELS["yolo"] = YOLO("yolo11n.pt")
            _ML_MODELS["face_app"] = _build_face_app()
            _ML_MODELS["body_model"], _ML_MODELS["body_preprocess"] = _build_body_model(_ML_MODELS["device"])
            print("ML models loaded and cached")

        return (
            _ML_MODELS["yolo"],
            _ML_MODELS["face_app"],
            _ML_MODELS["body_model"],
            _ML_MODELS["body_preprocess"],
            _ML_MODELS["device"],
        )


def _schools_with_custom():
    all_schools = list(schools)
    if not any(s["name"] == CLUSTER_SCHOOL for s in all_schools):
        all_schools.append({"name": CLUSTER_SCHOOL, "sports": [CLUSTER_SPORT]})
    return all_schools


def _load_cluster_data():
    with CLUSTER_DATA_LOCK:
        if not GROUPS_JSON.exists():
            return {"athletes": {}, "unclustered": [], "stats": {}}
        try:
            with GROUPS_JSON.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"athletes": {}, "unclustered": [], "stats": {}}
            data.setdefault("athletes", {})
            data.setdefault("unclustered", [])
            data.setdefault("stats", {})
            return data
        except (JSONDecodeError, OSError):
            return {"athletes": {}, "unclustered": [], "stats": {}}


def _write_cluster_data(data):
    tmp_path = GROUPS_JSON.with_suffix(".json.tmp")
    with CLUSTER_DATA_LOCK:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(GROUPS_JSON)


def _load_uploads_manifest():
    if not UPLOADS_JSON.exists():
        return []
    with UPLOADS_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_uploads_manifest(items):
    with UPLOADS_JSON.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def _load_user_profiles():
    """Load user profiles (instagram, etc.) keyed by clerk_user_id."""
    if not USER_PROFILES_JSON.exists():
        return {}
    try:
        with USER_PROFILES_JSON.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (JSONDecodeError, IOError):
        return {}


def _save_user_profiles(profiles):
    with USER_PROFILES_JSON.open("w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2)


def _load_notifications():
    """Load notifications list. Each item: {id, user_id, type, photo_names, amount_cents, created, read}."""
    if not NOTIFICATIONS_JSON.exists():
        return []
    try:
        with NOTIFICATIONS_JSON.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (JSONDecodeError, IOError):
        return []


def _save_notifications(notifications):
    with NOTIFICATIONS_JSON.open("w", encoding="utf-8") as f:
        json.dump(notifications, f, indent=2)


def _load_purchases():
    """Load recorded purchases (for download auth and receipts)."""
    if not PURCHASES_JSON.exists():
        return []
    try:
        with PURCHASES_JSON.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (JSONDecodeError, IOError):
        return []


def _save_purchases(purchases):
    with PURCHASES_JSON.open("w", encoding="utf-8") as f:
        json.dump(purchases, f, indent=2)


def _load_packages():
    """Load package deals keyed by uploader_id."""
    if not PACKAGES_JSON.exists():
        return {}
    try:
        with PACKAGES_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (JSONDecodeError, IOError):
        return {}


def _save_packages(packages):
    with PACKAGES_JSON.open("w", encoding="utf-8") as f:
        json.dump(packages, f, indent=2)


def _load_carts():
    """Load carts keyed by clerk_user_id."""
    if not CARTS_JSON.exists():
        return {}
    try:
        with CARTS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (JSONDecodeError, IOError):
        return {}


def _save_carts(carts):
    with CARTS_JSON.open("w", encoding="utf-8") as f:
        json.dump(carts, f, indent=2)


def _load_cluster_jobs():
    if not CLUSTER_JOBS_JSON.exists():
        return {}
    try:
        with CLUSTER_JOBS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (JSONDecodeError, IOError):
        return {}


def _save_cluster_jobs(jobs):
    with CLUSTER_JOBS_JSON.open("w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)


def _new_cluster_job(user_id, game_id, files, metadata=None):
    jobs = _load_cluster_jobs()
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    jobs[job_id] = {
        "id": job_id,
        "user_id": user_id,
        "game_id": str(game_id),
        "status": "queued",
        "files": files,
        "metadata": metadata or {},
        "created_at_unix": int(time.time()),
        "updated_at_unix": int(time.time()),
    }
    _save_cluster_jobs(jobs)
    return jobs[job_id]


def _set_cluster_job_status(job_id, status, extra=None):
    jobs = _load_cluster_jobs()
    job = jobs.get(job_id)
    if not job:
        return
    job["status"] = status
    job["updated_at_unix"] = int(time.time())
    if extra:
        job.update(extra)
    jobs[job_id] = job
    _save_cluster_jobs(jobs)


def _s3_client():
    if boto3 is None:
        return None
    try:
        return boto3.client("s3", region_name=AWS_REGION)
    except Exception:
        return None


def _sqs_client():
    if boto3 is None:
        return None
    try:
        return boto3.client("sqs", region_name=AWS_REGION)
    except Exception:
        return None


def _uploads_in_cloud_enabled():
    return bool(S3_UPLOADS_BUCKET)


def _cluster_queue_enabled():
    return bool(SQS_CLUSTER_QUEUE_URL)


def _enqueue_cluster_job(job):
    if not _cluster_queue_enabled():
        return False, "SQS_CLUSTER_QUEUE_URL is not configured"
    sqs = _sqs_client()
    if sqs is None:
        return False, "boto3 is not installed/configured"
    try:
        sqs.send_message(
            QueueUrl=SQS_CLUSTER_QUEUE_URL,
            MessageBody=json.dumps(job),
            MessageAttributes={
                "job_id": {"StringValue": job["id"], "DataType": "String"},
                "game_id": {"StringValue": str(job["game_id"]), "DataType": "String"},
                "user_id": {"StringValue": str(job["user_id"]), "DataType": "String"},
            },
        )
        return True, None
    except Exception as e:
        return False, str(e)


def _safe_storage_name(name):
    cleaned = secure_filename(str(name or "").strip())
    if not cleaned:
        cleaned = f"upload_{uuid.uuid4().hex[:8]}.jpg"
    return cleaned


def _build_storage_key(user_id, game_id, filename):
    now = int(time.time())
    cleaned = _safe_storage_name(filename)
    return f"uploads/{user_id}/{game_id}/{now}_{uuid.uuid4().hex[:8]}_{cleaned}"


def _cloudfront_url_for_key(key):
    if not key:
        return ""
    if not CLOUDFRONT_DOMAIN:
        return ""
    return f"https://{CLOUDFRONT_DOMAIN.strip('/')}/{key.lstrip('/')}"


def _all_cluster_photo_names(cluster_data):
    names = {
        det.get("photo")
        for dets in cluster_data.get("athletes", {}).values()
        for det in dets
        if det.get("photo")
    }
    names.update(det.get("photo") for det in cluster_data.get("unclustered", []) if det.get("photo"))
    return names


def _ingest_cloud_incremental_result(job, result_payload):
    if not isinstance(result_payload, dict):
        return True, {}

    result_key = (result_payload.get("incremental_groups_s3_key") or "").strip()
    if not result_key:
        return True, {}

    result_bucket = (result_payload.get("result_bucket") or S3_UPLOADS_BUCKET).strip()
    if not result_bucket:
        return False, {"error": "Cloud result bucket missing; cannot ingest clustering output."}

    s3 = _s3_client()
    if s3 is None:
        return False, {"error": "AWS SDK unavailable while ingesting clustering output."}

    try:
        with TemporaryDirectory(prefix="cloud_cluster_ingest_", dir=str(BASE_DIR)) as tmp_dir:
            tmp_path = Path(tmp_dir) / "incremental_groups.json"
            s3.download_file(result_bucket, result_key, str(tmp_path))
            _merge_incremental_clusters(tmp_path)
    except Exception as exc:
        return False, {"error": f"Failed to ingest clustering output from s3://{result_bucket}/{result_key}: {exc}"}

    job_files = []
    for f in (job or {}).get("files", []):
        if isinstance(f, dict):
            filename = (f.get("filename") or "").strip()
            if filename:
                job_files.append(filename)
    if job_files:
        data = _load_cluster_data()
        missing = [name for name in job_files if name not in _all_cluster_photo_names(data)]
        if missing:
            _append_photos_as_unclustered(missing)

    return True, {"ingested_result_key": result_key}


def _resolve_photo_urls(photo_name, manifest_entry):
    storage_key = (manifest_entry or {}).get("storage_key")
    thumbnail_key = (manifest_entry or {}).get("thumbnail_key")
    cloud_image_url = _cloudfront_url_for_key(storage_key)
    cloud_thumb_url = _cloudfront_url_for_key(thumbnail_key) or cloud_image_url
    if cloud_image_url:
        return cloud_image_url, cloud_thumb_url
    return f"/api/images/{photo_name}", f"/api/thumbnails/{photo_name}"


def _normalize_cart_items(items):
    """Normalize a submitted cart to safe, deduplicated photo objects."""
    if not isinstance(items, list):
        return []

    normalized = []
    seen = set()
    allowed_passthrough = {
        "image_path",
        "thumbnail_path",
        "photographer",
        "photographer_image_url",
        "uploader_id",
        "include_in_package",
    }

    for raw in items:
        if not isinstance(raw, dict):
            continue

        image_url = str(raw.get("image_url") or raw.get("photo_name") or "").strip()
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)

        item = {"image_url": image_url}
        for key in allowed_passthrough:
            value = raw.get(key)
            if value is not None:
                item[key] = value

        try:
            item["price"] = float(raw.get("price", 5))
        except (TypeError, ValueError):
            item["price"] = 5.0
        if item["price"] < 0.5:
            item["price"] = 5.0

        normalized.append(item)
        if len(normalized) >= 500:
            break

    return normalized


def _normalize_package_deals(deals):
    """Return sorted, deduplicated package deals."""
    best_by_qty = {}
    for raw in deals or []:
        try:
            qty = int(raw.get("quantity", 0))
            price_cents = int(raw.get("package_price_cents", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if qty < 2 or price_cents < 50:
            continue
        prev = best_by_qty.get(qty)
        if prev is None or price_cents < prev:
            best_by_qty[qty] = price_cents

    normalized = [
        {"quantity": qty, "package_price_cents": cents}
        for qty, cents in best_by_qty.items()
    ]
    normalized.sort(key=lambda d: (d["quantity"], d["package_price_cents"]))
    return normalized


def _package_deals_for_uploader(uploader_id):
    data = _load_packages()
    deals = data.get(uploader_id, {}).get("deals", [])
    return _normalize_package_deals(deals)


def _price_cents_from_manifest_price(value, default_cents=500):
    try:
        cents = int(round(float(value) * 100))
    except (TypeError, ValueError):
        cents = default_cents
    if cents < 50:
        return default_cents
    return cents


def _price_cents_from_item(item, default_cents=500):
    try:
        cents = int(item.get("price_cents", default_cents))
    except (TypeError, ValueError, AttributeError):
        cents = default_cents
    if cents < 50:
        return default_cents
    return cents


def _optimize_uploader_packages(photo_items, deals):
    """Choose package combinations that minimize cost for one uploader."""
    if not photo_items or not deals:
        base_total = sum(int(p["base_price_cents"]) for p in photo_items)
        return {
            "covered_photo_names": set(),
            "covered_count": 0,
            "total_cents_after_packages": base_total,
            "base_total_cents": base_total,
            "savings_cents": 0,
            "used_deals": [],
        }

    sorted_items = sorted(photo_items, key=lambda p: int(p["base_price_cents"]), reverse=True)
    n = len(sorted_items)
    prices = [int(p["base_price_cents"]) for p in sorted_items]
    total = sum(prices)

    prefix = [0] * (n + 1)
    for i in range(1, n + 1):
        prefix[i] = prefix[i - 1] + prices[i - 1]

    inf = 10 ** 18
    dp = [inf] * (n + 1)
    choice = [None] * (n + 1)
    dp[0] = 0

    for k in range(1, n + 1):
        for deal in deals:
            qty = int(deal["quantity"])
            pack_cents = int(deal["package_price_cents"])
            if qty <= k and dp[k - qty] != inf:
                cand = dp[k - qty] + pack_cents
                if cand < dp[k]:
                    dp[k] = cand
                    choice[k] = (k - qty, qty, pack_cents)

    best_total = total
    best_k = 0
    for k in range(1, n + 1):
        if dp[k] == inf:
            continue
        remain_individual = total - prefix[k]
        cand_total = dp[k] + remain_individual
        if cand_total < best_total:
            best_total = cand_total
            best_k = k

    if best_k == 0:
        return {
            "covered_photo_names": set(),
            "covered_count": 0,
            "total_cents_after_packages": total,
            "base_total_cents": total,
            "savings_cents": 0,
            "used_deals": [],
        }

    usage = {}
    k = best_k
    while k > 0 and choice[k] is not None:
        prev_k, qty, pack_cents = choice[k]
        key = (qty, pack_cents)
        usage[key] = usage.get(key, 0) + 1
        k = prev_k

    used_deals = [
        {
            "quantity": qty,
            "package_price_cents": pack_cents,
            "times_applied": times,
        }
        for (qty, pack_cents), times in sorted(usage.items(), key=lambda x: (x[0][0], x[0][1]))
    ]

    covered_names = {sorted_items[idx]["photo_name"] for idx in range(best_k)}
    savings = total - best_total
    return {
        "covered_photo_names": covered_names,
        "covered_count": best_k,
        "total_cents_after_packages": int(best_total),
        "base_total_cents": int(total),
        "savings_cents": int(max(0, savings)),
        "used_deals": used_deals,
    }


def _build_checkout_quote(items):
    """Compute package pricing and Stripe line items from raw cart items."""
    manifest = _load_uploads_manifest()
    profiles = _load_user_profiles()
    packages = _load_packages()
    manifest_by_name = {}
    for entry in manifest:
        filename = entry.get("filename")
        if filename:
            manifest_by_name[filename] = entry

    unique_photo_names = []
    seen = set()
    normalized_items = []
    for item in items or []:
        photo_name = (item.get("photo_name") or "").strip()
        if not photo_name or photo_name in seen:
            continue
        seen.add(photo_name)
        unique_photo_names.append(photo_name)
        manifest_entry = manifest_by_name.get(photo_name, {})
        if manifest_entry.get("price") is not None:
            base_cents = _price_cents_from_manifest_price(manifest_entry.get("price"))
        else:
            base_cents = _price_cents_from_item(item)
        include_flag = manifest_entry.get("include_in_package")
        include_in_package = True if include_flag is None else bool(include_flag)
        normalized_items.append({
            "photo_name": photo_name,
            "base_price_cents": base_cents,
            "uploader_id": manifest_entry.get("uploader_id"),
            "photographer": manifest_entry.get("photographer") or "SportsPic Photographer",
            "include_in_package": include_in_package,
        })

    subtotal_cents = sum(int(i["base_price_cents"]) for i in normalized_items)
    if not normalized_items:
        return {
            "photo_names": [],
            "subtotal_cents": 0,
            "total_cents": 0,
            "savings_cents": 0,
            "applied_packages": [],
            "available_packages": [],
            "stripe_line_items": [],
        }

    package_covered_names = set()
    applied_packages = []
    available_packages = []

    by_uploader = {}
    for item in normalized_items:
        uploader_id = item.get("uploader_id")
        if not uploader_id:
            continue
        by_uploader.setdefault(uploader_id, []).append(item)

    for uploader_id, uploader_items in by_uploader.items():
        raw_deals = packages.get(uploader_id, {}).get("deals", [])
        deals = _normalize_package_deals(raw_deals)
        if not deals:
            continue

        eligible = [i for i in uploader_items if i.get("include_in_package")]
        if not eligible:
            continue

        display_name = (
            profiles.get(uploader_id, {}).get("display_name")
            or next((i.get("photographer") for i in uploader_items if i.get("photographer")), None)
            or "SportsPic Photographer"
        )

        available_packages.append({
            "uploader_id": uploader_id,
            "photographer": display_name,
            "eligible_photo_count": len(eligible),
            "deals": [
                {
                    "quantity": int(d["quantity"]),
                    "package_price_cents": int(d["package_price_cents"]),
                }
                for d in deals
            ],
        })

        optimized = _optimize_uploader_packages(eligible, deals)
        if optimized["savings_cents"] <= 0:
            continue

        package_covered_names.update(optimized["covered_photo_names"])
        covered_total_for_uploader = sum(
            int(i["base_price_cents"])
            for i in eligible
            if i["photo_name"] in optimized["covered_photo_names"]
        )
        package_total_for_uploader = 0
        for used in optimized["used_deals"]:
            package_total_for_uploader += int(used["package_price_cents"]) * int(used["times_applied"])

        uploader_savings = max(0, covered_total_for_uploader - package_total_for_uploader)

        for used in optimized["used_deals"]:
            covered_count = int(used["quantity"]) * int(used["times_applied"])
            line_total = int(used["package_price_cents"]) * int(used["times_applied"])
            applied_packages.append({
                "uploader_id": uploader_id,
                "photographer": display_name,
                "quantity": int(used["quantity"]),
                "package_price_cents": int(used["package_price_cents"]),
                "times_applied": int(used["times_applied"]),
                "photos_covered": covered_count,
                "line_total_cents": line_total,
                "savings_cents": uploader_savings,
            })

    stripe_line_items = []
    for pkg in applied_packages:
        stripe_line_items.append({
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": f"Package Deal: {pkg['quantity']} photos",
                    "description": f"{pkg['photographer']} package",
                },
                "unit_amount": int(pkg["package_price_cents"]),
            },
            "quantity": int(pkg["times_applied"]),
        })

    for item in normalized_items:
        if item["photo_name"] in package_covered_names:
            continue
        stripe_line_items.append({
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": f"Photo: {item['photo_name']}",
                    "description": "High-resolution sports photo download",
                },
                "unit_amount": int(item["base_price_cents"]),
            },
            "quantity": 1,
        })

    total_cents = 0
    for li in stripe_line_items:
        unit_amount = int(li["price_data"]["unit_amount"])
        qty = int(li.get("quantity", 1))
        total_cents += unit_amount * qty

    savings_cents = max(0, subtotal_cents - total_cents)
    return {
        "photo_names": unique_photo_names,
        "subtotal_cents": int(subtotal_cents),
        "total_cents": int(total_cents),
        "savings_cents": int(savings_cents),
        "applied_packages": applied_packages,
        "available_packages": available_packages,
        "stripe_line_items": stripe_line_items,
    }


def _get_photographer_email(uploader_id):
    """Get photographer email from user_profiles, or Clerk API as fallback."""
    profiles = _load_user_profiles()
    email = (profiles.get(uploader_id, {}).get("notification_email") or "").strip()
    if email:
        return email
    # Fallback: Clerk API
    if not CLERK_SECRET_KEY:
        return None
    try:
        resp = requests.get(
            f"https://api.clerk.com/v1/users/{uploader_id}",
            headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
            timeout=5,
        )
        if resp.ok:
            data = resp.json()
            for addr in data.get("email_addresses", []):
                if addr.get("id") == data.get("primary_email_address_id"):
                    return (addr.get("email_address") or "").strip()
            if data.get("email_addresses"):
                return (data["email_addresses"][0].get("email_address") or "").strip()
    except Exception as e:
        print(f"Clerk API error fetching user {uploader_id}: {e}")
    return None


def _send_photographer_purchase_notification(to_email, photo_names, amount_dollars, photographer_name=None):
    """Notify a photographer that their photo(s) were purchased."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("FROM_EMAIL", smtp_user or "noreply@sportspic.local")
    if not smtp_host or not smtp_user or not smtp_password:
        print("Photographer notification skipped: SMTP not configured")
        return
    try:
        import smtplib
        from email.mime.text import MIMEText

        count = len(photo_names)
        subject = f"Someone purchased {count} of your photo{'s' if count != 1 else ''} on SportsPic!"
        body_lines = [
            "Great news!",
            "",
            f"A customer just purchased {count} of your photo{'s' if count != 1 else ''} on SportsPic.",
            "",
            f"Your share: ${amount_dollars:.2f}",
            "",
            "Photos purchased:",
        ]
        body_lines.extend(f"  • {name}" for name in photo_names)
        body_lines.append("")
        body_lines.append("Thank you for being part of SportsPic!")
        msg = MIMEText("\n".join(body_lines), "plain")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        print(f"Photographer notification sent to {to_email}")
    except Exception as e:
        print(f"Failed to send photographer notification to {to_email}: {e}")


def _send_purchase_email(to_email, photo_names, amount_cents, stripe_session_id=None):
    """Send receipt email with purchased photos attached. Uses SMTP from env."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("FROM_EMAIL", smtp_user or "noreply@sportspic.local")
    if not smtp_host or not smtp_user or not smtp_password:
        print("Purchase email skipped: SMTP_HOST, SMTP_USER, SMTP_PASSWORD not set")
        return
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        msg = MIMEMultipart()
        msg["Subject"] = "Your SportsPic purchase – photos and receipt"
        msg["From"] = from_email
        msg["To"] = to_email
        total_dollars = amount_cents / 100.0
        body_lines = [
            "Thanks for your purchase!",
            "",
            "Receipt",
            "-------",
            f"Order: {stripe_session_id or 'N/A'}",
            f"Total: ${total_dollars:.2f}",
            "",
            "Photos:",
        ]
        body_lines.extend(f"  • {name}" for name in photo_names)
        body_lines.append("")
        body_lines.append("Your photos are attached to this email.")
        msg.attach(MIMEText("\n".join(body_lines), "plain"))

        for photo_name in photo_names:
            path = PHOTO_DIR / photo_name
            if not path.exists():
                continue
            with open(path, "rb") as fp:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(fp.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=photo_name)
            msg.attach(part)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        print(f"Purchase email sent to {to_email} for {len(photo_names)} photo(s)")
    except Exception as e:
        print(f"Failed to send purchase email to {to_email}: {e}")


def _save_uploaded_files(files, photographer, school, sport, game_id, price=None, include_in_package=True, uploader_id=None):
    """Persist incoming photos and append metadata entries."""
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)

    uploaded = []
    skipped = 0
    manifest = _load_uploads_manifest()
    now_ts = int(time.time())

    # Parse price to float, default to 0
    try:
        price_value = float(price) if price else 0.0
    except (ValueError, TypeError):
        price_value = 0.0

    for idx, file in enumerate(files, start=1):
        if not file or not file.filename:
            continue

        clean_name = secure_filename(file.filename)
        suffix = Path(clean_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            skipped += 1
            continue

        target_path = PHOTO_DIR / clean_name
        if target_path.exists():
            stem = Path(clean_name).stem
            target_path = PHOTO_DIR / f"{stem}_{now_ts}_{idx}{suffix}"

        file.save(target_path)
        try:
            _normalize_uploaded_image(target_path)
        except Exception as exc:
            print(f"Image normalization failed for {target_path.name}: {exc}", flush=True)
            target_path.unlink(missing_ok=True)
            skipped += 1
            continue
        uploaded.append(target_path.name)
        entry = {
            "filename": target_path.name,
            "uploaded_at_unix": now_ts,
            "photographer": photographer,
            "school": school,
            "sport": sport,
            "game_id": str(game_id),
            "price": price_value,
            "include_in_package": bool(include_in_package),
        }
        if uploader_id:
            entry["uploader_id"] = uploader_id
        manifest.append(entry)

    _save_uploads_manifest(manifest)
    return uploaded, skipped


def _normalize_uploaded_image(path: Path):
    """
    Normalize uploaded image for stable CV/ML processing:
    - apply EXIF orientation
    - convert non-RGB modes to RGB
    - downscale oversized images to MAX_UPLOAD_LONG_EDGE
    """
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB",):
            img = img.convert("RGB")

        w, h = img.size
        max_side = max(w, h)
        if max_side > MAX_UPLOAD_LONG_EDGE:
            scale = MAX_UPLOAD_LONG_EDGE / float(max_side)
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )

        save_kwargs = {}
        fmt = "JPEG"
        if suffix == ".png":
            fmt = "PNG"
            save_kwargs = {"optimize": True}
        elif suffix == ".webp":
            fmt = "WEBP"
            save_kwargs = {"quality": 92, "method": 4}
        else:
            # .jpg/.jpeg
            save_kwargs = {"quality": 92, "optimize": True}
        img.save(path, format=fmt, **save_kwargs)


def _photo_sha256(photo_name):
    path = PHOTO_DIR / photo_name
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _build_hash_to_cluster_map(athletes):
    mapping = {}
    for cluster_id, detections in athletes.items():
        for det in detections:
            photo_name = det.get("photo")
            if not photo_name:
                continue
            digest = _photo_sha256(photo_name)
            if digest and digest not in mapping:
                mapping[digest] = cluster_id
    return mapping


def _next_athlete_index(athletes):
    max_idx = 0
    for key in athletes.keys():
        if key.startswith("athlete_"):
            try:
                max_idx = max(max_idx, int(key.split("_", 1)[1]))
            except ValueError:
                continue
    return max_idx + 1


def _merge_incremental_clusters(incremental_json_path):
    existing = _load_cluster_data()
    with incremental_json_path.open("r", encoding="utf-8") as f:
        inc = json.load(f)

    athletes = dict(existing.get("athletes", {}))
    next_idx = _next_athlete_index(athletes)
    hash_to_cluster = _build_hash_to_cluster_map(athletes)
    for _old_cluster_id, detections in sorted(inc.get("athletes", {}).items()):
        unmatched = []
        for det in detections:
            photo_name = det.get("photo")
            digest = _photo_sha256(photo_name) if photo_name else None
            matched_cluster = hash_to_cluster.get(digest) if digest else None
            if matched_cluster:
                athletes.setdefault(matched_cluster, []).append(det)
            else:
                unmatched.append(det)

        if unmatched:
            new_cluster = f"athlete_{next_idx}"
            athletes[new_cluster] = unmatched
            for det in unmatched:
                photo_name = det.get("photo")
                digest = _photo_sha256(photo_name) if photo_name else None
                if digest:
                    hash_to_cluster[digest] = new_cluster
            next_idx += 1

    merged_unclustered = list(existing.get("unclustered", []))
    for det in inc.get("unclustered", []):
        photo_name = det.get("photo")
        digest = _photo_sha256(photo_name) if photo_name else None
        matched_cluster = hash_to_cluster.get(digest) if digest else None
        if matched_cluster:
            athletes.setdefault(matched_cluster, []).append(det)
        else:
            merged_unclustered.append(det)

    merged = {
        "athletes": athletes,
        "unclustered": merged_unclustered,
        "stats": {
            "images": len({det["photo"] for dets in athletes.values() for det in dets} | {det["photo"] for det in merged_unclustered}),
            "detections": sum(len(v) for v in athletes.values()) + len(merged_unclustered),
            "clustered_detections": sum(len(v) for v in athletes.values()),
            "clusters": len(athletes),
            "method": "incremental_append_new_uploads",
        },
    }
    _write_cluster_data(merged)


def _extract_embedding_for_photo(photo_name, yolo, face_app, body_model, body_preprocess, device):
    """Extract combined face+body embedding for a photo. Returns None if no detection."""
    import numpy as np
    from face_body_cluster_pipeline import detect_people, extract_body_embedding, load_image_bgr
    from debug_embedding_clustering_v2 import extract_face_embedding_with_quality, normalize, adaptive_weights

    photo_path = PHOTO_DIR / photo_name
    if not photo_path.exists():
        return None

    image = load_image_bgr(photo_path)
    if image is None:
        return None

    detections = detect_people(
        image_path=photo_path,
        image_bgr=image,
        yolo_model=yolo,
        conf_threshold=0.25,
    )
    if not detections:
        return None

    # Use largest detection
    det = max(detections, key=lambda d: (d["bbox_xyxy"][2] - d["bbox_xyxy"][0]) * (d["bbox_xyxy"][3] - d["bbox_xyxy"][1]))
    crop = det["body_crop_bgr"]

    body_raw = extract_body_embedding(crop, body_model, body_preprocess, device)
    body_n = normalize(body_raw)
    face_raw, face_score, face_ratio = extract_face_embedding_with_quality(crop, face_app)

    if face_raw is not None:
        face_n = normalize(face_raw)
        fw, bw = adaptive_weights(face_score, face_ratio)
        return normalize(np.concatenate([fw * face_n, bw * body_n], axis=0))
    else:
        zero_face = np.zeros_like(body_n, dtype=np.float32)
        return normalize(np.concatenate([zero_face, body_n], axis=0))


def _get_cluster_embeddings_cached():
    """
    Get precomputed cluster embeddings (with caching).
    Only recomputes when athlete_groups.json changes.
    Returns dict: cluster_id -> centroid embedding (numpy array)
    """
    import numpy as np
    from debug_embedding_clustering_v2 import normalize

    # Check if cache is valid
    data = _load_cluster_data()
    data_str = json.dumps(data.get("athletes", {}), sort_keys=True)
    data_hash = hashlib.md5(data_str.encode()).hexdigest()

    with _CLUSTER_EMB_LOCK:
        if _CLUSTER_EMBEDDINGS_CACHE["data_hash"] == data_hash:
            return _CLUSTER_EMBEDDINGS_CACHE["embeddings"]

    # Need to recompute - load models
    yolo, face_app, body_model, body_preprocess, device = _get_ml_models()
    athletes = data.get("athletes", {})
    cluster_centroids = {}

    for cluster_id, cluster_detections in athletes.items():
        embeddings = []
        # Sample up to 3 photos per cluster for centroid
        for cdet in cluster_detections[:3]:
            photo = cdet.get("photo", "")
            if not photo:
                continue
            emb = _extract_embedding_for_photo(photo, yolo, face_app, body_model, body_preprocess, device)
            if emb is not None:
                embeddings.append(emb)

        if embeddings:
            centroid = normalize(np.mean(np.vstack(embeddings), axis=0))
            cluster_centroids[cluster_id] = centroid

    # Update cache
    with _CLUSTER_EMB_LOCK:
        _CLUSTER_EMBEDDINGS_CACHE["data_hash"] = data_hash
        _CLUSTER_EMBEDDINGS_CACHE["embeddings"] = cluster_centroids

    print(f"Cluster embeddings cache rebuilt: {len(cluster_centroids)} clusters")
    return cluster_centroids


def _try_assign_by_embedding(photo_name):
    """
    Try to assign a single photo to an existing cluster using embedding similarity.
    Uses cached models and precomputed cluster centroids for speed.
    Returns the cluster_id if a strong match is found, otherwise None.
    """
    try:
        import numpy as np

        # Get cached models
        yolo, face_app, body_model, body_preprocess, device = _get_ml_models()

        # Extract embedding for uploaded photo
        upload_emb = _extract_embedding_for_photo(photo_name, yolo, face_app, body_model, body_preprocess, device)
        if upload_emb is None:
            return None

        # Get cached cluster centroids
        cluster_centroids = _get_cluster_embeddings_cached()
        if not cluster_centroids:
            return None

        best_cluster = None
        best_similarity = 0.55  # Minimum threshold for assignment

        for cluster_id, centroid in cluster_centroids.items():
            similarity = float(np.dot(upload_emb, centroid))
            if similarity > best_similarity:
                best_similarity = similarity
                best_cluster = cluster_id

        return best_cluster

    except Exception as e:
        print(f"Embedding assignment failed: {e}")
        return None


def _append_photos_as_unclustered(photo_names):
    """
    Fallback path for tiny/crashed batches:
    1. Try embedding similarity to assign to existing clusters (for same-person matching).
    2. Fall back to hash matching for byte-identical duplicates.
    3. Otherwise, keep it visible in Unknown.
    """
    data = _load_cluster_data()
    athletes = dict(data.get("athletes", {}))
    unclustered = list(data.get("unclustered", []))
    existing_photos = {det.get("photo") for det in unclustered}
    existing_athlete_photos = {
        det.get("photo")
        for dets in athletes.values()
        for det in dets
        if det.get("photo")
    }

    hash_to_cluster = _build_hash_to_cluster_map(athletes)

    for name in photo_names:
        if name in existing_photos or name in existing_athlete_photos:
            continue

        # First try embedding-based assignment (visual similarity)
        matched_cluster = None if DISABLE_HEAVY_CLUSTERING else _try_assign_by_embedding(name)

        # Fall back to hash matching
        if not matched_cluster:
            digest = _photo_sha256(name)
            matched_cluster = hash_to_cluster.get(digest) if digest else None

        detection_record = {
            "photo": name,
            "bbox_xyxy": [],
            "confidence": 1.0 if matched_cluster else 0.0,
            "jersey_number": None,
            "jersey_confidence": None,
        }

        if matched_cluster:
            athletes.setdefault(matched_cluster, []).append(detection_record)
        else:
            unclustered.append(detection_record)

    data["athletes"] = athletes
    data["unclustered"] = unclustered
    _write_cluster_data(data)


def _run_full_reclustering():
    cmd = [
        sys.executable,
        "update_web_clusters_combined.py",
        "--images-dir",
        str(PHOTO_DIR),
        "--output-json",
        str(GROUPS_JSON),
        "--output-crops-dir",
        str(DATA_DIR / "athlete_groups"),
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "code": result.returncode,
            "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-3000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "code": -1,
            "stdout": "",
            "stderr": "Clustering timed out after 300 seconds.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": -1,
            "stdout": "",
            "stderr": f"Clustering failed: {exc}",
        }


def _run_incremental_reclustering(new_files):
    files_to_cluster = [name for name in new_files if (PHOTO_DIR / name).exists()]
    if not files_to_cluster:
        return {"ok": True, "code": 0, "stdout": "", "stderr": ""}
    if len(files_to_cluster) < 2:
        # HDBSCAN is unstable for tiny batches; surface new uploads via Unknown until more arrive.
        _append_photos_as_unclustered(files_to_cluster)
        return {
            "ok": True,
            "code": 0,
            "stdout": "",
            "stderr": "",
        }

    try:
        with TemporaryDirectory(prefix="incremental_cluster_", dir=str(BASE_DIR)) as tmp_dir:
            tmp_root = Path(tmp_dir)
            tmp_images = tmp_root / "images"
            tmp_images.mkdir(parents=True, exist_ok=True)
            for name in files_to_cluster:
                shutil.copy2(PHOTO_DIR / name, tmp_images / name)

            tmp_output_json = tmp_root / "incremental_groups.json"
            tmp_crops_dir = tmp_root / "incremental_crops"
            cmd = [
                sys.executable,
                "update_web_clusters_combined.py",
                "--images-dir",
                str(tmp_images),
                "--output-json",
                str(tmp_output_json),
                "--output-crops-dir",
                str(tmp_crops_dir),
            ]
            result = subprocess.run(
                cmd,
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if result.returncode != 0:
                if result.returncode < 0:
                    _append_photos_as_unclustered(files_to_cluster)
                    return {
                        "ok": True,
                        "code": 0,
                        "stdout": "",
                        "stderr": "",
                    }
                return {
                    "ok": False,
                    "code": result.returncode,
                    "stdout": result.stdout[-3000:],
                    "stderr": result.stderr[-3000:],
                }

            _merge_incremental_clusters(tmp_output_json)

            # Ensure every uploaded file is represented (clustering may skip images with no detection)
            data = _load_cluster_data()
            all_photos = {
                det.get("photo")
                for dets in data.get("athletes", {}).values()
                for det in dets
                if det.get("photo")
            }
            all_photos.update(
                det.get("photo") for det in data.get("unclustered", []) if det.get("photo")
            )
            missing = [f for f in files_to_cluster if f not in all_photos]
            if missing:
                _append_photos_as_unclustered(missing)
            return {
                "ok": True,
                "code": 0,
                "stdout": result.stdout[-3000:],
                "stderr": "",
            }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "code": -1,
            "stdout": "",
            "stderr": "Incremental clustering timed out after 300 seconds.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": -1,
            "stdout": "",
            "stderr": f"Incremental clustering failed: {exc}",
        }


def _run_reclustering_worker(initial_files):
    pending_files = list(initial_files or [])
    result = _run_incremental_reclustering(pending_files) if pending_files else _run_full_reclustering()
    if pending_files and not result.get("ok", False):
        # Preserve uploaded photos in the Unknown bucket when clustering fails.
        _append_photos_as_unclustered(pending_files)

    with CLUSTER_STATE_LOCK:
        CLUSTER_STATE["last_finished_unix"] = int(time.time())
        CLUSTER_STATE["last_success"] = bool(result.get("ok", False))
        err = None if result.get("ok", False) else result.get("stderr", "Cluster update failed.")
        CLUSTER_STATE["last_error"] = err
        if CLUSTER_QUEUE:
            next_files = list(CLUSTER_QUEUE)
            CLUSTER_QUEUE.clear()
        else:
            CLUSTER_STATE["running"] = False
            next_files = []

    if err:
        print(f"[CLUSTERING ERROR] {err}", flush=True)
        if result.get("stdout"):
            print(f"[CLUSTERING STDOUT] {result['stdout'][-1500:]}", flush=True)

    if next_files:
        _run_reclustering_worker(next_files)


def _start_reclustering_async(new_files=None):
    if DISABLE_HEAVY_CLUSTERING:
        files = [f for f in (new_files or []) if f]
        if files:
            _append_photos_as_unclustered(files)
        with CLUSTER_STATE_LOCK:
            now = int(time.time())
            CLUSTER_STATE["running"] = False
            CLUSTER_STATE["last_started_unix"] = now
            CLUSTER_STATE["last_finished_unix"] = now
            CLUSTER_STATE["last_success"] = True
            CLUSTER_STATE["last_error"] = None
            CLUSTER_QUEUE.clear()
        return True

    with CLUSTER_STATE_LOCK:
        if new_files:
            CLUSTER_QUEUE.extend([f for f in new_files if f])
        if CLUSTER_STATE["running"]:
            return True
        CLUSTER_STATE["running"] = True
        CLUSTER_STATE["last_started_unix"] = int(time.time())
        CLUSTER_STATE["last_error"] = None
        batch = list(CLUSTER_QUEUE)
        CLUSTER_QUEUE.clear()
    worker = threading.Thread(target=_run_reclustering_worker, args=(batch,), daemon=True)
    worker.start()
    return True


@app.route("/api/search-school", methods=["GET"])
def api_search_school():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    
    matches = [s for s in _schools_with_custom() if query.lower() in s["name"].lower()]
    return jsonify(matches)


@app.route("/api/school/<school_name>/sports", methods=["GET"])
def api_list_sports(school_name):
    school = next((s for s in _schools_with_custom() if s["name"] == school_name), None)
    if not school:
        return jsonify({"error": "School not found"}), 404
    return jsonify(school["sports"])


@app.route("/api/schedule", methods=["GET"])
def api_view_schedule():
    school_name = request.args.get("school")
    sport = request.args.get("sport")
    if not school_name or not sport:
        return jsonify({"error": "Missing school or sport"}), 400
    
    if school_name == CLUSTER_SCHOOL and sport == CLUSTER_SPORT:
        schedule = [
            {
                "school": CLUSTER_SCHOOL,
                "sport": CLUSTER_SPORT,
                "date": "2026-04-12",
                "opponent": "Lincoln-Way East",
                "game_id": CLUSTER_GAME_ID,
            }
        ]
    else:
        schedule = view_schedule(school_name, sport)
    return jsonify(schedule)


def _photographer_info_for_photo(photo_name, manifest, profiles):
    """Get photographer, uploader_id, and photographer_image_url for a photo."""
    entry = next((p for p in manifest if p.get("filename") == photo_name), None)
    if not entry:
        return "Unknown Photographer", None, None
    photographer = entry.get("photographer", "Unknown Photographer")
    uploader_id = entry.get("uploader_id")
    image_url = None
    if uploader_id:
        prof = profiles.get(uploader_id, {})
        display_name = prof.get("display_name", "").strip()
        if display_name:
            photographer = display_name
        image_url = prof.get("image_url", "").strip() or None
    return photographer, uploader_id, image_url


@app.route("/api/game/<int:game_id>/clusters", methods=["GET"])
def api_game_clusters(game_id):
    data = _load_cluster_data()
    athletes = data.get("athletes", {})
    
    # Load manifest to get prices, photographer, uploader_id, and filter by game_id
    manifest = _load_uploads_manifest()
    profiles = _load_user_profiles()
    manifest_by_filename = {p.get("filename"): p for p in manifest if p.get("filename")}
    game_photo_names = {p.get("filename") for p in manifest if str(p.get("game_id", "")) == str(game_id)}
    price_map = {p.get("filename"): p.get("price", 5.0) for p in manifest}
    include_map = {}
    for p in manifest:
        include_flag = p.get("include_in_package")
        include_map[p.get("filename")] = True if include_flag is None else bool(include_flag)

    def photo_obj(photo_name):
        photographer, uploader_id, photographer_image_url = _photographer_info_for_photo(photo_name, manifest, profiles)
        entry = manifest_by_filename.get(photo_name, {})
        image_path, thumbnail_path = _resolve_photo_urls(photo_name, entry)
        return {
            "image_url": photo_name,
            "image_path": image_path,
            "thumbnail_path": thumbnail_path,
            "price": float(price_map.get(photo_name, 5.0)),
            "include_in_package": bool(include_map.get(photo_name, True)),
            "photographer": photographer,
            "uploader_id": uploader_id,
            "photographer_image_url": photographer_image_url,
        }

    clusters = []
    for cluster_id in sorted(athletes.keys()):
        detections = athletes[cluster_id]
        # Only include photos that belong to this game
        game_detections = [d for d in detections if d.get("photo") in game_photo_names]
        if not game_detections:
            continue
        unique_photos = sorted({det["photo"] for det in game_detections})
        clusters.append(
            {
                "cluster_id": cluster_id,
                "photo_count": len(unique_photos),
                "photos": [photo_obj(photo_name) for photo_name in unique_photos],
            }
        )

    # Expose uncertain detections as a dedicated unknown gallery (filtered by game)
    unclustered = data.get("unclustered", [])
    unknown_photos = sorted({det["photo"] for det in unclustered if det.get("photo") in game_photo_names})
    if unknown_photos:
        clusters.append(
            {
                "cluster_id": "unknown",
                "photo_count": len(unknown_photos),
                "photos": [photo_obj(photo_name) for photo_name in unknown_photos],
            }
        )
    return jsonify(clusters)


@app.route("/api/game/<int:game_id>/clusters/unknown", methods=["DELETE"])
@require_auth
def api_clear_unknown_cluster(game_id):
    data = _load_cluster_data()
    unclustered = data.get("unclustered", [])
    game_id_str = str(game_id)

    kept = []
    removed = 0
    for det in unclustered:
        photo_name = det.get("photo")
        if not photo_name:
            kept.append(det)
            continue
        meta = _manifest_lookup().get(photo_name, {})
        if str(meta.get("game_id", "")).strip() == game_id_str:
            removed += 1
            continue
        kept.append(det)

    data["unclustered"] = kept
    _write_cluster_data(data)
    return jsonify({"ok": True, "removed": removed, "game_id": game_id})


@app.route("/api/clustering/status", methods=["GET"])
def api_clustering_status():
    with CLUSTER_STATE_LOCK:
        state = dict(CLUSTER_STATE)
    return jsonify(state)


@app.route("/api/clusters/<cluster_id>", methods=["GET"])
def api_cluster_detail(cluster_id):
    game_id = request.args.get("game_id", type=int)
    data = _load_cluster_data()
    athletes = data.get("athletes", {})
    if cluster_id == "unknown":
        detections = data.get("unclustered", [])
    else:
        detections = athletes.get(cluster_id)
    if detections is None:
        return jsonify({"error": "Cluster not found"}), 404

    manifest = _load_uploads_manifest()
    manifest_by_filename = {p.get("filename"): p for p in manifest if p.get("filename")}
    profiles = _load_user_profiles()
    if game_id is not None:
        game_photo_names = {p.get("filename") for p in manifest if str(p.get("game_id", "")) == str(game_id)}
        detections = [d for d in detections if d.get("photo") in game_photo_names]
    price_map = {p.get("filename"): p.get("price", 5.0) for p in manifest}
    include_map = {}
    for p in manifest:
        include_flag = p.get("include_in_package")
        include_map[p.get("filename")] = True if include_flag is None else bool(include_flag)

    unique_photos = sorted({det["photo"] for det in detections})
    photos = []
    for photo_name in unique_photos:
        photographer, uploader_id, photographer_image_url = _photographer_info_for_photo(photo_name, manifest, profiles)
        entry = manifest_by_filename.get(photo_name, {})
        image_path, thumbnail_path = _resolve_photo_urls(photo_name, entry)
        photos.append({
            "image_url": photo_name,
            "image_path": image_path,
            "thumbnail_path": thumbnail_path,
            "price": float(price_map.get(photo_name, 5.0)),
            "include_in_package": bool(include_map.get(photo_name, True)),
            "photographer": photographer,
            "uploader_id": uploader_id,
            "photographer_image_url": photographer_image_url,
        })
    return jsonify(
        {
            "cluster_id": cluster_id,
            "photo_count": len(unique_photos),
            "photos": photos,
            "detections": detections,
        }
    )


def _generate_thumbnail(filename):
    """Generate a thumbnail for the given image if it doesn't exist."""
    from PIL import Image

    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMB_DIR / filename
    source_path = PHOTO_DIR / filename

    if thumb_path.exists():
        return thumb_path

    if not source_path.exists():
        return None

    try:
        with Image.open(source_path) as img:
            # Convert to RGB if necessary (handles PNG with alpha)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            # Save as JPEG for smaller size
            thumb_name = Path(filename).stem + ".jpg"
            thumb_path = THUMB_DIR / thumb_name
            img.save(thumb_path, "JPEG", quality=80, optimize=True)
            return thumb_path
    except Exception as e:
        print(f"Thumbnail generation failed for {filename}: {e}")
        return None


@app.route("/api/images/<path:filename>", methods=["GET"])
def api_image(filename):
    return send_from_directory(str(PHOTO_DIR), filename)


@app.route("/api/thumbnails/<path:filename>", methods=["GET"])
def api_thumbnail(filename):
    """Serve thumbnail images (auto-generated on demand)."""
    # Try exact filename first
    thumb_path = THUMB_DIR / filename
    if thumb_path.exists():
        return send_from_directory(str(THUMB_DIR), filename)

    # Try with .jpg extension (thumbnails are always JPEG)
    jpg_name = Path(filename).stem + ".jpg"
    thumb_path = THUMB_DIR / jpg_name
    if thumb_path.exists():
        return send_from_directory(str(THUMB_DIR), jpg_name)

    # Generate on demand
    generated = _generate_thumbnail(filename)
    if generated and generated.exists():
        return send_from_directory(str(THUMB_DIR), generated.name)

    # Fallback to original
    return send_from_directory(str(PHOTO_DIR), filename)


@app.route("/api/photographer/photos", methods=["GET"])
def api_photographer_photos():
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    photos = sorted(
        [p.name for p in PHOTO_DIR.iterdir() if p.suffix.lower() in ALLOWED_EXTENSIONS]
    )
    return jsonify(
        [
            {
                "image_url": name,
                "image_path": f"/api/images/{name}",
            }
            for name in photos
        ]
    )


@app.route("/api/uploads/presign", methods=["POST"])
@require_auth_or_anon_upload
def api_uploads_presign():
    """Create presigned S3 upload URLs for direct browser upload."""
    if not _uploads_in_cloud_enabled():
        return jsonify({"error": "Cloud uploads are not configured (S3_UPLOADS_BUCKET missing)."}), 503
    s3 = _s3_client()
    if s3 is None:
        return jsonify({"error": "AWS SDK unavailable. Install boto3 and configure credentials."}), 503

    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401

    data = request.get_json() or {}
    files = data.get("files") or []
    game_id = str(data.get("game_id") or CLUSTER_GAME_ID)
    if not isinstance(files, list) or not files:
        return jsonify({"error": "Missing files array"}), 400

    targets = []
    for raw in files[:200]:
        if not isinstance(raw, dict):
            continue
        filename = _safe_storage_name(raw.get("filename"))
        content_type = str(raw.get("content_type") or "application/octet-stream")
        key = _build_storage_key(user_id=user_id, game_id=game_id, filename=filename)
        try:
            upload_url = s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": S3_UPLOADS_BUCKET,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=900,
            )
        except Exception as e:
            return jsonify({"error": f"Failed to generate upload URL: {e}"}), 500

        targets.append({
            "filename": filename,
            "key": key,
            "content_type": content_type,
            "upload_url": upload_url,
            "public_url": _cloudfront_url_for_key(key),
        })

    if not targets:
        return jsonify({"error": "No valid files provided"}), 400

    return jsonify({
        "bucket": S3_UPLOADS_BUCKET,
        "region": AWS_REGION,
        "targets": targets,
    })


@app.route("/api/uploads/complete", methods=["POST"])
@require_auth_or_anon_upload
def api_uploads_complete():
    """Finalize direct uploads, append manifest entries, and enqueue cloud clustering."""
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401

    data = request.get_json() or {}
    uploads = data.get("uploads") or []
    if not isinstance(uploads, list) or not uploads:
        return jsonify({"error": "Missing uploads array"}), 400

    game_id = str(data.get("game_id") or CLUSTER_GAME_ID)
    school = (data.get("school") or CLUSTER_SCHOOL).strip() or CLUSTER_SCHOOL
    sport = (data.get("sport") or CLUSTER_SPORT).strip() or CLUSTER_SPORT
    photographer = (data.get("photographer") or "Unknown Photographer").strip() or "Unknown Photographer"
    include_in_package = bool(data.get("include_in_package", True))
    try:
        price_value = float(data.get("price", 5))
    except (TypeError, ValueError):
        price_value = 5.0
    if price_value <= 0:
        price_value = 5.0

    now_ts = int(time.time())
    manifest = _load_uploads_manifest()
    uploaded_files = []
    file_records = []

    for raw in uploads[:200]:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        filename = _safe_storage_name(raw.get("filename") or Path(key).name)
        if not key or not filename:
            continue
        uploaded_files.append(filename)
        file_records.append({"filename": filename, "key": key})
        manifest.append({
            "filename": filename,
            "storage_key": key,
            "price": float(price_value),
            "include_in_package": bool(include_in_package),
            "uploaded_at_unix": now_ts,
            "photographer": photographer,
            "school": school,
            "sport": sport,
            "game_id": str(game_id),
            "uploader_id": user_id,
            "storage": "s3",
        })

    if not uploaded_files:
        return jsonify({"error": "No valid uploads provided"}), 400

    _save_uploads_manifest(manifest)
    job = _new_cluster_job(
        user_id=user_id,
        game_id=game_id,
        files=file_records,
        metadata={
            "school": school,
            "sport": sport,
            "photographer": photographer,
        },
    )
    ok, queue_err = _enqueue_cluster_job(job)
    if ok:
        _set_cluster_job_status(job["id"], "queued", {"queue": "sqs"})
    else:
        # Fallback: local clustering worker for non-cloud/dev mode.
        _start_reclustering_async(uploaded_files)
        _set_cluster_job_status(job["id"], "running", {"queue": "local_thread", "queue_error": queue_err})

    return jsonify({
        "ok": True,
        "uploaded_count": len(uploaded_files),
        "uploaded_files": uploaded_files,
        "job_id": job["id"],
        "clustering_started": True,
    })


@app.route("/api/jobs/<job_id>", methods=["GET"])
@require_auth
def api_job_status(job_id):
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401
    jobs = _load_cluster_jobs()
    job = jobs.get(job_id)
    if not job or job.get("user_id") != user_id:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/internal/jobs/<job_id>", methods=["POST"])
def api_internal_job_update(job_id):
    """Internal worker callback to update clustering job status."""
    if not WORKER_SHARED_SECRET:
        return jsonify({"error": "WORKER_SHARED_SECRET not configured"}), 503
    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {WORKER_SHARED_SECRET}":
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    status = (data.get("status") or "").strip()
    if status not in {"queued", "running", "completed", "failed"}:
        return jsonify({"error": "Invalid status"}), 400
    jobs = _load_cluster_jobs()
    job = jobs.get(job_id, {})
    extra = {}
    if "error" in data:
        extra["error"] = str(data.get("error") or "")
    if "result" in data:
        extra["result"] = data.get("result")
    if status == "completed":
        ok, ingest_extra = _ingest_cloud_incremental_result(job, data.get("result"))
        if ok:
            extra.update(ingest_extra)
        else:
            status = "failed"
            extra.update(ingest_extra)
    _set_cluster_job_status(job_id, status, extra=extra)
    return jsonify({"ok": True})


@app.route("/api/photographer/upload", methods=["POST"])
@require_auth_or_anon_upload
def api_photographer_upload():
    photographer = request.form.get("photographer", "Unknown Photographer").strip()
    school = request.form.get("school", CLUSTER_SCHOOL).strip() or CLUSTER_SCHOOL
    sport = request.form.get("sport", CLUSTER_SPORT).strip() or CLUSTER_SPORT
    game_id = request.form.get("game_id", str(CLUSTER_GAME_ID)).strip() or str(CLUSTER_GAME_ID)
    price = request.form.get("price", "0")
    include_in_package = request.form.get("include_in_package", "true").lower() == "true"
    files = request.files.getlist("photos")
    uploader_id = getattr(request, "clerk_user_id", None)

    if not files:
        return jsonify({"error": "No files provided."}), 400

    uploaded, skipped = _save_uploaded_files(
        files=files,
        photographer=photographer,
        school=school,
        sport=sport,
        game_id=game_id,
        price=price,
        include_in_package=include_in_package,
        uploader_id=uploader_id,
    )
    if not uploaded:
        return jsonify({
            "error": "No supported image files were uploaded. Allowed extensions: .jpg, .jpeg, .png, .webp",
            "uploaded_count": 0,
            "skipped_count": skipped,
        }), 400

    clustering_started = _start_reclustering_async(uploaded)

    return jsonify(
        {
            "uploaded_count": len(uploaded),
            "skipped_count": skipped,
            "uploaded_files": uploaded,
            "school": school,
            "sport": sport,
            "game_id": game_id,
            "clustering_started": clustering_started,
        }
    )


@app.route("/api/game/<int:game_id>/upload", methods=["POST"])
@require_auth_or_anon_upload
def api_game_upload(game_id):
    files = request.files.getlist("photos")
    if not files:
        return jsonify({"error": "No files provided."}), 400

    photographer = request.form.get("photographer", "Unknown Photographer").strip() or "Unknown Photographer"
    school = request.form.get("school", CLUSTER_SCHOOL).strip() or CLUSTER_SCHOOL
    sport = request.form.get("sport", CLUSTER_SPORT).strip() or CLUSTER_SPORT
    price = request.form.get("price", "0")
    include_in_package = request.form.get("include_in_package", "true").lower() == "true"
    uploader_id = getattr(request, "clerk_user_id", None)

    uploaded, skipped = _save_uploaded_files(
        files=files,
        photographer=photographer,
        school=school,
        sport=sport,
        game_id=str(game_id),
        price=price,
        include_in_package=include_in_package,
        uploader_id=uploader_id,
    )
    if not uploaded:
        return jsonify({
            "error": "No supported image files were uploaded. Allowed extensions: .jpg, .jpeg, .png, .webp",
            "uploaded_count": 0,
            "skipped_count": skipped,
        }), 400

    clustering_started = _start_reclustering_async(uploaded)
    clusters = api_game_clusters(game_id).get_json()

    return jsonify(
        {
            "uploaded_count": len(uploaded),
            "skipped_count": skipped,
            "uploaded_files": uploaded,
            "game_id": game_id,
            "clustering_started": clustering_started,
            "clusters": clusters,
        }
    )


@app.route("/api/claim-uploader", methods=["POST"])
@require_auth_or_anon_upload
def api_claim_uploader():
    """Set the current user as uploader_id and photographer (display name) for manifest entries.
    Updates 'Game Upload' entries and syncs photographer for entries already owned by this user.
    Also syncs display_name and image_url to user_profiles for signed-out viewing."""
    uploader_id = getattr(request, "clerk_user_id", None)
    if not uploader_id:
        return jsonify({"error": "Not signed in"}), 401
    data = request.get_json() or {}
    display_name = (data.get("display_name") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    notification_email = (data.get("notification_email") or "").strip()
    manifest = _load_uploads_manifest()
    updated = 0
    for entry in manifest:
        if entry.get("photographer") == "Game Upload" and entry.get("uploader_id") != uploader_id:
            entry["uploader_id"] = uploader_id
            if display_name:
                entry["photographer"] = display_name
            updated += 1
        elif entry.get("uploader_id") == uploader_id and display_name and entry.get("photographer") != display_name:
            entry["photographer"] = display_name
            updated += 1
    if updated:
        _save_uploads_manifest(manifest)
    # Sync to user_profiles so photographer name/image show correctly when signed out, and for purchase notifications
    if display_name or image_url or notification_email:
        profiles = _load_user_profiles()
        profiles.setdefault(uploader_id, {})
        if display_name:
            profiles[uploader_id]["display_name"] = display_name
        if image_url:
            profiles[uploader_id]["image_url"] = image_url
        if notification_email:
            profiles[uploader_id]["notification_email"] = notification_email
        _save_user_profiles(profiles)
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/create-checkout-session", methods=["POST"])
def create_checkout_session():
    """Create a Stripe Checkout session for purchasing photos (single or multiple)."""
    try:
        data = request.get_json() or {}
        
        # Support both single photo (legacy) and multiple items (cart)
        items = data.get("items", [])
        
        # Legacy single-photo support
        if not items and data.get("photo_name"):
            items = [{
                "photo_name": data.get("photo_name", ""),
                "price_cents": data.get("price_cents", 500),
            }]
        
        if not items:
            return jsonify({"error": "No items provided"}), 400

        quote = _build_checkout_quote(items)
        line_items = quote["stripe_line_items"]
        photo_names = quote["photo_names"]

        if not line_items or not photo_names:
            return jsonify({"error": "No valid items provided"}), 400

        # Get the origin for success/cancel URLs
        origin = request.headers.get("Origin", "http://localhost:5173")
        customer_email = (data.get("customer_email") or "").strip() or None
        clerk_user_id = (data.get("clerk_user_id") or "").strip() or None

        metadata = {
            "photo_names": ",".join(photo_names),
            "photo_count": str(len(photo_names)),
            "subtotal_cents": str(quote["subtotal_cents"]),
            "savings_cents": str(quote["savings_cents"]),
            "charged_cents": str(quote["total_cents"]),
        }
        if customer_email:
            metadata["customer_email"] = customer_email
        if clerk_user_id:
            metadata["clerk_user_id"] = clerk_user_id

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{origin}?purchase=success&photos={','.join(photo_names)}",
            cancel_url=f"{origin}?purchase=cancelled",
            metadata=metadata,
            customer_email=customer_email if customer_email else None,
        )

        return jsonify({
            "checkout_url": checkout_session.url,
            "subtotal_cents": quote["subtotal_cents"],
            "savings_cents": quote["savings_cents"],
            "total_cents": quote["total_cents"],
        })

    except stripe.error.StripeError as e:
        print(f"Stripe error: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"Checkout error: {e}")
        return jsonify({"error": "Failed to create checkout session"}), 500


@app.route("/api/profile/<user_id>", methods=["GET"])
def api_get_profile(user_id):
    """Get a user's profile (instagram, display_name, image_url). Public."""
    profiles = _load_user_profiles()
    profile = profiles.get(user_id, {})
    return jsonify({
        "instagram": profile.get("instagram", ""),
        "display_name": profile.get("display_name", ""),
        "image_url": profile.get("image_url", ""),
    })


@app.route("/api/photographer/packages", methods=["GET"])
def api_get_photographer_packages():
    """Get package deals for a photographer. Public."""
    user_id = (request.args.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    deals = _package_deals_for_uploader(user_id)
    return jsonify({
        "user_id": user_id,
        "deals": [
            {
                "quantity": int(d["quantity"]),
                "package_price_cents": int(d["package_price_cents"]),
                "package_price_dollars": round(int(d["package_price_cents"]) / 100, 2),
            }
            for d in deals
        ],
    })


@app.route("/api/photographer/packages", methods=["POST"])
@require_auth
def api_set_photographer_packages():
    """Set package deals for the signed-in photographer."""
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    deals = _normalize_package_deals(data.get("deals", []))
    packages = _load_packages()
    packages[user_id] = {
        "deals": deals,
        "updated_at_unix": int(time.time()),
    }
    _save_packages(packages)
    return jsonify({"ok": True, "user_id": user_id, "deals": deals})


@app.route("/api/package-quote", methods=["POST"])
def api_package_quote():
    """Return package pricing preview for a prospective cart."""
    data = request.get_json() or {}
    items = data.get("items", [])
    quote = _build_checkout_quote(items)
    return jsonify({
        "photo_count": len(quote["photo_names"]),
        "subtotal_cents": quote["subtotal_cents"],
        "total_cents": quote["total_cents"],
        "savings_cents": quote["savings_cents"],
        "applied_packages": quote["applied_packages"],
        "available_packages": quote["available_packages"],
    })


@app.route("/api/cart", methods=["GET"])
@require_auth
def api_get_cart():
    """Get cart for the signed-in user."""
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401
    carts = _load_carts()
    items = _normalize_cart_items(carts.get(user_id, []))
    return jsonify({"items": items})


@app.route("/api/cart", methods=["PUT"])
@require_auth
def api_put_cart():
    """Replace cart for the signed-in user. Body: { items: [...] }."""
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401
    data = request.get_json() or {}
    items = _normalize_cart_items(data.get("items", []))
    carts = _load_carts()
    carts[user_id] = items
    _save_carts(carts)
    return jsonify({"ok": True, "items": items})


@app.route("/api/cart", methods=["DELETE"])
@require_auth
def api_delete_cart():
    """Clear cart for the signed-in user."""
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401
    carts = _load_carts()
    carts[user_id] = []
    _save_carts(carts)
    return jsonify({"ok": True, "items": []})


@app.route("/api/profile", methods=["POST"])
@require_auth
def api_update_profile():
    """Update the current user's profile (instagram, display_name, image_url)."""
    profiles = _load_user_profiles()
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401
    data = request.get_json() or {}
    instagram = (data.get("instagram") or "").strip()
    display_name = (data.get("display_name") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    notification_email = (data.get("notification_email") or "").strip()
    profiles.setdefault(user_id, {})
    profiles[user_id]["instagram"] = instagram
    profiles[user_id]["display_name"] = display_name
    if image_url:
        profiles[user_id]["image_url"] = image_url
    if notification_email:
        profiles[user_id]["notification_email"] = notification_email
    _save_user_profiles(profiles)
    return jsonify({"ok": True, "instagram": instagram, "display_name": display_name, "image_url": image_url})


# --- Instagram OAuth (Business Login) ---

def _instagram_state_encode(clerk_user_id, return_url):
    """Create signed state for Instagram OAuth (CSRF protection)."""
    payload = {"cid": clerk_user_id, "return": return_url or FRONTEND_ORIGIN, "ts": int(time.time())}
    return jwt.encode(payload, app.secret_key, algorithm="HS256")


def _instagram_state_decode(state):
    """Decode and validate state. Returns (clerk_user_id, return_url) or (None, None)."""
    try:
        payload = jwt.decode(state, app.secret_key, algorithms=["HS256"])
        if time.time() - payload.get("ts", 0) > 600:  # 10 min expiry
            return None, None
        return payload.get("cid"), payload.get("return", FRONTEND_ORIGIN)
    except Exception:
        return None, None


@app.route("/api/instagram/connect", methods=["POST"])
@require_auth
def api_instagram_connect():
    """Start Instagram OAuth flow. Returns redirect URL for frontend to navigate to."""
    if not INSTAGRAM_APP_ID or not INSTAGRAM_APP_SECRET or not INSTAGRAM_REDIRECT_URI:
        return jsonify({"error": "Instagram OAuth not configured"}), 503
    clerk_user_id = getattr(request, "clerk_user_id", None)
    if not clerk_user_id:
        return jsonify({"error": "Not signed in"}), 401
    data = request.get_json() or {}
    return_url = (data.get("return_url") or request.headers.get("Origin") or FRONTEND_ORIGIN).rstrip("/")
    state = _instagram_state_encode(clerk_user_id, return_url)
    auth_url = (
        "https://www.instagram.com/oauth/authorize"
        f"?client_id={INSTAGRAM_APP_ID}"
        f"&redirect_uri={requests.utils.quote(INSTAGRAM_REDIRECT_URI)}"
        "&response_type=code"
        "&scope=instagram_business_basic"
        f"&state={state}"
    )
    return jsonify({"redirect_url": auth_url})


@app.route("/api/instagram/callback", methods=["GET"])
def api_instagram_callback():
    """Handle Instagram OAuth callback. Exchange code for token, get username, save to profile, redirect to frontend."""
    if not INSTAGRAM_APP_ID or not INSTAGRAM_APP_SECRET or not INSTAGRAM_REDIRECT_URI:
        return jsonify({"error": "Instagram OAuth not configured"}), 503
    code = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()
    error = request.args.get("error")
    if error:
        return_url = FRONTEND_ORIGIN
        _, decoded_return = _instagram_state_decode(state)
        if decoded_return:
            return_url = decoded_return
        return redirect(f"{return_url}?instagram=denied")
    if not code or not state:
        return redirect(f"{FRONTEND_ORIGIN}?instagram=error")
    clerk_user_id, return_url = _instagram_state_decode(state)
    if not clerk_user_id:
        return redirect(f"{FRONTEND_ORIGIN}?instagram=error")
    # Exchange code for short-lived token
    try:
        resp = requests.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": INSTAGRAM_APP_ID,
                "client_secret": INSTAGRAM_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": INSTAGRAM_REDIRECT_URI,
                "code": code.replace("#_", "").strip(),  # strip Instagram's #_ suffix if present
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # Response can be { access_token, user_id } or { data: [{ access_token, user_id }] }
        if "data" in data and data["data"]:
            token_data = data["data"][0]
        else:
            token_data = data
        access_token = token_data.get("access_token")
        if not access_token:
            return redirect(f"{return_url}?instagram=error")
        # Exchange for long-lived token (optional but recommended)
        long_resp = requests.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": INSTAGRAM_APP_SECRET,
                "access_token": access_token,
            },
            timeout=10,
        )
        if long_resp.ok:
            long_data = long_resp.json()
            access_token = long_data.get("access_token", access_token)
        # Get username from /me
        me_resp = requests.get(
            "https://graph.instagram.com/me",
            params={"fields": "username", "access_token": access_token},
            timeout=10,
        )
        if not me_resp.ok:
            return redirect(f"{return_url}?instagram=error")
        me_data = me_resp.json()
        username = (me_data.get("username") or "").strip()
        if not username:
            return redirect(f"{return_url}?instagram=error")
        # Save to user_profiles
        profiles = _load_user_profiles()
        profiles.setdefault(clerk_user_id, {})
        profiles[clerk_user_id]["instagram"] = username
        _save_user_profiles(profiles)
        return redirect(f"{return_url}?instagram=linked&username={requests.utils.quote(username)}")
    except requests.RequestException as e:
        print(f"Instagram OAuth error: {e}")
        return redirect(f"{return_url}?instagram=error")


@app.route("/api/notifications", methods=["GET"])
@require_auth
def api_get_notifications():
    """Get notifications for the current user."""
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401
    notifications = _load_notifications()
    mine = [n for n in notifications if n.get("user_id") == user_id]
    mine.sort(key=lambda n: n.get("created", ""), reverse=True)
    unread_count = sum(1 for n in mine if not n.get("read"))
    return jsonify({"notifications": mine, "unread_count": unread_count})


@app.route("/api/notifications/mark-read", methods=["POST"])
@require_auth
def api_mark_notifications_read():
    """Mark notifications as read. Body: { ids: [...] } or empty to mark all."""
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Not signed in"}), 401
    data = request.get_json() or {}
    ids = data.get("ids") or []
    notifications = _load_notifications()
    for n in notifications:
        if n.get("user_id") != user_id:
            continue
        if not ids or n.get("id") in ids:
            n["read"] = True
    _save_notifications(notifications)
    return jsonify({"ok": True})


def _stats_for_photographer(user_id):
    """Compute sales stats for a photographer (uploader_id)."""
    manifest = _load_uploads_manifest()
    photographer_photos = {p.get("filename") for p in manifest if p.get("uploader_id") == user_id}
    photos_uploaded = len(photographer_photos)
    total_cents = 0
    purchase_count = 0
    purchases = _load_purchases()
    for p in purchases:
        photo_names = p.get("photo_names") or []
        amount_cents = int(p.get("amount_cents", 0))
        if not photo_names or amount_cents <= 0:
            continue
        my_photos = [n for n in photo_names if n in photographer_photos]
        if not my_photos:
            continue
        purchase_count += 1
        total_cents += round(amount_cents * len(my_photos) / len(photo_names))
    return {
        "total_sales_cents": total_cents,
        "total_sales_dollars": round(total_cents / 100.0, 2),
        "purchase_count": purchase_count,
        "photos_uploaded": photos_uploaded,
    }


@app.route("/api/stats/sales", methods=["GET"])
def api_stats_sales():
    """Return total sales and upload counts. If user_id query param provided, return that photographer's stats."""
    user_id = request.args.get("user_id", "").strip()
    if user_id:
        stats = _stats_for_photographer(user_id)
        return jsonify(stats)
    purchases = _load_purchases()
    total_cents = sum(int(p.get("amount_cents", 0)) for p in purchases)
    manifest = _load_uploads_manifest()
    return jsonify({
        "total_sales_cents": total_cents,
        "total_sales_dollars": round(total_cents / 100.0, 2),
        "purchase_count": len(purchases),
        "photos_uploaded": len(manifest),
    })


@app.route("/api/stats/sales/<user_id>", methods=["GET"])
def api_stats_sales_for_user(user_id):
    """Return sales stats for a specific photographer. Public."""
    stats = _stats_for_photographer(user_id)
    return jsonify(stats)


@app.route("/api/photo/<photo_name>/price", methods=["GET"])
def get_photo_price(photo_name):
    """Get the price for a specific photo."""
    manifest = _load_uploads_manifest()
    photo_entry = next((p for p in manifest if p.get("filename") == photo_name), None)
    
    if photo_entry and photo_entry.get("price"):
        return jsonify({"price": float(photo_entry["price"])})
    
    # Default price
    return jsonify({"price": 5.00})


@app.route("/api/stripe-webhook", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhooks: record purchase and send receipt email with attachments."""
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook secret not configured"}), 500
    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        print(f"Webhook payload invalid: {e}")
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError as e:
        print(f"Webhook signature invalid: {e}")
        return jsonify({"error": "Invalid signature"}), 400

    if event["type"] != "checkout.session.completed":
        return jsonify({"ok": True})

    session = event["data"]["object"]
    session_id = session.get("id", "")
    metadata = session.get("metadata") or {}
    photo_names_str = metadata.get("photo_names", "")
    photo_names = [n.strip() for n in photo_names_str.split(",") if n.strip()]
    # Prefer Stripe-collected email, then our metadata (for signed-in user)
    email = (session.get("customer_email") or "").strip() or metadata.get("customer_email", "").strip()
    clerk_user_id = metadata.get("clerk_user_id", "").strip() or None
    amount_total = session.get("amount_total") or 0

    if not photo_names:
        return jsonify({"ok": True})

    purchase = {
        "id": session_id,
        "email": email,
        "clerk_user_id": clerk_user_id,
        "photo_names": photo_names,
        "amount_cents": amount_total,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    purchases = _load_purchases()
    purchases.append(purchase)
    _save_purchases(purchases)

    if email:
        _send_purchase_email(to_email=email, photo_names=photo_names, amount_cents=amount_total, stripe_session_id=session_id)
    else:
        print("No email for purchase, receipt not sent")

    # Notify photographers whose photos were purchased
    manifest = _load_uploads_manifest()
    filename_to_uploader = {p.get("filename"): p.get("uploader_id") for p in manifest if p.get("uploader_id")}
    photographer_photos = {}  # uploader_id -> [photo_names]
    for photo_name in photo_names:
        uploader_id = filename_to_uploader.get(photo_name)
        if not uploader_id:
            continue
        photographer_photos.setdefault(uploader_id, []).append(photo_name)
    notifications = _load_notifications()
    for uid, photos in photographer_photos.items():
        share_cents = round(amount_total * len(photos) / len(photo_names))
        # Email notification
        photographer_email = _get_photographer_email(uid)
        if photographer_email:
            _send_photographer_purchase_notification(
                to_email=photographer_email,
                photo_names=photos,
                amount_dollars=share_cents / 100.0,
            )
        # In-app notification
        notifications.append({
            "id": f"purchase-{session_id}-{uid}",
            "user_id": uid,
            "type": "purchase",
            "photo_names": photos,
            "amount_cents": share_cents,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "read": False,
        })
    if photographer_photos:
        _save_notifications(notifications)

    return jsonify({"ok": True})


@app.route("/api/photo/<path:photo_name>/download", methods=["GET"])
@require_auth
def download_purchased_photo(photo_name):
    """Serve purchased photo as download for the owning user."""
    purchases = _load_purchases()
    user_id = getattr(request, "clerk_user_id", None)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    for p in purchases:
        if p.get("clerk_user_id") == user_id and photo_name in (p.get("photo_names") or []):
            path = PHOTO_DIR / photo_name
            if not path.exists():
                return jsonify({"error": "Photo file not found"}), 404
            return send_file(
                path,
                as_attachment=True,
                download_name=photo_name,
                mimetype="application/octet-stream",
            )
    return jsonify({"error": "You do not have access to this photo"}), 403


@app.route("/config.js", methods=["GET"])
def frontend_runtime_config():
    config = {
        "clerkPublishableKey": CLERK_PUBLISHABLE_KEY,
        "enableCloudUploads": ENABLE_CLOUD_UPLOADS,
    }
    payload = "window.__APP_CONFIG__ = " + json.dumps(config) + ";"
    return app.response_class(payload, mimetype="application/javascript")


@app.route("/", defaults={"path": ""}, methods=["GET"])
@app.route("/<path:path>", methods=["GET"])
def serve_frontend(path):
    """Serve built React app for non-API routes."""
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404

    if path:
        requested = FRONTEND_DIST_DIR / path
        if requested.exists() and requested.is_file():
            return send_from_directory(FRONTEND_DIST_DIR, path)

    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return send_from_directory(FRONTEND_DIST_DIR, "index.html")

    return jsonify({"status": "ok", "service": "sports-photography-api"}), 200


if __name__ == "__main__":
    # Run on port 8080 to avoid conflict with React (usually 3000/5173) or default Flask (5000)
    app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=False)
