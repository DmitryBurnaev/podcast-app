.DEFAULT_GOAL := help

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

.PHONY: run
run-in-docker: .env ## Run app
	@echo Run project in container...
	docker compose up app --build

.PHONY: test
test: .env ## Run tests with coverage report
	@echo Test project with coverage...
	uv run coverage run -m pytest -v
	uv run coverage report
	rm .coverage

.PHONY: run
test-in-docker: .env  ## Run tests inside docker container
	@echo Run project in container...
	docker compose up test --build

.PHONY: run
lint-in-docker: .env  ## Run linting inside docker container
	@echo Run project in container...
	docker compose up lint --build

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
