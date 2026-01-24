FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY package/src/inferia/services/filtration/requirements.txt /app/package/src/inferia/services/filtration/requirements.txt
COPY apps/filtration-gateway/requirements.txt /app/apps/filtration-gateway/requirements.txt

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/package/src/inferia/services/filtration/requirements.txt \
    && pip install --no-cache-dir -r /app/apps/filtration-gateway/requirements.txt

# Copy source code
COPY package/src/inferia/services/filtration /app/package/src/inferia/services/filtration
COPY apps/filtration-gateway /app/apps/filtration-gateway

# Set python path
ENV PYTHONPATH=/app/package/src/inferia/services/filtration

# Expose port
EXPOSE 8000

# Run the application
WORKDIR /app
CMD ["python", "apps/filtration-gateway/app.py"]
