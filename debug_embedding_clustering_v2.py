"""
V2 embedding debug script for athlete clustering.

Improvements over v1:
1) Quality filters for body crops (size + blur).
2) Adaptive face/body weighting from face quality (det score + face size ratio).
3) Epsilon sweep for DBSCAN on combined embeddings (0.60 -> 0.75).
4) Compact metrics per eps to identify over-merge vs over-split regimes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from sklearn.cluster import DBSCAN
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity
from ultralytics import YOLO

from face_body_cluster_pipeline import (
    _build_body_model,
    _build_face_app,
    detect_people,
    extract_body_embedding,
)


def normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalize a vector."""
    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n < 1e-12:
        return arr
    return arr / n


def laplacian_var(image_bgr: np.ndarray) -> Optional[float]:
    """Simple sharpness proxy: variance of Laplacian."""
    try:
        arr = np.asarray(image_bgr)
        if arr.ndim == 2:
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.ndim != 3 or arr.shape[2] != 3:
            return None
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8, copy=False)
        arr = np.ascontiguousarray(arr)
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return None


def pick_largest_detection(dets: List[Dict]) -> Dict:
    """Use largest bbox as representative person for per-image debugging."""
    best = None
    best_area = -1.0
    for d in dets:
        x1, y1, x2, y2 = d["bbox_xyxy"]
        area = float(max(0, x2 - x1) * max(0, y2 - y1))
        if area > best_area:
            best_area = area
            best = d
    return best


def body_quality_ok(
    crop_bgr: np.ndarray,
    min_w: int,
    min_h: int,
    min_laplacian_var: float,
) -> Tuple[bool, str]:
    """Reject tiny/very blurry crops that degrade embedding quality."""
    if crop_bgr is None:
        return False, "invalid_crop(None)"
    try:
        arr = np.asarray(crop_bgr)
    except Exception:
        return False, "invalid_crop(non_array)"
    if arr.ndim != 3 or arr.shape[2] != 3:
        return False, f"invalid_crop(shape={getattr(arr, 'shape', None)})"
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8, copy=False)
    arr = np.ascontiguousarray(arr)

    h, w = arr.shape[:2]
    if w < min_w or h < min_h:
        return False, f"small_crop({w}x{h})"
    blur_score = laplacian_var(arr)
    if blur_score is None:
        # OpenCV not healthy in this environment; skip blur gate instead of hard-failing.
        return True, "ok_no_blur_check"
    if blur_score < min_laplacian_var:
        return False, f"blurry({blur_score:.1f})"
    return True, "ok"


def extract_face_embedding_with_quality(
    body_crop_bgr: np.ndarray,
    face_app,
) -> Tuple[Optional[np.ndarray], Optional[float], Optional[float]]:
    """
    Return (embedding, face_det_score, face_area_ratio_in_body).
    If no face is detected, returns (None, None, None).
    """
    if face_app is None:
        return None, None, None
    try:
        faces = face_app.get(body_crop_bgr)
    except Exception:
        return None, None, None
    if not faces:
        return None, None, None

    # Use largest face in body crop.
    def area(fobj) -> float:
        x1, y1, x2, y2 = fobj.bbox
        return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))

    best = max(faces, key=area)
    emb = np.asarray(best.embedding, dtype=np.float32)
    score = float(getattr(best, "det_score", 0.5))

    bh, bw = body_crop_bgr.shape[:2]
    face_area = area(best)
    body_area = float(max(1, bw * bh))
    ratio = face_area / body_area
    return emb, score, ratio


def adaptive_weights(face_score: Optional[float], face_ratio: Optional[float]) -> Tuple[float, float]:
    """
    Adaptive weighting:
    - if no face, use body only
    - else compute face weight from score + face-size ratio (clipped)
    """
    if face_score is None or face_ratio is None:
        return 0.0, 1.0

    # Normalize ratio into a practical range (face typically tiny inside body crop).
    # ratio_ref ~ 0.08 means face occupies 8% body-crop area.
    ratio_term = min(1.0, max(0.0, face_ratio / 0.08))
    # Base + score contribution + size contribution.
    face_w = 0.45 + 0.35 * float(face_score) + 0.20 * ratio_term
    face_w = float(min(0.90, max(0.35, face_w)))
    body_w = 1.0 - face_w
    return face_w, body_w


def matrix_print(title: str, emb: np.ndarray, names: List[str]) -> None:
    if len(emb) == 0:
        print(f"\n{title}\n(no embeddings)")
        return
    sim = cosine_similarity(emb)
    print(f"\n{title}")
    print("rows/cols:", names)
    print(np.array2string(sim, precision=3, suppress_small=True))


def dbscan_labels(emb: np.ndarray, eps: float, min_samples: int = 2) -> np.ndarray:
    if len(emb) == 0:
        return np.array([], dtype=int)
    return DBSCAN(eps=eps, min_samples=min_samples, metric="euclidean").fit_predict(emb)


def summarize_labels(labels: np.ndarray) -> Dict[str, float]:
    if labels.size == 0:
        return {"clusters": 0, "noise": 0, "largest_cluster": 0}
    uniq, counts = np.unique(labels, return_counts=True)
    cluster_counts = [int(c) for l, c in zip(uniq, counts) if l != -1]
    noise = int(counts[np.where(uniq == -1)][0]) if np.any(uniq == -1) else 0
    return {
        "clusters": len(cluster_counts),
        "noise": noise,
        "largest_cluster": max(cluster_counts) if cluster_counts else 0,
    }


