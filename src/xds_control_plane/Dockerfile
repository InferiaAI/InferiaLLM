FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY xds_shim.py .

ENV PORT=18000 \
    NODES_FILE=/data/nodes.json \
    XDS_CLUSTER_NAME=xds_cluster \
    CONTROL_PLANE_URL=

EXPOSE 18000
CMD ["python3", "xds_shim.py"]
