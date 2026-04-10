.PHONY: setup install doctor stale help

# Default target
.DEFAULT_GOAL := help

ORCHESTRATOR_HOME ?= $(HOME)/.config/orchestrator

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
	@[ -d "config/steps/contracts" ] && echo "  ✅ config/steps/contracts" || echo "  ⚠️  config/steps/contracts not yet created"
	@[ -d "agents" ] && echo "  ✅ agents" || echo "  ⚠️  agents not yet created"
	@[ -d "skills" ] && echo "  ✅ skills" || echo "  ⚠️  skills not yet created"
	@echo "Done."

stale: ## Detect stale/abandoned workflow state directories
	@echo "Scanning for stale workflow state..."
	@STALE_THRESHOLD=$${STALE_DAYS:-7}; \
	found=0; \
	for dir in $(ORCHESTRATOR_HOME)/changes/*/; do \
		[ -d "$$dir" ] || continue; \
		for state in "$$dir"*/state.yaml; do \
			[ -f "$$state" ] || continue; \
			change_dir=$$(dirname "$$state"); \
			change_id=$$(basename "$$change_dir"); \
			status=$$(grep '^status:' "$$state" 2>/dev/null | head -1 | awk '{print $$2}'); \
			if [ "$$status" = "active" ] || [ "$$status" = "paused" ]; then \
				mod_time=$$(stat -f %m "$$state" 2>/dev/null || stat -c %Y "$$state" 2>/dev/null); \
				now=$$(date +%s); \
				age_days=$$(( (now - mod_time) / 86400 )); \
				if [ "$$age_days" -ge "$$STALE_THRESHOLD" ]; then \
					repo=$$(basename "$$(dirname "$$change_dir")"); \
					echo "  ⚠️  $$repo/$$change_id — status: $$status, last modified: $${age_days}d ago"; \
					found=$$((found + 1)); \
				fi; \
			fi; \
		done; \
	done; \
	if [ "$$found" -eq 0 ]; then \
		echo "  ✅ No stale workflows found (threshold: $${STALE_THRESHOLD}d)"; \
	else \
		echo "  Found $$found stale workflow(s). To archive: /complete-feature <change-id>"; \
	fi
