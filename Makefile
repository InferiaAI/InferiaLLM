
# InferiaLLM Makefile

.PHONY: setup start test clean docker-up docker-down

# Setup the project (env, dependencies, init)
setup:
	@./setup_project.sh

# Start the API services using the installed CLI
start:
	@echo "Starting InferiaLLM API..."
	inferia api-start

# Run tests
test:
	pytest

# Clean up
clean:
	rm -rf .venv
	find . -type d -name "__pycache__" -exec rm -rf {} +

# Docker helpers
docker-up:
	cd deploy && docker compose up -d

docker-down:
	cd deploy && docker compose down
