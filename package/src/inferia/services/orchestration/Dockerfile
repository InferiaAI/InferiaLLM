FROM python:3.10-slim

# ---- system deps (SkyPilot requires these) ----
RUN apt-get update && apt-get install -y \
    git \
    curl \
    openssh-client \
    rsync \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# ---- environment ----
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV INFERIA_ENV=container
ENV POSTGRES_DSN=postgresql://inferia:inferia@inferia-postgres:5432/inferia

WORKDIR /app2

# ---- install python deps first (layer caching) ----
COPY Requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r Requirements.txt

# ---- copy source ----
COPY ./ ./orchestration

WORKDIR /app2/orchestration

# ---- entrypoint ----
CMD ["python", "-m", "app.main"]
