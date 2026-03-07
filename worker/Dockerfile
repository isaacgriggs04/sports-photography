FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PROJECT_ROOT=/app
ENV CLUSTER_SCRIPT_PATH=update_web_clusters_combined.py
ENV YOLO_MODEL_PATH=yolo11m.pt

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
# Pin opencv without letting pip upgrade numpy underneath scipy/sklearn/hdbscan.
RUN pip install --no-cache-dir --force-reinstall \
    "opencv-python-headless==4.10.0.84" \
    "numpy==1.26.4"
# Reinstall ABI-sensitive wheels together so the final environment is internally consistent.
RUN pip install --no-cache-dir --force-reinstall \
    "numpy==1.26.4" \
    "scipy==1.11.4" \
    "scikit-learn==1.5.2" \
    "hdbscan==0.8.39"
RUN python -c "import hdbscan, numpy, scipy, sklearn; print({'numpy': numpy.__version__, 'scipy': scipy.__version__, 'sklearn': sklearn.__version__, 'hdbscan': getattr(hdbscan, '__version__', 'unknown')})"
RUN python -c "import paddle, paddleocr; print({'paddle': paddle.__version__, 'paddleocr': getattr(paddleocr, '__version__', 'unknown')})"

COPY worker/cloud_cluster_worker.py /app/worker/cloud_cluster_worker.py
COPY update_web_clusters_combined.py /app/update_web_clusters_combined.py
COPY face_body_cluster_pipeline.py /app/face_body_cluster_pipeline.py
COPY debug_embedding_clustering_v2.py /app/debug_embedding_clustering_v2.py

CMD ["python", "worker/cloud_cluster_worker.py"]
