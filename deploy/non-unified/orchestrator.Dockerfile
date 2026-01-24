FROM python:3.10-slim

# ---- system deps ----
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- inputs ----
# We need services/orchestration as a dependency
COPY package/src/inferia/services/orchestration/Requirements.txt /app/package/src/inferia/services/orchestration/Requirements.txt
COPY apps/orchestration-gateway/requirements.txt /app/apps/orchestration-gateway/requirements.txt

# ---- install deps ----
RUN pip install --upgrade pip \
    && pip install -r /app/package/src/inferia/services/orchestration/Requirements.txt \
    && pip install -r /app/apps/orchestration-gateway/requirements.txt

# ---- copy source ----
COPY package/src/inferia/services/orchestration /app/package/src/inferia/services/orchestration
COPY apps/orchestration-gateway /app/apps/orchestration-gateway

# ---- install sidecar deps ----
WORKDIR /app/package/src/inferia/services/orchestration/app/services/depin-sidecar
RUN npm install

# ---- setup python path ----
# Orchestration gateway needs to import from services/orchestration/app (see app.py sys.path patch)
# But we can also set PYTHONPATH to make it cleaner
ENV PYTHONPATH=/app/package/src/inferia/services/orchestration/app:/app

# ---- entrypoint ----
WORKDIR /app
# app.py expects to be run directly or via uvicorn
CMD ["python", "apps/orchestration-gateway/run_docker_stack.py"]
