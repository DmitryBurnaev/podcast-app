.DEFAULT_GOAL := help

APP_PLATFORM ?= linux/amd64
PLATFORM_ARCH = $(lastword $(subst /, ,$(APP_PLATFORM)))
CI_SERVICE_IMAGE = podcast-app:ci-$(PLATFORM_ARCH)
CI_TEST_IMAGE = podcast-app:test-$(PLATFORM_ARCH)
CI_COMPOSE = APP_PLATFORM=$(APP_PLATFORM) DOCKER_IMAGE=$(CI_SERVICE_IMAGE) TEST_DOCKER_IMAGE=$(CI_TEST_IMAGE) docker compose --env-file .github/ci.env

MAKEFILE_TARGET := $(filter-out ai-client --help Makefile,$(MAKECMDGOALS))

.PHONY: help
help: ## This help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*? / {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: lint
lint: ## Linting project
	@echo Linting...
	uv run ruff check
	uv run mypy .

.PHONY: format
format: ## Apply formatting using black
	@echo Formatting...
	uv run black .
	uv run ruff check --fix

.PHONY: install
install: ## Install dependencies
	@echo Installing dependencies...
	uv venv --clear
	uv sync

.PHONY: deps
deps: ## Update dependencies
	@echo Updating dependencies...
	uv lock --upgrade
	uv sync --reinstall

.PHONY: run
run: .env ## Run app
	@echo Run project...
	uv run python -m src.main

.PHONY: run-worker
run-worker: .env ## Run app
	@echo Run background worker process...
	uv run python -m src.worker

.PHONY: run-in-docker
run-in-docker: .env ## Run app
	@echo Run project in container...
	docker compose --profile service up --build

.PHONY: test
test: .env ## Run tests with coverage report
	@echo Test project with coverage...
	uv run coverage run -m pytest -v
	uv run coverage report
	rm .coverage

.PHONY: test-frontend
test-frontend: ## Run frontend JavaScript tests
	@echo Test frontend JavaScript...
	node --test src/tests/frontend/app.test.cjs src/tests/frontend/http.test.cjs

.PHONY: test-in-docker
test-in-docker: .env  ## Run tests inside docker container
	@echo Run project in container...
	docker compose --profile test up --build --abort-on-container-exit --exit-code-from test test

.PHONY: lint-in-docker
lint-in-docker: .env  ## Run linting inside docker container
	@echo Run project in container...
	docker compose run --rm --no-deps --build lint

.PHONY: build-platform
build-platform: ## Build service and test images for APP_PLATFORM (default linux/amd64)
	docker buildx build --platform $(APP_PLATFORM) --target service --tag $(CI_SERVICE_IMAGE) --load .
	docker buildx build --platform $(APP_PLATFORM) --target tests --build-arg DEV_DEPS=true --tag $(CI_TEST_IMAGE) --load .

.PHONY: check-platform
check-platform: ## Verify and test previously built APP_PLATFORM images using CI-only configuration
	test "$$(docker image inspect --format '{{.Os}}/{{.Architecture}}' $(CI_SERVICE_IMAGE))" = "$(APP_PLATFORM)"
	test "$$(docker image inspect --format '{{.Os}}/{{.Architecture}}' $(CI_TEST_IMAGE))" = "$(APP_PLATFORM)"
	docker run --rm --platform $(APP_PLATFORM) --entrypoint ffmpeg $(CI_SERVICE_IMAGE) -version
	docker run --rm --platform $(APP_PLATFORM) --entrypoint ffprobe $(CI_SERVICE_IMAGE) -version
	$(CI_COMPOSE) --profile test up --detach --wait --wait-timeout 120 db redis
	$(CI_COMPOSE) run --rm --no-deps --pull never test

.PHONY: migration
migration: .env ## Make DB migrations
	@read -p "Revision: " db_revision; \
	uv run alembic revision --autogenerate -m "$$db_revision"

.PHONY: migrations-upgrade
migrate: .env ## Apply DB migrations
	@echo Migrations: apply revisions...
	uv run alembic upgrade head

.PHONY: migrations
downgrade: .env ## Downgrade (unapply) DB migration (last revision)
	@echo Migrations: downgrade last revisions...
	uv run alembic downgrade -1

.PHONY: secrets
secrets: .env ## Create new secrets
	@echo Encryption: creating new key
	uv run python -m src.modules.cli.generate_secrets
