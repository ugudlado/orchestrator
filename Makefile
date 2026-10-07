.PHONY: help test

# Default target
.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[32m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

test: ## Run orchestrator_next unit tests
	@.venv/bin/python -m pytest orchestrator_next/tests -q
