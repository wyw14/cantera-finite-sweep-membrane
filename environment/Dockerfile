FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-lock.txt /app/requirements-lock.txt
COPY wheels/ /opt/wheels/
COPY wheels-sha256.txt /opt/wheels/SHA256SUMS
RUN cd /opt/wheels && sha256sum -c SHA256SUMS && \
    python -m pip install --no-index --no-deps --find-links /opt/wheels -r /app/requirements-lock.txt && \
    python -m pip check
COPY src/ /app/src/
COPY upstream/ /app/src/upstream/
ENV PYTHONPATH=/app/src
