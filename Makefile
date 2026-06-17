# InferiaLLM Makefile

.PHONY: setup start test clean docker-build-unified docker-build-split docker-up-unified docker-up-split docker-down docker-clean docker-up-sso docker-down-sso docker-logs-sso docker-build-sso

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

# The PRIMARY compose is the root ./docker-compose.yml (project: deploy) — the
# canonical single-file unified stack (app + postgres + redis + ES/Logstash/
# Kibana) that we actually run and deploy. Split mode is a dev-only topology
# kept in the docker/ profiles file.
DOCKER_COMPOSE = docker compose -f docker-compose.yml
DOCKER_COMPOSE_SPLIT = docker compose -f docker/docker-compose.profiles.yml

# Build images
docker-build-unified:
	$(DOCKER_COMPOSE) build

docker-build-split:
	$(DOCKER_COMPOSE_SPLIT) --profile split build

# Run services
docker-up-unified:
	$(DOCKER_COMPOSE) up -d

docker-up-split:
	$(DOCKER_COMPOSE_SPLIT) --profile split up -d

# Stop services (tear down whichever topology is up; '-' ignores "not running")
docker-down:
	-$(DOCKER_COMPOSE) down
	-$(DOCKER_COMPOSE_SPLIT) --profile split down

# Clean up docker (volumes, orphans)
docker-clean:
	-$(DOCKER_COMPOSE) down -v --remove-orphans
	-$(DOCKER_COMPOSE_SPLIT) --profile split down -v --remove-orphans

# ==========================================
# SSO Topology (InferiaLLM + inferia-auth + Caddy)
# ==========================================
# Self-contained compose at docker-compose.sso.yml. Requires the
# sibling repo at ../inferia-auth/ (relative to this directory). Operator
# must add `inferia.local` and `auth.inferia.local` to /etc/hosts pointing
# at 127.0.0.1 before bringing the stack up. See docs/operations/auth.md.
DOCKER_COMPOSE_SSO = docker compose -f docker/docker-compose.sso.yml

docker-build-sso:
	$(DOCKER_COMPOSE_SSO) build

docker-up-sso:
	$(DOCKER_COMPOSE_SSO) up --build -d

docker-down-sso:
	$(DOCKER_COMPOSE_SSO) down

docker-logs-sso:
	$(DOCKER_COMPOSE_SSO) logs -f --tail=100

.PHONY: smoke-local smoke-local-up smoke-local-down smoke-aws smoke-aws-dry

smoke-local-up:    ## bring up unified stack and build worker image (no worker container yet)
	docker compose -f docker-compose.yml up -d
	docker build -t inferia-worker:smoke ../inferia-worker

smoke-local-down:  ## tear down worker compose + unified
	-docker compose -f docker/compose.worker-local.yml down -v
	docker compose -f docker-compose.yml down

smoke-local: smoke-local-up   ## run the local Qwen3 smoke end-to-end
	python -m scripts.smoke.local

smoke-aws-dry:     ## AWS smoke pre-flight only (no spend)
	python -m scripts.smoke.aws --dry-run

smoke-aws:         ## real EC2 AWS smoke; hard 20-min wall clock
	timeout 1200 python -m scripts.smoke.aws --instance-type=g4dn.xlarge
