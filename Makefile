.PHONY: setup install install-cli use-local doctor stale help test

# Default target
.DEFAULT_GOAL := help

ORCHESTRATOR_HOME ?= $(HOME)/.config/orchestrator

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[32m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Full install: CLI on PATH (~/.local/bin), ORCHESTRATOR_HOME, agent symlinks
	@bash ./install.sh

install: setup ## Alias for setup (same as make setup)

use-local: ## Point ORCHESTRATOR_HOME at this repo (run from any repo with a config/ dir)
	@PROFILE=$${SHELL_PROFILE:-$(HOME)/.zshrc}; \
	MARKER="export ORCHESTRATOR_HOME="; \
	if grep -qF "$$MARKER" "$$PROFILE" 2>/dev/null; then \
		sed -i.bak "s|^export ORCHESTRATOR_HOME=.*|export ORCHESTRATOR_HOME=\"$(CURDIR)\"|" "$$PROFILE"; \
		echo "  Updated ORCHESTRATOR_HOME=$(CURDIR) in $$PROFILE"; \
	else \
		echo "" >> "$$PROFILE"; \
		echo "export ORCHESTRATOR_HOME=\"$(CURDIR)\"" >> "$$PROFILE"; \
		echo "  Added ORCHESTRATOR_HOME=$(CURDIR) to $$PROFILE"; \
	fi; \
	echo "  Run: source $$PROFILE"

install-cli: ## Symlink orchestrator into ~/.local/bin only (no agent/skill wiring)
	@mkdir -p "$(HOME)/.local/bin"
	@ln -sf "$(CURDIR)/bin/orchestrator" "$(HOME)/.local/bin/orchestrator"
	@echo "  linked $(HOME)/.local/bin/orchestrator -> $(CURDIR)/bin/orchestrator"
	@echo "  ensure ~/.local/bin is on PATH (make install adds it to your shell profile)"

doctor: ## Run unified orchestrator health check (orchestrator_next.doctor)
	@PYTHONPATH="$(CURDIR):$$PYTHONPATH" python3 -m orchestrator_next.doctor

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

test: ## Run orchestrator_next unit tests
	@poetry run pytest -q

