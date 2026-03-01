"""
Face + body player clustering pipeline for small sports image batches.

Pipeline:
1) Detect people with YOLOv26 (yolo26n.pt)
2) Crop body for each detected person
3) Try to detect a face inside the body crop (InsightFace / ArcFace)
4) Always extract a body embedding (OSNet via torchreid)
5) Combine embeddings:
   - 70% face + 30% body if face exists
   - body only if face missing
6) L2-normalize final embeddings
7) Cluster with HDBSCAN (min_cluster_size=2)
8) Print image membership per cluster
"""

from __future__ import annotations

import argparse
import os
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from PIL import Image
from ultralytics import YOLO 


_YOLO_ERROR_LOGGED = {"mem": False, "list": False, "path": False}


def load_image_bgr(image_path: Path, preloaded_image=None) -> Optional[np.ndarray]:
    """
    Load an image as BGR uint8. Tries cv2.imread first, then byte-based decode.
    Returns None if unreadable.
    """
    image = preloaded_image
    if image is not None and not isinstance(image, np.ndarray):
        print(
            f"Ignoring non-ndarray preloaded image for {image_path}: type={type(image)}",
            flush=True,
        )
        image = None

    if image is None:
        image = cv2.imread(str(image_path))

    if image is None:
        try:
            with open(image_path, "rb") as f:
                buf = np.frombuffer(f.read(), dtype=np.uint8)
            image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            image = None

    if image is None:
        exists = os.path.exists(image_path)
        size = os.path.getsize(image_path) if exists else 0
        print(
            f"Image load failed: path={image_path} exists={exists} size={size}",
            flush=True,
        )
        return None

    image = np.asarray(image)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim != 3 or image.shape[2] != 3:
        print(
            f"Image shape invalid for {image_path}: shape={getattr(image, 'shape', None)}",
            flush=True,
        )
        return None
    if image.shape[0] <= 0 or image.shape[1] <= 0:
        print(
            f"Image has empty dimensions for {image_path}: shape={image.shape}",
            flush=True,
        )
        return None
    if image.dtype != np.uint8:
        image = image.astype(np.uint8, copy=False)

    return np.ascontiguousarray(image)


def detect_people(
    image_path: Path,
    image_bgr: np.ndarray,
    yolo_model: YOLO,
    conf_threshold: float = 0.25,
) -> List[Dict]:
    """
    Detect person bounding boxes in a single image using YOLO.
    Returns a list of detection dicts with body crops and metadata.
    """
    image_bgr = load_image_bgr(image_path, preloaded_image=image_bgr)
    if image_bgr is None:
        return []
    print(
        "YOLO input "
        f"path={image_path} type={type(image_bgr)} shape={image_bgr.shape} "
        f"dtype={image_bgr.dtype} contiguous={image_bgr.flags['C_CONTIGUOUS']}",
        flush=True,
    )
    try:
        # Primary path: run on in-memory array.
        results = yolo_model.predict(
            source=image_bgr,
            classes=[0],  # COCO class 0 = person
            conf=conf_threshold,
            verbose=False,
        )[0]
    except Exception as exc_mem:
        if not _YOLO_ERROR_LOGGED["mem"]:
            print(
                f"YOLO in-memory error sample for {image_path.name}: {exc_mem}\n{traceback.format_exc()}",
                flush=True,
            )
            _YOLO_ERROR_LOGGED["mem"] = True
        try:
            # Secondary path: list wrapper for ultralytics source parser.
            results = yolo_model.predict(
                source=[image_bgr],
                classes=[0],
                conf=conf_threshold,
                verbose=False,
            )[0]
        except Exception as exc_list:
            if not _YOLO_ERROR_LOGGED["list"]:
                print(
                    f"YOLO list-source error sample for {image_path.name}: {exc_list}\n{traceback.format_exc()}",
                    flush=True,
                )
                _YOLO_ERROR_LOGGED["list"] = True
            try:
                # Tertiary path: filesystem source.
                results = yolo_model.predict(
                    source=str(image_path),
                    classes=[0],
                    conf=conf_threshold,
                    verbose=False,
                )[0]
            except Exception as exc_path:
                if not _YOLO_ERROR_LOGGED["path"]:
                    print(
                        f"YOLO path-source error sample for {image_path.name}: {exc_path}\n{traceback.format_exc()}",
                        flush=True,
                    )
                    _YOLO_ERROR_LOGGED["path"] = True
                print(f"YOLO detect failed for {image_path.name}; using full-frame fallback", flush=True)
                # Hard fallback for environments where OpenCV bridge is broken.
                h, w = image_bgr.shape[:2]
                if h <= 1 or w <= 1:
                    return []
                return [
                    {
                        "image_name": image_path.name,
                        "image_path": str(image_path),
                        "bbox_xyxy": [0, 0, w, h],
                        "confidence": 0.0,
                        "body_crop_bgr": np.ascontiguousarray(image_bgr.astype(np.uint8, copy=False)),
                    }
                ]

    detections: List[Dict] = []
    if results.boxes is None or len(results.boxes) == 0:
        return detections

    h, w = image_bgr.shape[:2]
    for box in results.boxes:
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().tolist()
        conf = float(box.conf[0].cpu().numpy())

        # Clamp coordinates to image bounds.
        x1_i = max(0, min(w - 1, int(x1)))
        y1_i = max(0, min(h - 1, int(y1)))
        x2_i = max(0, min(w, int(x2)))
        y2_i = max(0, min(h, int(y2)))
        if x2_i <= x1_i or y2_i <= y1_i:
            continue

        body_crop = image_bgr[y1_i:y2_i, x1_i:x2_i]
        if body_crop.size == 0:
            continue
        body_crop = np.ascontiguousarray(np.asarray(body_crop).astype(np.uint8, copy=False))

        detections.append(
            {
                "image_name": image_path.name,
                "image_path": str(image_path),
                "bbox_xyxy": [x1_i, y1_i, x2_i, y2_i],
                "confidence": conf,
                "body_crop_bgr": body_crop,
            }
        )

    return detections


