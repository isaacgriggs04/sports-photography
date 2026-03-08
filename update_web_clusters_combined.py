import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import hdbscan
import numpy as np
import torch
from PIL import Image
from ultralytics import YOLO

from face_body_cluster_pipeline import (
    _build_body_model,
    _build_face_app,
    _ensure_bgr_uint8,
    detect_people,
    extract_body_embedding,
    load_image_bgr,
)
from debug_embedding_clustering_v2 import (
    adaptive_weights,
    body_quality_ok,
    extract_face_embedding_with_quality,
    normalize,
    pick_largest_detection,
)


def parse_args():
    p = argparse.ArgumentParser(description="Update athlete_groups.json using combined face+body embeddings.")
    p.add_argument("--images-dir", default="game_photos")
    p.add_argument("--yolo-model", default="yolo11m.pt")
    p.add_argument("--min-cluster-size", type=int, default=2)
    p.add_argument("--min-samples", type=int, default=1)
    p.add_argument("--merge-combined-cos", type=float, default=0.50)
    p.add_argument("--merge-face-cos", type=float, default=0.45)
    p.add_argument("--assign-combined-cos", type=float, default=0.57)
    p.add_argument("--assign-face-cos", type=float, default=0.62)
    p.add_argument("--assign-margin", type=float, default=0.02)
    p.add_argument("--assign-no-face-combined-cos", type=float, default=0.62)
    p.add_argument("--conf-threshold", type=float, default=0.25)
    p.add_argument("--output-json", default="athlete_groups.json")
    p.add_argument("--output-crops-dir", default="athlete_groups")
    p.add_argument("--min-w", type=int, default=80)
    p.add_argument("--min-h", type=int, default=140)
    p.add_argument("--min-lap-var", type=float, default=20.0)
    return p.parse_args()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) + 1e-12) * (np.linalg.norm(b) + 1e-12))
    )


def _safe_merge_labels(
    labels: np.ndarray,
    combined_embeddings: np.ndarray,
    face_embeddings: list,
    combined_cos_threshold: float,
    face_cos_threshold: float,
) -> np.ndarray:
    """
    Merge micro-clusters conservatively:
    - high combined-centroid cosine required
    - if both clusters have face centroids, require face-centroid cosine too
    """
    unique = sorted([int(l) for l in set(labels.tolist()) if l != -1])
    if len(unique) <= 1:
        return labels

    members = {cid: np.where(labels == cid)[0].tolist() for cid in unique}
    comb_centroids = {}
    face_centroids = {}
    for cid in unique:
        idxs = members[cid]
        c = normalize(np.mean(combined_embeddings[idxs], axis=0))
        comb_centroids[cid] = c
        face_vecs = [face_embeddings[i] for i in idxs if face_embeddings[i] is not None]
        if face_vecs:
            face_centroids[cid] = normalize(np.mean(np.vstack(face_vecs), axis=0))
        else:
            face_centroids[cid] = None

    parent = {cid: cid for cid in unique}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    pairs = []
    for i, a in enumerate(unique):
        for b in unique[i + 1 :]:
            sim = _cosine(comb_centroids[a], comb_centroids[b])
            pairs.append((sim, a, b))
    pairs.sort(reverse=True, key=lambda x: x[0])

    for sim, a, b in pairs:
        if sim < combined_cos_threshold:
            continue

        if max(len(members[a]), len(members[b])) < 2:
            continue

        fa = face_centroids[a]
        fb = face_centroids[b]
        if fa is not None and fb is not None:
            face_sim = _cosine(fa, fb)
            if face_sim < face_cos_threshold:
                continue

        union(a, b)

    root_to_new = {}
    next_label = 0
    merged = labels.copy()
    for cid in unique:
        root = find(cid)
        if root not in root_to_new:
            root_to_new[root] = next_label
            next_label += 1
        new_id = root_to_new[root]
        merged[labels == cid] = new_id
    return merged


