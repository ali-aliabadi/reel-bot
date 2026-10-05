# reel-bot's single entry point for setup, lint and test. CI runs the same targets.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

TOOLS := $(CURDIR)/bin/tools

# Pinned tool versions. Python tools are pinned in uv.lock; bump these deliberately
# (a new gitleaks version also needs its checksums in scripts/install-gitleaks.sh).
GITLEAKS_VERSION := v8.30.1

export PATH := $(TOOLS):$(PATH)

.PHONY: help
help: ## list targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

.PHONY: tools
tools: ## install Python, the locked dev tools and gitleaks (run once)
	uv sync --locked
	scripts/install-gitleaks.sh $(GITLEAKS_VERSION) $(TOOLS)

.PHONY: run
run: ## run the CLI (reads .env if present), e.g. make run ARGS=version
	set -a; [ -f .env ] && . ./.env; set +a; uv run reel-bot $(ARGS)

.PHONY: fmt
fmt: ## format and auto-fix with ruff
	uv run ruff format .
	uv run ruff check --fix .

.PHONY: fmt-check
fmt-check: ## fail if any file needs formatting
	uv run ruff format --check .

.PHONY: lint
lint: ## ruff, pyright, file length, lockfile, actionlint
	uv run ruff check .
	uv run pyright
	scripts/check-file-length.sh
	uv lock --check
	uv run actionlint

.PHONY: test
test: ## unit and integration tests with coverage
	uv run pytest --cov --cov-report=term-missing:skip-covered

.PHONY: sec
sec: ## pip-audit and gitleaks
	uv export --locked --format requirements.txt --no-emit-project --quiet | uv run pip-audit --strict --disable-pip -r /dev/stdin
	gitleaks git --redact --no-banner .

.PHONY: check
check: fmt-check lint test sec ## everything to run before a commit

.PHONY: build
build: ## build the wheel into ./dist
	uv build