def extract_face_embedding(
    body_crop_bgr: np.ndarray,
    face_app,
) -> Optional[np.ndarray]:
    """
    Try to find a face in the body crop and return ArcFace embedding.
    Returns None if no face is detected.
    """
    if face_app is None:
        return None
    try:
        faces = face_app.get(body_crop_bgr)
    except Exception:
        return None
    if not faces:
        return None

    # Use largest detected face to reduce false positives on crowd/background.
    def face_area(face_obj) -> float:
        x1, y1, x2, y2 = face_obj.bbox
        return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))

    best_face = max(faces, key=face_area)
    emb = np.asarray(best_face.embedding, dtype=np.float32)
    return emb


def extract_body_embedding(
    body_crop_bgr: np.ndarray,
    body_model: torch.nn.Module,
    preprocess,
    device: torch.device,
) -> np.ndarray:
    """
    Extract body embedding using pretrained OSNet.
    """
    arr = np.asarray(body_crop_bgr)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"Invalid body crop shape for embedding: {getattr(arr, 'shape', None)}")
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8, copy=False)
    arr = np.ascontiguousarray(arr)
    # Avoid cv2 color conversion in environments where OpenCV bridge is unstable.
    crop_rgb = arr[:, :, ::-1].copy()
    pil_img = Image.fromarray(crop_rgb)
    tensor = preprocess(pil_img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = body_model(tensor)

    # torchreid OSNet may return tensor or tuple/list; keep first tensor.
    if isinstance(feat, (tuple, list)):
        feat = feat[0]
    emb = feat.detach().cpu().numpy().reshape(-1).astype(np.float32)
    return emb


def combine_embeddings(
    face_embedding: Optional[np.ndarray],
    body_embedding: np.ndarray,
    face_weight: float = 0.7,
    body_weight: float = 0.3,
) -> np.ndarray:
    """
    Combine face/body embeddings:
    - 70% face + 30% body when face exists
    - body only when face missing
    Final vector is L2-normalized.
    """

    def l2norm(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v) + 1e-12
        return v / n

    body_vec = l2norm(body_embedding.astype(np.float32))

    if face_embedding is None:
        return body_vec

    face_vec = l2norm(face_embedding.astype(np.float32))
    # ArcFace and OSNet are usually 512-dim. If mismatch occurs, fallback body-only.
    if face_vec.shape[0] != body_vec.shape[0]:
        return body_vec

    combined = face_weight * face_vec + body_weight * body_vec
    return l2norm(combined.astype(np.float32))


def cluster_players(
    embeddings: List[np.ndarray],
    min_cluster_size: int = 2,
) -> np.ndarray:
    """
    Cluster normalized embeddings with HDBSCAN.
    Returns cluster labels (shape: [N]).
    """
    if not embeddings:
        return np.array([], dtype=int)

    try:
        import hdbscan
    except ImportError as exc:
        raise ImportError(
            "hdbscan is not installed. Install with: pip install hdbscan"
        ) from exc

    X = np.vstack(embeddings)
    # Because vectors are normalized, Euclidean distance behaves well for clustering.
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean")
    labels = clusterer.fit_predict(X)
    return labels


def _build_face_app():
    try:
        from insightface.app import FaceAnalysis
    except Exception as exc:
        print(f"Face model disabled: {exc}")
        return None

    try:
        providers = ["CPUExecutionProvider"]
        face_app = FaceAnalysis(name="buffalo_l", providers=providers)
        face_app.prepare(ctx_id=-1, det_size=(640, 640))
        return face_app
    except Exception as exc:
        print(f"Face model disabled during initialization: {exc}")
        return None


def _build_body_model(device: torch.device):
    try:
        import torchreid
    except ImportError as exc:
        missing = ""
        if getattr(exc, "name", None):
            missing = f" (missing module: {exc.name})"
        raise ImportError(
            "torchreid runtime dependency missing"
            f"{missing}. Install torchreid extras (e.g. gdown, tensorboard)."
        ) from exc

    # OSNet for person ReID body embeddings.
    body_model = torchreid.models.build_model(
        name="osnet_x1_0",
        num_classes=1000,
        pretrained=True,
    )
    body_model.eval().to(device)

    # Avoid torchvision ToTensor() numpy bridge to prevent numpy instance/type conflicts.
    mean = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32).view(3, 1, 1)

    def preprocess(pil_img: Image.Image) -> torch.Tensor:
        img = pil_img.resize((128, 256), Image.Resampling.BILINEAR).convert("RGB")
        w, h = img.size
        buf = torch.ByteTensor(torch.ByteStorage.from_buffer(img.tobytes()))
        tensor = buf.view(h, w, 3).permute(2, 0, 1).to(dtype=torch.float32).div_(255.0)
        return (tensor - mean) / std

    return body_model, preprocess


