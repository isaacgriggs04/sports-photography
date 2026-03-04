import argparse
import json
import os
import re
import sys
from pathlib import Path

import cv2
import hdbscan
import numpy as np
import torch
from ultralytics import YOLO

from face_body_cluster_pipeline import (
    _build_body_model,
    _build_face_app,
    _opencv_safe_array,
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
    p.add_argument("--yolo-model", default="yolo11n.pt")
    p.add_argument("--min-cluster-size", type=int, default=2)
    p.add_argument("--min-samples", type=int, default=1)
    p.add_argument("--merge-combined-cos", type=float, default=0.50)
    p.add_argument("--merge-face-cos", type=float, default=0.45)
    p.add_argument("--assign-combined-cos", type=float, default=0.57)
    p.add_argument("--assign-face-cos", type=float, default=0.62)
    p.add_argument("--assign-margin", type=float, default=0.02)
    p.add_argument("--assign-no-face-combined-cos", type=float, default=0.62)
    p.add_argument("--number-conf-thres", type=float, default=0.7)
    p.add_argument("--number-match-bonus", type=float, default=0.03)
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
    jersey_numbers: list,
    jersey_confs: list,
    combined_cos_threshold: float,
    face_cos_threshold: float,
    number_conf_thres: float,
    number_match_bonus: float,
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

    cluster_number = {}
    for cid in unique:
        nums = []
        for i in members[cid]:
            n = jersey_numbers[i]
            c = jersey_confs[i]
            if n is not None and c is not None and c >= number_conf_thres:
                nums.append(n)
        if not nums:
            cluster_number[cid] = None
        else:
            # Majority number in this micro-cluster.
            cluster_number[cid] = max(set(nums), key=nums.count)

    # Union-find over cluster ids.
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

    # Consider most similar centroid pairs first.
    pairs = []
    for i, a in enumerate(unique):
        for b in unique[i + 1 :]:
            sim = _cosine(comb_centroids[a], comb_centroids[b])
            pairs.append((sim, a, b))
    pairs.sort(reverse=True, key=lambda x: x[0])

    for sim, a, b in pairs:
        if sim < combined_cos_threshold:
            continue

        # At least one side should have decent support (protect against singleton drift).
        if max(len(members[a]), len(members[b])) < 2:
            continue

        fa = face_centroids[a]
        fb = face_centroids[b]
        if fa is not None and fb is not None:
            face_sim = _cosine(fa, fb)
            if face_sim < face_cos_threshold:
                continue

        # Soft number signal: confident match lowers merge threshold slightly.
        na = cluster_number.get(a)
        nb = cluster_number.get(b)
        local_threshold = combined_cos_threshold
        if na is not None and nb is not None and na == nb:
            local_threshold = max(0.0, combined_cos_threshold - number_match_bonus)
            if sim < local_threshold:
                continue

        union(a, b)

    # Build new labels.
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
    jersey_numbers: list,
    jersey_confs: list,
    assign_combined_cos: float,
    assign_face_cos: float,
    assign_margin: float,
    assign_no_face_combined_cos: float,
    number_conf_thres: float,
    number_match_bonus: float,
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

    cluster_number = {}
    for cid in cluster_ids:
        nums = []
        for i in members[cid]:
            n = jersey_numbers[i]
            c = jersey_confs[i]
            if n is not None and c is not None and c >= number_conf_thres:
                nums.append(n)
        if not nums:
            cluster_number[cid] = None
        else:
            cluster_number[cid] = max(set(nums), key=nums.count)

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

        # Strict combined+margin gates.
        min_combined = assign_combined_cos
        if face_embeddings[i] is None:
            min_combined = assign_no_face_combined_cos

        # Soft number signal:
        # - confident match allows a small threshold relaxation
        # - mismatch does not block (to avoid micro-splitting on OCR errors)
        item_num = jersey_numbers[i]
        item_num_conf = jersey_confs[i]
        target_num = cluster_number.get(best_cid)
        if (
            item_num is not None
            and item_num_conf is not None
            and item_num_conf >= number_conf_thres
            and target_num is not None
        ):
            if item_num == target_num:
                min_combined = max(0.0, min_combined - number_match_bonus)

        if best_sim < min_combined:
            continue
        if (best_sim - second_sim) < assign_margin:
            continue

        # If face exists and target has face centroid, require face agreement.
        item_face = face_embeddings[i]
        centroid_face = face_centroids.get(best_cid)
        if item_face is not None and centroid_face is not None:
            face_sim = _cosine(item_face, centroid_face)
            if face_sim < assign_face_cos:
                continue

        out[i] = best_cid

    return out


def _build_number_ocr():
    # Disable online model source checks after first download to reduce startup overhead.
    try:
        from paddleocr import PaddleOCR

        return PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    except Exception as exc:
        print(f"Jersey OCR disabled: {exc}")
        return None


def _jersey_roi(body_crop_bgr: np.ndarray) -> np.ndarray:
    """Take center torso region where jersey numbers are most likely visible."""
    h, w = body_crop_bgr.shape[:2]
    x1 = int(0.15 * w)
    x2 = int(0.85 * w)
    y1 = int(0.20 * h)
    y2 = int(0.82 * h)
    if x2 <= x1 or y2 <= y1:
        return body_crop_bgr
    return body_crop_bgr[y1:y2, x1:x2]


def _extract_number_candidates_from_ocr_output(ocr_output):
    """
    Yield (text, score) pairs from PaddleOCR outputs across different formats.
    Supports both old ocr() and newer predict() style results.
    """
    candidates = []
    if ocr_output is None:
        return candidates

    # Newer predict() style: list of dicts with rec_texts / rec_scores.
    if isinstance(ocr_output, list):
        for item in ocr_output:
            if isinstance(item, dict):
                texts = item.get("rec_texts", [])
                scores = item.get("rec_scores", [])
                for t, s in zip(texts, scores):
                    candidates.append((str(t), float(s)))
            elif isinstance(item, list):
                # Older style: [[box, (text, score)], ...]
                for sub in item:
                    if (
                        isinstance(sub, list)
                        and len(sub) >= 2
                        and isinstance(sub[1], (list, tuple))
                        and len(sub[1]) >= 2
                    ):
                        candidates.append((str(sub[1][0]), float(sub[1][1])))
    return candidates


def extract_jersey_number(body_crop_bgr: np.ndarray, number_ocr):
    """
    OCR jersey number from body crop.
    Returns (number_text_or_None, confidence_or_None).
    """
    if number_ocr is None:
        return None, None

    roi = _jersey_roi(body_crop_bgr)
    if roi.size == 0:
        return None, None

    # Speed + robustness: downscale oversized crops before OCR.
    h, w = roi.shape[:2]
    max_side = max(h, w)
    if max_side > 960:
        scale = 960.0 / max_side
        roi = cv2.resize(roi, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    # Use predict() to avoid deprecated warnings.
    try:
        out = number_ocr.predict(roi)
    except Exception as exc:
        print(f"Jersey OCR inference failed: {exc}")
        return None, None
    candidates = _extract_number_candidates_from_ocr_output(out)
    if not candidates:
        return None, None

    # Keep strongest candidate containing 1-3 digits.
    best_num = None
    best_conf = -1.0
    for text, conf in candidates:
        match = re.findall(r"\d{1,3}", text)
        if not match:
            continue
        # Pick first digit group; typical jersey is short integer.
        num = match[0]
        if conf > best_conf:
            best_conf = conf
            best_num = num
    if best_num is None:
        return None, None
    return best_num, float(best_conf)


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
    number_ocr = _build_number_ocr()
    face_enabled = face_app is not None
    jersey_ocr_enabled = number_ocr is not None
    if not face_enabled:
        # Without face embeddings, body-only vectors are more error-prone on similar uniforms.
        # Tighten merge/assign gates to reduce catastrophic over-merge.
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
    if not jersey_ocr_enabled:
        print("[CLUSTER MODE] Jersey OCR unavailable; number-based merge bonus disabled.", flush=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    body_model, body_preprocess = _build_body_model(device)

    records = []
    embeddings = []
    face_embeddings = []
    jersey_numbers = []
    jersey_confs = []
    crops = []
    face_detection_count = 0
    jersey_detection_count = 0

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
            crop = np.asarray(crop)
            if crop.ndim == 2:
                crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            if crop.ndim != 3 or crop.shape[2] != 3:
                print(
                    f"Skipping {image_path.name}: invalid crop shape={getattr(crop, 'shape', None)}",
                    flush=True,
                )
                continue
            if crop.dtype != np.uint8:
                crop = crop.astype(np.uint8, copy=False)
            crop = np.ascontiguousarray(crop)
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

        jersey_num, jersey_conf = extract_jersey_number(crop, number_ocr)
        if jersey_num is not None:
            jersey_detection_count += 1

        records.append(
            {
                "photo": image_path.name,
                "bbox_xyxy": [float(v) for v in det["bbox_xyxy"]],
                "confidence": float(det["confidence"]),
                "jersey_number": jersey_num,
                "jersey_confidence": jersey_conf,
            }
        )
        embeddings.append(emb)
        face_embeddings.append(face_n)
        jersey_numbers.append(jersey_num)
        jersey_confs.append(jersey_conf)
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
        jersey_numbers=jersey_numbers,
        jersey_confs=jersey_confs,
        combined_cos_threshold=args.merge_combined_cos,
        face_cos_threshold=args.merge_face_cos,
        number_conf_thres=args.number_conf_thres,
        number_match_bonus=args.number_match_bonus,
    )
    labels = _prototype_reassign_labels(
        labels=labels,
        combined_embeddings=X,
        face_embeddings=face_embeddings,
        jersey_numbers=jersey_numbers,
        jersey_confs=jersey_confs,
        assign_combined_cos=args.assign_combined_cos,
        assign_face_cos=args.assign_face_cos,
        assign_margin=args.assign_margin,
        assign_no_face_combined_cos=args.assign_no_face_combined_cos,
        number_conf_thres=args.number_conf_thres,
        number_match_bonus=args.number_match_bonus,
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
            "jersey_ocr_enabled": jersey_ocr_enabled,
            "face_detections": face_detection_count,
            "jersey_detections": jersey_detection_count,
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
            "number_conf_thres": args.number_conf_thres,
            "number_match_bonus": args.number_match_bonus,
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
            crop_img = np.asarray(crops[i])
            if crop_img.ndim == 2:
                crop_img = cv2.cvtColor(crop_img, cv2.COLOR_GRAY2BGR)
            if crop_img.ndim != 3 or crop_img.shape[2] != 3:
                raise ValueError(f"invalid crop shape={getattr(crop_img, 'shape', None)}")
            crop_img = _opencv_safe_array(crop_img)
            ok = cv2.imwrite(str(athlete_dir / crop_name), crop_img)
            if not ok:
                print(f"WARN: cv2.imwrite returned False for {rec['photo']} crop index {i}", flush=True)
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
