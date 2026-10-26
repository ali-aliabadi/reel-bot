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

.PHONY: models
KOKORO_URL := https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0

models: ## download the Whisper (captions) and Kokoro (reading guide) models into models/ (run once; about 270 MB)
	uv run python -c "from faster_whisper import download_model; from reel_bot.captions_write import MODEL, MODEL_DIR; download_model(MODEL, output_dir=str(MODEL_DIR))"
	mkdir -p models/kokoro
	cd models/kokoro && for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do [ -f $$f ] || { curl -fsSL -o $$f.part $(KOKORO_URL)/$$f && mv $$f.part $$f; } || exit 1; done
	cd models/kokoro && printf '%s\n' \
		'6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb  kokoro-v1.0.int8.onnx' \
		'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d  voices-v1.0.bin' | shasum -a 256 -c

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
