FROM python:3.12.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# torch с cpu-индекса, иначе pip тянет cuda на пару гигов
RUN pip install --no-cache-dir torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu

WORKDIR /sdk
COPY . /sdk/packets
RUN pip install --no-cache-dir -U pip \
    && cd /sdk/packets && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "opencv-python-headless==4.10.0.84" pyyaml "numpy==1.26.4"
RUN pip install --no-cache-dir pydantic pydantic-settings "numpy==1.26.4"

ENV PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES=-1
WORKDIR /repo
CMD ["bash"]