def _prototype_reassign_labels(
    labels: np.ndarray,
    combined_embeddings: np.ndarray,
    face_embeddings: list,
    assign_combined_cos: float,
    assign_face_cos: float,
    assign_margin: float,
    assign_no_face_combined_cos: float,
) -> np.ndarray:
    """
    Reassign only currently-unclustered detections to existing clusters if:
    - nearest combined centroid similarity is high enough
    - margin over second-best cluster is high enough
    - if face exists for item and centroid has face, face similarity is high enough
    """
    cluster_ids = sorted([int(l) for l in set(labels.tolist()) if l != -1])
    if not cluster_ids:
        return labels

    members = {cid: np.where(labels == cid)[0].tolist() for cid in cluster_ids}
    comb_centroids = {}
    face_centroids = {}
    for cid in cluster_ids:
        idxs = members[cid]
        comb_centroids[cid] = normalize(np.mean(combined_embeddings[idxs], axis=0))
        face_vecs = [face_embeddings[i] for i in idxs if face_embeddings[i] is not None]
        if face_vecs:
            face_centroids[cid] = normalize(np.mean(np.vstack(face_vecs), axis=0))
        else:
            face_centroids[cid] = None

    out = labels.copy()
    unclustered = np.where(labels == -1)[0].tolist()
    for i in unclustered:
        sims = []
        for cid in cluster_ids:
            sim = _cosine(combined_embeddings[i], comb_centroids[cid])
            sims.append((sim, cid))
        sims.sort(reverse=True, key=lambda x: x[0])
        best_sim, best_cid = sims[0]
        second_sim = sims[1][0] if len(sims) > 1 else -1.0

        min_combined = assign_combined_cos
        if face_embeddings[i] is None:
            min_combined = assign_no_face_combined_cos

        if best_sim < min_combined:
            continue
        if (best_sim - second_sim) < assign_margin:
            continue

        item_face = face_embeddings[i]
        centroid_face = face_centroids.get(best_cid)
        if item_face is not None and centroid_face is not None:
            face_sim = _cosine(item_face, centroid_face)
            if face_sim < assign_face_cos:
                continue

        out[i] = best_cid

    return out


