FROM node:20-bullseye AS frontend-build
WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Install torch + torchvision from PyTorch CPU index (smaller, faster than PyPI)
RUN pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 \
    --index-url https://download.pytorch.org/whl/cpu

# Install Python dependencies
COPY constraints.txt /app/constraints.txt
COPY requirements-api-slim.txt /app/requirements-api-slim.txt
ENV PIP_CONSTRAINT=/app/constraints.txt
RUN pip install --no-cache-dir -r /app/requirements-api-slim.txt

COPY . /app
COPY --from=frontend-build /frontend/dist /app/frontend/dist

EXPOSE 8080
ENV PORT=8080
CMD gunicorn --workers 1 --threads 4 --timeout 300 --graceful-timeout 60 --keep-alive 5 -b 0.0.0.0:${PORT} app:app
