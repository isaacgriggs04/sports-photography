FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PROJECT_ROOT=/app
ENV CLUSTER_SCRIPT_PATH=update_web_clusters_combined.py
ENV YOLO_MODEL_PATH=yolo11n.pt

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    python3-dev \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-worker.txt /app/requirements-worker.txt
# Install CPU-only torch wheels to avoid pulling huge CUDA runtime packages.
RUN pip install --no-cache-dir --retries 10 --timeout 300 \
    torch==2.5.1 torchvision==0.20.1 \
    --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --retries 10 --timeout 300 -r /app/requirements-worker.txt
# Ensure numpy/scipy ABI compatibility for sklearn/hdbscan import path.
RUN pip install --no-cache-dir --force-reinstall numpy==1.26.4 scipy==1.11.4
# Pin opencv to avoid upgrade to 4.11+ which can cause "Expected Ptr<cv::UMat>" with numpy arrays.
RUN pip install --no-cache-dir --force-reinstall "opencv-python-headless==4.10.0.84"

COPY worker/cloud_cluster_worker.py /app/worker/cloud_cluster_worker.py
COPY update_web_clusters_combined.py /app/update_web_clusters_combined.py
COPY face_body_cluster_pipeline.py /app/face_body_cluster_pipeline.py
COPY debug_embedding_clustering_v2.py /app/debug_embedding_clustering_v2.py

CMD ["python", "worker/cloud_cluster_worker.py"]
