import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import torch
from sklearn.cluster import DBSCAN
from torchvision import models, transforms
from ultralytics import YOLO
from PIL import Image


def parse_args():
    parser = argparse.ArgumentParser(description="Detect and cluster athletes from game photos.")
    parser.add_argument(
        "--photos-dir",
        default="game_photos",
        help="Directory containing game images.",
    )
    parser.add_argument(
        "--yolo-model",
        default="yolo26n.pt",
        help="YOLO model file/name to use for person detection.",
    )
    parser.add_argument(
        "--output-json",
        default="athlete_groups.json",
        help="Path to write grouped athlete JSON.",
    )
    parser.add_argument(
        "--output-crops-dir",
        default="athlete_groups",
        help="Directory to write grouped crop previews.",
    )
    parser.add_argument(
        "--conf-thres",
        type=float,
        default=0.35,
        help="YOLO confidence threshold for person detections.",
    )
    parser.add_argument(
        "--eps",
        type=float,
        default=0.4,
        help="DBSCAN epsilon (cosine distance). Lower = stricter clustering.",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=2,
        help="DBSCAN min samples to form a cluster.",
    )
    return parser.parse_args()


def load_embedder(device):
    model = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V2)
    model.fc = torch.nn.Identity()
    model.eval().to(device)
    preprocess = transforms.Compose(
        [
            transforms.Resize((256, 128)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )
    return model, preprocess


def crop_and_embed(image_bgr, box_xyxy, embedder, preprocess, device):
    h, w = image_bgr.shape[:2]
    x1, y1, x2, y2 = map(int, box_xyxy)
    x1 = max(0, min(w - 1, x1))
    x2 = max(0, min(w, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(0, min(h, y2))
    if x2 <= x1 or y2 <= y1:
        return None, None

    crop = image_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return None, None

    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(crop_rgb)
    tensor = preprocess(pil_img).unsqueeze(0).to(device)
    with torch.no_grad():
        emb = embedder(tensor).detach().cpu().numpy().reshape(-1)
    norm = np.linalg.norm(emb) + 1e-12
    emb = emb / norm
    return crop, emb


def main():
    args = parse_args()
    photos_dir = Path(args.photos_dir)
    if not photos_dir.exists():
        alt = Path("game photos")
        if alt.exists():
            photos_dir = alt
        else:
            raise FileNotFoundError(
                f"Could not find '{args.photos_dir}' or 'game photos' directory."
            )

    image_paths = sorted(
        [
            p
            for p in photos_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]
    )
    if not image_paths:
        raise ValueError(f"No images found in {photos_dir}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    detector = YOLO(args.yolo_model)
    embedder, preprocess = load_embedder(device)

    detections = []
    embeddings = []
    crops = []

    print(f"Found {len(image_paths)} images in {photos_dir}")
    print(f"Using device: {device}")
    for idx, image_path in enumerate(image_paths, start=1):
        print(f"[{idx}/{len(image_paths)}] Processing {image_path.name}...")
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        results = detector.predict(
            source=image,
            verbose=False,
            conf=args.conf_thres,
            classes=[0],  # person class
        )[0]
        if results.boxes is None or len(results.boxes) == 0:
            continue

        for box in results.boxes:
            xyxy = box.xyxy[0].cpu().numpy().tolist()
            conf = float(box.conf[0].cpu().numpy())
            crop, emb = crop_and_embed(image, xyxy, embedder, preprocess, device)
            if crop is None or emb is None:
                continue
            detections.append(
                {
                    "photo": image_path.name,
                    "bbox_xyxy": [round(v, 2) for v in xyxy],
                    "confidence": round(conf, 4),
                }
            )
            embeddings.append(emb)
            crops.append(crop)

    if not embeddings:
        output = {"athletes": {}, "unclustered": [], "stats": {"detections": 0, "images": len(image_paths)}}
        Path(args.output_json).write_text(json.dumps(output, indent=2))
        print(f"No player detections found. Wrote empty results to {args.output_json}")
        return

    emb_matrix = np.vstack(embeddings)
    clusterer = DBSCAN(eps=args.eps, min_samples=args.min_samples, metric="cosine")
    labels = clusterer.fit_predict(emb_matrix)

    unique_labels = sorted([l for l in set(labels) if l != -1])
    label_to_name = {label: f"athlete_{idx + 1}" for idx, label in enumerate(unique_labels)}

    output = {
        "athletes": {},
        "unclustered": [],
        "stats": {
            "images": len(image_paths),
            "detections": len(detections),
            "clustered_detections": int(np.sum(labels != -1)),
            "clusters": len(unique_labels),
        },
    }

    crops_root = Path(args.output_crops_dir)
    crops_root.mkdir(parents=True, exist_ok=True)

    for i, label in enumerate(labels):
        det = detections[i]
        if label == -1:
            output["unclustered"].append(det)
            continue

        athlete_name = label_to_name[label]
        output["athletes"].setdefault(athlete_name, []).append(det)

        athlete_dir = crops_root / athlete_name
        athlete_dir.mkdir(parents=True, exist_ok=True)
        crop_name = f"{Path(det['photo']).stem}_det{i+1}.jpg"
        cv2.imwrite(str(athlete_dir / crop_name), crops[i])

    Path(args.output_json).write_text(json.dumps(output, indent=2))

    print("Grouping complete.")
    print(f"Images processed: {len(image_paths)}")
    print(f"Player detections: {len(detections)}")
    print(f"Athlete groups: {len(unique_labels)}")
    print(f"Output JSON: {args.output_json}")
    print(f"Crop folders: {args.output_crops_dir}")


if __name__ == "__main__":
    main()
