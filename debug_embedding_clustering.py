"""
Debug script for athlete embedding collapse issues.

What this script does:
1) Detect people with YOLOv26.
2) For each image, pick one target athlete crop (largest detected person).
3) Extract face embedding (ArcFace) and body embedding (OSNet).
4) Print L2 norms of raw face/body embeddings.
5) Normalize face/body embeddings separately.
6) Concatenate normalized face+body, then normalize final combined vector.
7) Print cosine similarity matrices for face/body/combined.
8) Cluster with DBSCAN (euclidean) for:
   - face only
   - body only
   - combined
9) Print cluster labels with image names for easy comparison.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import torch
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_similarity
from ultralytics import YOLO

from face_body_cluster_pipeline import (
    _build_body_model,
    _build_face_app,
    detect_people,
    extract_body_embedding,
    extract_face_embedding,
)


def normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalize a vector (safe against zero norm)."""
    v = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return v
    return v / n


def _largest_person_index(detections: List[dict]) -> int:
    """Pick the largest detected person box as the representative athlete crop."""
    areas = []
    for det in detections:
        x1, y1, x2, y2 = det["bbox_xyxy"]
        areas.append(max(0, x2 - x1) * max(0, y2 - y1))
    return int(np.argmax(areas))


def _print_similarity_matrix(title: str, matrix: np.ndarray, names: List[str]) -> None:
    print(f"\n{title}")
    print("rows/cols:", names)
    print(np.array2string(matrix, precision=3, suppress_small=True))


def _run_dbscan_and_print(title: str, embeddings: np.ndarray, names: List[str]) -> None:
    """
    Run DBSCAN using euclidean distance and print labels with image names.
    For small datasets, this gives a quick sanity check against cluster collapse.
    """
    if len(embeddings) == 0:
        print(f"\n{title}\n(no embeddings)")
        return
    labels = DBSCAN(eps=0.55, min_samples=2, metric="euclidean").fit_predict(embeddings)
    print(f"\n{title}")
    for name, label in zip(names, labels.tolist()):
        print(f"  {name}: {label}")
    print("  labels:", labels.tolist())


