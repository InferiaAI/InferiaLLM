# Orchestration Gateway

The Orchestration Gateway provides compute pool management, model deployment, and inventory management services.

## Features

- **REST API** (port 8080): Deployment and inventory management endpoints
- **gRPC API** (port 50051): Compute pool and model registry services

## Running

### Development

```bash
# From this directory
python app.py

# Or with uvicorn
uvicorn app:app --port 8080 --reload
```

### With orchestrator script

```bash
# From this directory
./orchestrator.sh start
```

## Environment Variables

See `.env.example` for all configuration options.

| Variable | Description | Default |
|----------|-------------|---------|
| `HTTP_PORT` | HTTP server port | 8080 |
| `GRPC_PORT` | gRPC server port | 50051 |
| `POSTGRES_DSN` | PostgreSQL connection string | `postgresql://...` |
| `REDIS_HOST` | Redis host | localhost |

## Endpoints

- `GET /health` - Health check
- `POST /deployment/deploy` - Create deployment
- `GET /deployment/deployments` - List all deployments
- `GET /deployment/listPools/{owner_id}` - List compute pools
- `POST /inventory/heartbeat` - Node heartbeat