def silhouette_if_valid(emb: np.ndarray, labels: np.ndarray) -> Optional[float]:
    # Silhouette requires >=2 non-noise clusters and enough samples.
    if len(emb) < 3:
        return None
    valid_mask = labels != -1
    if np.sum(valid_mask) < 3:
        return None
    kept_labels = labels[valid_mask]
    if len(set(kept_labels.tolist())) < 2:
        return None
    try:
        return float(silhouette_score(emb[valid_mask], kept_labels, metric="euclidean"))
    except Exception:
        return None


def run():
    parser = argparse.ArgumentParser(description="V2 debug script for face/body athlete clustering.")
    parser.add_argument("--images-dir", default="game_photos")
    parser.add_argument("--yolo-model", default="yolo26n.pt")
    parser.add_argument("--conf-threshold", type=float, default=0.25)
    parser.add_argument("--min-w", type=int, default=80)
    parser.add_argument("--min-h", type=int, default=140)
    parser.add_argument("--min-lap-var", type=float, default=20.0)
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    images = sorted([p for p in images_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}])
    if not images:
        raise ValueError(f"No images found in {images_dir}")

    print(f"Using images: {images_dir} ({len(images)})")
    yolo = YOLO(args.yolo_model)
    face_app = _build_face_app()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    body_model, body_preprocess = _build_body_model(device)

    names: List[str] = []
    face_names: List[str] = []
    face_norms: List[np.ndarray] = []
    body_norms: List[np.ndarray] = []
    comb_norms: List[np.ndarray] = []

    dropped = []
    for i, p in enumerate(images, start=1):
        img = cv2.imread(str(p))
        if img is None:
            dropped.append((p.name, "read_fail"))
            continue

        dets = detect_people(p, img, yolo, conf_threshold=args.conf_threshold)
        if not dets:
            dropped.append((p.name, "no_person"))
            continue

        d = pick_largest_detection(dets)
        crop = d["body_crop_bgr"]

        ok, reason = body_quality_ok(crop, args.min_w, args.min_h, args.min_lap_var)
        if not ok:
            dropped.append((p.name, reason))
            continue

        body_raw = extract_body_embedding(crop, body_model, body_preprocess, device)
        body_n = normalize(body_raw)
        face_raw, face_score, face_ratio = extract_face_embedding_with_quality(crop, face_app)

        if face_raw is not None:
            face_n = normalize(face_raw)
            fw, bw = adaptive_weights(face_score, face_ratio)
            comb = normalize(np.concatenate([fw * face_n, bw * body_n], axis=0))

            face_norms.append(face_n)
            face_names.append(p.name)
            face_l2 = float(np.linalg.norm(face_raw))
        else:
            fw, bw = 0.0, 1.0
            zero_face = np.zeros_like(body_n, dtype=np.float32)
            comb = normalize(np.concatenate([zero_face, bw * body_n], axis=0))
            face_l2 = 0.0

        names.append(p.name)
        body_norms.append(body_n)
        comb_norms.append(comb)

        print(
            f"[{i}/{len(images)}] {p.name} | "
            f"face={face_raw is not None} score={face_score if face_score is not None else 'n/a'} "
            f"ratio={face_ratio if face_ratio is not None else 'n/a'} "
            f"weights=(f:{fw:.2f}, b:{bw:.2f}) "
            f"L2(face)={face_l2:.3f} L2(body)={float(np.linalg.norm(body_raw)):.3f}"
        )

    if dropped:
        print("\nDropped images:")
        for n, r in dropped:
            print(f"  - {n}: {r}")

    face_arr = np.vstack(face_norms) if face_norms else np.empty((0, 0), dtype=np.float32)
    body_arr = np.vstack(body_norms) if body_norms else np.empty((0, 0), dtype=np.float32)
    comb_arr = np.vstack(comb_norms) if comb_norms else np.empty((0, 0), dtype=np.float32)

    matrix_print("FACE COSINE SIMILARITY MATRIX:", face_arr, face_names)
    matrix_print("BODY COSINE SIMILARITY MATRIX:", body_arr, names)
    matrix_print("COMBINED COSINE SIMILARITY MATRIX:", comb_arr, names)

    # Baseline face/body labels at eps=0.8 (as prior debug reference).
    face_labels = dbscan_labels(face_arr, eps=0.8, min_samples=2)
    body_labels = dbscan_labels(body_arr, eps=0.8, min_samples=2)
    print("\nFACE CLUSTERS:")
    for n, l in zip(face_names, face_labels.tolist() if face_labels.size else []):
        print(f"  {n}: {l}")
    print("  labels:", face_labels.tolist() if face_labels.size else [])

    print("\nBODY CLUSTERS:")
    for n, l in zip(names, body_labels.tolist() if body_labels.size else []):
        print(f"  {n}: {l}")
    print("  labels:", body_labels.tolist() if body_labels.size else [])

    # Sweep eps for combined embeddings.
    print("\nCOMBINED CLUSTERS (EPS SWEEP):")
    for eps in [0.60, 0.65, 0.70, 0.75]:
        labels = dbscan_labels(comb_arr, eps=eps, min_samples=2)
        summary = summarize_labels(labels)
        sil = silhouette_if_valid(comb_arr, labels)
        sil_txt = f"{sil:.3f}" if sil is not None else "n/a"
        print(
            f"  eps={eps:.2f} -> labels={labels.tolist()} | "
            f"clusters={summary['clusters']} noise={summary['noise']} "
            f"largest={summary['largest_cluster']} silhouette={sil_txt}"
        )


if __name__ == "__main__":
    run()