def _collect_images(images_dir: Path) -> List[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted([p for p in images_dir.iterdir() if p.suffix.lower() in exts])


def main():
    parser = argparse.ArgumentParser(
        description="Group players from a sports image folder using face+body embeddings."
    )
    parser.add_argument(
        "--images-dir",
        default="/images",
        help="Folder containing input images (default: /images).",
    )
    parser.add_argument(
        "--yolo-model",
        default="yolo26n.pt",
        help="YOLOv26 model name/path (default: yolo26n.pt).",
    )
    parser.add_argument(
        "--conf-threshold",
        type=float,
        default=0.25,
        help="YOLO person confidence threshold.",
    )
    parser.add_argument(
        "--min-cluster-size",
        type=int,
        default=2,
        help="HDBSCAN min_cluster_size.",
    )
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")

    image_paths = _collect_images(images_dir)
    if not image_paths:
        raise ValueError(f"No images found in {images_dir}")

    print(f"Loading YOLO model: {args.yolo_model}")
    yolo_model = YOLO(args.yolo_model)

    print("Loading InsightFace ArcFace model...")
    face_app = _build_face_app()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Loading OSNet body model on {device}...")
    body_model, body_preprocess = _build_body_model(device)

    all_records: List[Dict] = []
    final_embeddings: List[np.ndarray] = []

    for idx, image_path in enumerate(image_paths, start=1):
        image_bgr = load_image_bgr(image_path)
        if image_bgr is None:
            continue

        detections = detect_people(
            image_path=image_path,
            image_bgr=image_bgr,
            yolo_model=yolo_model,
            conf_threshold=args.conf_threshold,
        )

        print(f"[{idx}/{len(image_paths)}] {image_path.name}: {len(detections)} people")

        for det in detections:
            body_crop = det["body_crop_bgr"]
            face_emb = extract_face_embedding(body_crop_bgr=body_crop, face_app=face_app)
            body_emb = extract_body_embedding(
                body_crop_bgr=body_crop,
                body_model=body_model,
                preprocess=body_preprocess,
                device=device,
            )
            final_emb = combine_embeddings(face_emb, body_emb, face_weight=0.7, body_weight=0.3)
            final_embeddings.append(final_emb)

            det["has_face"] = face_emb is not None
            # Remove raw crop before storing records.
            det.pop("body_crop_bgr", None)
            all_records.append(det)

    labels = cluster_players(final_embeddings, min_cluster_size=args.min_cluster_size)
    if labels.size == 0:
        print("No detections were clustered.")
        return

    # Print cluster membership by image names.
    unique_labels = sorted(set(labels.tolist()))
    print("\n=== Cluster Results ===")
    for cluster_id in unique_labels:
        member_idxs = np.where(labels == cluster_id)[0].tolist()
        member_images = sorted({all_records[i]["image_name"] for i in member_idxs})

        if cluster_id == -1:
            print(f"noise/unclustered ({len(member_idxs)} detections):")
        else:
            print(f"cluster_{cluster_id} ({len(member_idxs)} detections):")

        for name in member_images:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
