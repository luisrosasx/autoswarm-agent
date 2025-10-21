# syntax=docker/dockerfile:1.7
FROM python:3.12-alpine

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV AUTOSWARM_DOKPLOY_URL=http://dokploy:3000 \
    AUTOSWARM_TRAEFIK_NETWORK=traefik-public \
    AUTOSWARM_RECONCILE_INTERVAL=60

CMD ["python", "src/autoswarm.py"]
