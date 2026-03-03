FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PROJECT_ROOT=/app
ENV CLUSTER_SCRIPT_PATH=update_web_clusters_combined.py
ENV YOLO_MODEL_PATH=yolo26n.pt

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-worker.txt /app/requirements-worker.txt
RUN pip install --no-cache-dir -r /app/requirements-worker.txt

COPY worker/cloud_cluster_worker.py /app/worker/cloud_cluster_worker.py
COPY update_web_clusters_combined.py /app/update_web_clusters_combined.py
COPY face_body_cluster_pipeline.py /app/face_body_cluster_pipeline.py
COPY debug_embedding_clustering_v2.py /app/debug_embedding_clustering_v2.py

CMD ["python", "worker/cloud_cluster_worker.py"]
