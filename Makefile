# InferiaLLM Makefile

.PHONY: setup start test clean docker-build-unified docker-up-unified docker-down docker-clean

# Setup the project (env, dependencies, init)
setup:
	@./setup_project.sh

# Start the API services using the installed CLI
start:
	@echo "Starting InferiaLLM API..."
	inferiallm start

# Run tests
test:
	pytest

# Clean up
clean:
	rm -rf .venv
	find . -type d -name "__pycache__" -exec rm -rf {} +

# ==========================================
# Docker Commands
# ==========================================

# There is ONE canonical compose: the root ./docker-compose.yml (project:
# deploy) — app + postgres + redis. Topology and port
# variants are env-driven on this single file (see .env: APP_PORT, HTTP_PORT,
# GRPC_PORT, DEPIN_SIDECAR_PORT, and the AUTH_PROVIDER / EXTERNAL_AUTH_* block
# for external SSO). There is no separate split / localhost / SSO compose.
DOCKER_COMPOSE = docker compose -f docker-compose.yml

# Build images
docker-build-unified:
	$(DOCKER_COMPOSE) build

# Run services
docker-up-unified:
	$(DOCKER_COMPOSE) up -d

# Stop services
docker-down:
	$(DOCKER_COMPOSE) down

# Clean up docker (volumes, orphans)
docker-clean:
	$(DOCKER_COMPOSE) down -v --remove-orphans

.PHONY: smoke-local smoke-local-up smoke-local-down smoke-aws smoke-aws-dry

smoke-local-up:    ## bring up unified stack and build worker image (no worker container yet)
	docker compose -f docker-compose.yml up -d
	docker build -t inferia-worker:smoke ../inferia-worker

smoke-local-down:  ## tear down the smoke worker container + unified stack
	-docker rm -f inferia-worker
	-docker volume rm worker-state-local
	docker compose -f docker-compose.yml down

smoke-local: smoke-local-up   ## run the local Qwen3 smoke end-to-end
	python -m scripts.smoke.local

smoke-aws-dry:     ## AWS smoke pre-flight only (no spend)
	python -m scripts.smoke.aws --dry-run

smoke-aws:         ## real EC2 AWS smoke; hard 20-min wall clock
	timeout 1200 python -m scripts.smoke.aws --instance-type=g4dn.xlarge
