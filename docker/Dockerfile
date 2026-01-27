# syntax=docker/dockerfile:1

# ==========================================
# Stage 1: Base Python Environment
# ==========================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV INFERIA_ENV=container

RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ==========================================
# Stage 2: Node.js Builder (for Sidecars)
# ==========================================
FROM node:20-slim AS node-builder

WORKDIR /build/depin-sidecar

# Copy package files first for better caching
COPY package/src/inferia/services/orchestration/app/services/depin-sidecar/package*.json ./
RUN npm install

# Copy source and build
COPY package/src/inferia/services/orchestration/app/services/depin-sidecar/ ./
RUN npm run build

# ==========================================
# Stage 3: Python Package Builder
# ==========================================
FROM base AS python-builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy package definition
COPY package/ /app/package/

WORKDIR /app/package

# 1. Install CPU-only Torch first to prevent downloading the 2GB CUDA version
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 2. Install the package WITHOUT [all-providers] (removes AWS/GCP/Azure SDKs)
#    We install with --no-deps for torch to avoid overwriting the CPU version
RUN pip install --no-cache-dir .

# Final Cleanup of build artifacts
RUN find /usr/local/lib/python3.12/site-packages -name "*.pyc" -delete && \
    find /usr/local/lib/python3.12/site-packages -name "__pycache__" -delete

# Copy entrypoint script
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["entrypoint.sh"]

# ==========================================
# Stage 4: Filtration Service
# ==========================================
FROM python-builder AS filtration
LABEL service="filtration"
EXPOSE 8000
CMD ["inferiallm", "start", "filtration"]

# ==========================================
# Stage 5: Inference Service
# ==========================================
FROM python-builder AS inference
LABEL service="inference"
EXPOSE 8001
CMD ["inferiallm", "start", "inference"]

# ==========================================
# Stage 6: Orchestration Service (Python + Node)
# ==========================================
FROM python-builder AS orchestration
LABEL service="orchestration"

# Install Node.js runtime
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy built sidecar from node-builder
COPY --from=node-builder /build/depin-sidecar /app/package/src/inferia/services/orchestration/app/services/depin-sidecar

EXPOSE 8080 3000 50051
CMD ["inferiallm", "start", "orchestration"]

# ==========================================
# Stage 7: Unified Image (Everything)
# ==========================================
FROM orchestration AS unified
LABEL service="unified"

# Essential runtime assets (only those not in the python package)
WORKDIR /app
COPY assets /app/assets

EXPOSE 8000 8001 8080 3000 3001 50051
CMD ["inferiallm", "start"]
