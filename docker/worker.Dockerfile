FROM python:3.12-slim

ARG ONNXRUNTIME_PACKAGE=onnxruntime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY worker/requirements.txt worker/requirements.txt
RUN grep -v '^onnxruntime' worker/requirements.txt > /tmp/worker-requirements.txt \
    && pip install --no-cache-dir "$ONNXRUNTIME_PACKAGE" -r /tmp/worker-requirements.txt

COPY shared shared
COPY worker worker
COPY config.yaml config.yaml

ENV PYTHONUNBUFFERED=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
CMD ["python", "-m", "worker.app.main"]
