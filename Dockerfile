FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-api.txt /app/requirements-api.txt
RUN pip install --no-cache-dir --default-timeout=600 -r /app/requirements-api.txt

COPY . /app

EXPOSE 8080
ENV PORT=8080
CMD gunicorn -b 0.0.0.0:${PORT} app:app
