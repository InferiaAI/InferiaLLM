# InferiaLLM Makefile

.PHONY: setup start test clean docker-build-unified docker-build-split docker-up-unified docker-up-split docker-down docker-clean

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

DOCKER_COMPOSE = docker compose -f docker/docker-compose.yml

# Build images
docker-build-unified:
	$(DOCKER_COMPOSE) --profile unified build

docker-build-split:
	$(DOCKER_COMPOSE) --profile split build

# Run services
docker-up-unified:
	$(DOCKER_COMPOSE) --profile unified up -d

docker-up-split:
	$(DOCKER_COMPOSE) --profile split up -d

# Stop services
docker-down:
	$(DOCKER_COMPOSE) --profile unified --profile split down

# Clean up docker (volumes, orphans)
docker-clean:
	$(DOCKER_COMPOSE) --profile unified --profile split down -v --remove-orphans