def collect_embeddings(
    images_dir: Path,
    yolo_model_name: str,
    conf_threshold: float,
    face_weight: float,
    body_weight: float,
) -> Tuple[List[str], List[np.ndarray], List[np.ndarray], List[np.ndarray]]:
    """
    Collect face/body/combined embeddings for one representative detection per image.

    Returns:
      image_names
      face_embeddings_norm (only images where a face is found)
      body_embeddings_norm (all images with a person detection)
      combined_embeddings_norm (all images with a person detection)
    """
    image_paths = sorted(
        [p for p in images_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    )
    if not image_paths:
        raise ValueError(f"No images found in {images_dir}")

    yolo = YOLO(yolo_model_name)
    face_app = _build_face_app()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    body_model, body_preprocess = _build_body_model(device)

    names_all: List[str] = []
    face_norm_list: List[np.ndarray] = []
    face_names: List[str] = []
    body_norm_list: List[np.ndarray] = []
    combined_norm_list: List[np.ndarray] = []

    print(f"Processing {len(image_paths)} images from: {images_dir}")
    for idx, image_path in enumerate(image_paths, start=1):
        img = cv2.imread(str(image_path))
        if img is None:
            print(f"[{idx}] {image_path.name}: failed to read, skipping")
            continue

        detections = detect_people(
            image_path=image_path,
            image_bgr=img,
            yolo_model=yolo,
            conf_threshold=conf_threshold,
        )
        if not detections:
            print(f"[{idx}] {image_path.name}: no people detected, skipping")
            continue

        det = detections[_largest_person_index(detections)]
        crop = det["body_crop_bgr"]

        # Always compute body embedding.
        body_raw = extract_body_embedding(crop, body_model, body_preprocess, device)
        body_norm = normalize(body_raw)

        # Try face embedding.
        face_raw = extract_face_embedding(crop, face_app)
        has_face = face_raw is not None
        if has_face:
            face_norm = normalize(face_raw)
            # Combined: face-heavy scaling then concat, then final normalization.
            combined = normalize(np.concatenate([face_weight * face_norm, body_weight * body_norm], axis=0))
            face_norm_list.append(face_norm)
            face_names.append(image_path.name)
            face_l2 = float(np.linalg.norm(face_raw))
        else:
            # Combined fallback when no face: zero-face + weighted body.
            zero_face = np.zeros_like(body_norm, dtype=np.float32)
            combined = normalize(np.concatenate([zero_face, body_weight * body_norm], axis=0))
            face_l2 = 0.0

        names_all.append(image_path.name)
        body_norm_list.append(body_norm)
        combined_norm_list.append(combined)

        print(
            f"[{idx}] {image_path.name} | "
            f"face_detected={has_face} | "
            f"face_l2={face_l2:.4f} | "
            f"body_l2={float(np.linalg.norm(body_raw)):.4f}"
        )

    print("\nSummary:")
    print(f"  images used for body/combined: {len(names_all)}")
    print(f"  images with face embeddings: {len(face_norm_list)}")

    return names_all, face_names, face_norm_list, body_norm_list, combined_norm_list


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug face/body embedding collapse.")
    parser.add_argument("--images-dir", default="game_photos", help="Folder containing images.")
    parser.add_argument("--yolo-model", default="yolo26n.pt", help="YOLO model file/name.")
    parser.add_argument("--conf-threshold", type=float, default=0.25, help="YOLO confidence threshold.")
    parser.add_argument("--face-weight", type=float, default=0.85, help="Weight for face embedding in combined vector.")
    parser.add_argument("--body-weight", type=float, default=0.15, help="Weight for body embedding in combined vector.")
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")

    names_all, face_names, face_norm_list, body_norm_list, combined_norm_list = collect_embeddings(
        images_dir=images_dir,
        yolo_model_name=args.yolo_model,
        conf_threshold=args.conf_threshold,
        face_weight=args.face_weight,
        body_weight=args.body_weight,
    )

    face_arr = np.vstack(face_norm_list) if face_norm_list else np.empty((0, 0), dtype=np.float32)
    body_arr = np.vstack(body_norm_list) if body_norm_list else np.empty((0, 0), dtype=np.float32)
    combined_arr = (
        np.vstack(combined_norm_list) if combined_norm_list else np.empty((0, 0), dtype=np.float32)
    )

    # Cosine similarity diagnostics.
    if len(face_arr) > 0:
        _print_similarity_matrix(
            "FACE COSINE SIMILARITY MATRIX:",
            cosine_similarity(face_arr),
            face_names,
        )
    else:
        print("\nFACE COSINE SIMILARITY MATRIX:\n(no face embeddings)")

    if len(body_arr) > 0:
        _print_similarity_matrix(
            "BODY COSINE SIMILARITY MATRIX:",
            cosine_similarity(body_arr),
            names_all,
        )
    else:
        print("\nBODY COSINE SIMILARITY MATRIX:\n(no body embeddings)")

    if len(combined_arr) > 0:
        _print_similarity_matrix(
            "COMBINED COSINE SIMILARITY MATRIX:",
            cosine_similarity(combined_arr),
            names_all,
        )
    else:
        print("\nCOMBINED COSINE SIMILARITY MATRIX:\n(no combined embeddings)")

    # DBSCAN clustering diagnostics (euclidean metric, as requested).
    _run_dbscan_and_print("FACE CLUSTERS:", face_arr, face_names)
    _run_dbscan_and_print("BODY CLUSTERS:", body_arr, names_all)
    _run_dbscan_and_print("COMBINED CLUSTERS:", combined_arr, names_all)


if __name__ == "__main__":
    main()
