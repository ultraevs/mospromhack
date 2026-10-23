FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openvpn iproute2 dnsutils curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests websocket-client aiohttp httpx websockets
RUN pip install --no-cache-dir pika protobuf "numpy<2" pyyaml "opencv-python-headless==4.10.0.84" pydantic pydantic-settings

COPY linq-vpn-entry.sh /usr/local/bin/linq-vpn-entry.sh
RUN chmod +x /usr/local/bin/linq-vpn-entry.sh
WORKDIR /repo
ENTRYPOINT ["/usr/local/bin/linq-vpn-entry.sh"]
CMD ["bash"]
