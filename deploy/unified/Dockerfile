# --- Stage 1: Node.js Sidecar Builder ---
FROM node:20-slim AS node-builder
WORKDIR /app/sidecar
COPY package/src/inferia/services/orchestration/app/services/depin-sidecar/package*.json ./
RUN npm install --production

# --- Stage 2: Final Unified Image ---
FROM python:3.10-slim

# 1. Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    supervisor \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Install Python dependencies (Optimized)
COPY package/src/inferia/services/filtration/requirements.txt /tmp/f_reqs.txt
COPY apps/filtration-gateway/requirements.txt /tmp/fg_reqs.txt
COPY apps/inference-gateway/requirements.txt /tmp/ig_reqs.txt
COPY package/src/inferia/services/orchestration/Requirements.txt /tmp/o_reqs.txt
COPY apps/orchestration-gateway/requirements.txt /tmp/og_reqs.txt

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/f_reqs.txt && \
    pip install --no-cache-dir -r /tmp/fg_reqs.txt && \
    pip install --no-cache-dir -r /tmp/ig_reqs.txt && \
    pip install --no-cache-dir -r /tmp/o_reqs.txt && \
    pip install --no-cache-dir -r /tmp/og_reqs.txt && \
    pip install --no-cache-dir "pydantic[email]" email-validator && \
    python -m spacy download en_core_web_sm && \
    rm -rf /root/.cache/pip

# 3. Copy package separately and install it to provide the 'inferia' command
COPY package /app/package
RUN pip install -e /app/package

# 4. Copy rest of the code
COPY . .

# 5. Copy Node.js sidecar from builder
COPY --from=node-builder /app/sidecar/node_modules /app/package/src/inferia/services/orchestration/app/services/depin-sidecar/node_modules

# 5. Final Configuration
ENV PYTHONPATH=/app/package/src/inferia/services/filtration:/app/package/src/inferia/services/orchestration/app:/app/apps/inference-gateway:/app
RUN mkdir -p /var/log/supervisor

# 6. Entrypoint
CMD ["/usr/bin/supervisord", "-c", "/app/deploy/unified/supervisord.conf"]