def main():
    args = parse_args()
    images_dir = Path(args.images_dir)
    image_paths = sorted(
        [p for p in images_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    )
    sample_paths = [str(p) for p in image_paths[:5]]
    print(
        f"[CLUSTER INPUT] pid={os.getpid()} DATA_DIR={os.getenv('DATA_DIR')} "
        f"images_dir={images_dir} count={len(image_paths)} sample={sample_paths}",
        flush=True,
    )
    print(
        f"[RUNTIME DIAG] numpy={np.__version__} np_file={getattr(np, '__file__', None)} "
        f"np_id={id(np)} cv2={cv2.__version__} py={sys.version.split()[0]}",
        flush=True,
    )
    if not image_paths:
        raise ValueError(f"No images found in {images_dir}")

    yolo = YOLO(args.yolo_model)
    face_app = _build_face_app()
    face_enabled = face_app is not None
    if not face_enabled:
        args.merge_combined_cos = max(args.merge_combined_cos, 0.62)
        args.assign_combined_cos = max(args.assign_combined_cos, 0.72)
        args.assign_no_face_combined_cos = max(args.assign_no_face_combined_cos, 0.74)
        print(
            "[CLUSTER MODE] Face model unavailable; using stricter body-only thresholds: "
            f"merge_combined_cos={args.merge_combined_cos} "
            f"assign_combined_cos={args.assign_combined_cos} "
            f"assign_no_face_combined_cos={args.assign_no_face_combined_cos}",
            flush=True,
        )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    body_model, body_preprocess = _build_body_model(device)

    records = []
    embeddings = []
    face_embeddings = []
    crops = []
    face_detection_count = 0

    print(f"Processing {len(image_paths)} images with combined face+body embeddings...")
    for idx, image_path in enumerate(image_paths, start=1):
        print(f"[{idx}/{len(image_paths)}] {image_path.name}")
        image = load_image_bgr(image_path)
        if image is None:
            continue

        detections = detect_people(
            image_path=image_path,
            image_bgr=image,
            yolo_model=yolo,
            conf_threshold=args.conf_threshold,
        )
        if not detections:
            continue

        # Use one representative athlete crop per image for stable gallery grouping.
        det = pick_largest_detection(detections)
        crop = det.get("body_crop_bgr")
        try:
            crop = _ensure_bgr_uint8(crop)
            if crop is None:
                print(
                    f"Skipping {image_path.name}: invalid crop shape={getattr(crop, 'shape', None)}",
                    flush=True,
                )
                continue
        except Exception as exc:
            print(f"Skipping {image_path.name}: crop normalization failed: {exc}", flush=True)
            continue

        ok, _reason = body_quality_ok(crop, args.min_w, args.min_h, args.min_lap_var)
        if not ok:
            continue

        body_raw = extract_body_embedding(crop, body_model, body_preprocess, device)
        body_n = normalize(body_raw)
        face_raw, face_score, face_ratio = extract_face_embedding_with_quality(crop, face_app)

        if face_raw is not None:
            face_n = normalize(face_raw)
            fw, bw = adaptive_weights(face_score, face_ratio)
            emb = normalize(np.concatenate([fw * face_n, bw * body_n], axis=0))
            face_detection_count += 1
        else:
            face_n = None
            zero_face = np.zeros_like(body_n, dtype=np.float32)
            emb = normalize(np.concatenate([zero_face, body_n], axis=0))

        records.append(
            {
                "photo": image_path.name,
                "bbox_xyxy": [float(v) for v in det["bbox_xyxy"]],
                "confidence": float(det["confidence"]),
            }
        )
        embeddings.append(emb)
        face_embeddings.append(face_n)
        crops.append(crop)

    if not embeddings:
        output = {
            "athletes": {},
            "unclustered": [],
            "stats": {"images": len(image_paths), "detections": 0, "clustered_detections": 0, "clusters": 0},
        }
        Path(args.output_json).write_text(json.dumps(output, indent=2))
        print(f"No usable detections. Wrote empty {args.output_json}")
        return

    X = np.vstack(embeddings)
    initial_labels = hdbscan.HDBSCAN(
        min_cluster_size=args.min_cluster_size,
        min_samples=args.min_samples,
        metric="euclidean",
    ).fit_predict(X)
    labels = _safe_merge_labels(
        labels=initial_labels,
        combined_embeddings=X,
        face_embeddings=face_embeddings,
        combined_cos_threshold=args.merge_combined_cos,
        face_cos_threshold=args.merge_face_cos,
    )
    labels = _prototype_reassign_labels(
        labels=labels,
        combined_embeddings=X,
        face_embeddings=face_embeddings,
        assign_combined_cos=args.assign_combined_cos,
        assign_face_cos=args.assign_face_cos,
        assign_margin=args.assign_margin,
        assign_no_face_combined_cos=args.assign_no_face_combined_cos,
    )

    cluster_ids = sorted([int(l) for l in set(labels.tolist()) if l != -1])
    id_map = {cid: f"athlete_{i+1}" for i, cid in enumerate(cluster_ids)}

    output = {
        "athletes": {},
        "unclustered": [],
        "stats": {
            "images": len(image_paths),
            "detections": len(records),
            "clustered_detections": int(np.sum(labels != -1)),
            "clusters": len(cluster_ids),
            "face_enabled": face_enabled,
            "face_detections": face_detection_count,
            "method": "combined_face_body_adaptive",
            "clusterer": "hdbscan",
            "metric": "euclidean",
            "min_cluster_size": args.min_cluster_size,
            "min_samples": args.min_samples,
            "initial_clusters": len([int(l) for l in set(initial_labels.tolist()) if l != -1]),
            "merge_combined_cos": args.merge_combined_cos,
            "merge_face_cos": args.merge_face_cos,
            "assign_combined_cos": args.assign_combined_cos,
            "assign_face_cos": args.assign_face_cos,
            "assign_margin": args.assign_margin,
            "assign_no_face_combined_cos": args.assign_no_face_combined_cos,
        },
    }

    crops_root = Path(args.output_crops_dir)
    if crops_root.exists():
        for p in crops_root.iterdir():
            if p.is_dir():
                for f in p.glob("*"):
                    f.unlink(missing_ok=True)
                p.rmdir()
    crops_root.mkdir(parents=True, exist_ok=True)

    for i, label in enumerate(labels.tolist()):
        rec = records[i]
        if label == -1:
            output["unclustered"].append(rec)
            continue

        athlete_name = id_map[int(label)]
        output["athletes"].setdefault(athlete_name, []).append(rec)

        athlete_dir = crops_root / athlete_name
        athlete_dir.mkdir(parents=True, exist_ok=True)
        crop_name = f"{Path(rec['photo']).stem}_det{i+1}.jpg"
        try:
            crop_img = _ensure_bgr_uint8(crops[i])
            if crop_img is None:
                raise ValueError(f"invalid crop shape={getattr(crop_img, 'shape', None)}")
            crop_rgb = crop_img[:, :, ::-1].copy()
            Image.fromarray(crop_rgb).save(athlete_dir / crop_name, format="JPEG", quality=95)
        except Exception as exc:
            # Crop artifact write failures should not fail clustering output generation.
            print(f"WARN: failed to write crop for {rec['photo']} index {i}: {exc}", flush=True)

    Path(args.output_json).write_text(json.dumps(output, indent=2))
    print("Updated web cluster artifacts.")
    print(f"Output JSON: {args.output_json}")
    print(f"Cluster folders: {args.output_crops_dir}")
    print(f"Stats: {output['stats']}")


if __name__ == "__main__":
    main()
