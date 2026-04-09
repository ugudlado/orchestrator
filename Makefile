.PHONY: setup install doctor help

# Default target
.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[32m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Run install.sh to wire per-tool symlinks
	@bash ./install.sh

install: setup ## Alias for setup

doctor: ## Check all required tools and symlinks are in place
	@echo "Checking orchestrator health..."
	@[ -f "spec/project.yaml" ] && echo "  ✅ spec/project.yaml" || echo "  ❌ spec/project.yaml missing"
	@[ -f "install.sh" ] && echo "  ✅ install.sh" || echo "  ⚠️  install.sh not yet created"
	@[ -d "config/workflows" ] && echo "  ✅ config/workflows" || echo "  ⚠️  config/workflows not yet created"
	@[ -d "config/steps" ] && echo "  ✅ config/steps" || echo "  ⚠️  config/steps not yet created"
	@[ -d "agents" ] && echo "  ✅ agents" || echo "  ⚠️  agents not yet created"
	@[ -d "skills" ] && echo "  ✅ skills" || echo "  ⚠️  skills not yet created"
	@echo "Done."
