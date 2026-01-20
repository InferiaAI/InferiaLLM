FROM python:3.10-slim

WORKDIR /app

# ---- inputs ----
COPY apps/inference-gateway/requirements.txt /app/apps/inference-gateway/requirements.txt

# ---- install deps ----
RUN pip install --upgrade pip \
    && pip install -r /app/apps/inference-gateway/requirements.txt

# ---- copy source ----
COPY apps/inference-gateway /app/apps/inference-gateway

# ---- setup python path ----
ENV PYTHONPATH=/app/apps/inference-gateway

# ---- entrypoint ----
WORKDIR /app/apps/inference-gateway
# app.py uses uvicorn in __main__? No, app.py in inference gateway is a FastAPI app object.
# checking file content again... it doesn't have if __main__ block for uvicorn run.
# So we need to run uvicorn explicitly.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
