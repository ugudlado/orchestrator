.PHONY: setup install install-cli doctor stale help test lint-contracts dashboard dashboard-stop

# Default target
.DEFAULT_GOAL := help

ORCHESTRATOR_HOME ?= $(HOME)/.config/orchestrator

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[32m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Full install: CLI on PATH (~/.local/bin), ORCHESTRATOR_HOME, agent symlinks
	@bash ./install.sh

install: setup ## Alias for setup (same as make setup)

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
	@python3 -m unittest discover -s orchestrator_next/tests

lint-contracts: ## HL-287 M2: every step contract must declare inputs: and outputs:
	@missing=0; \
	for f in config/steps/*.yaml; do \
		grep -q "^inputs:" "$$f" || { echo "  ❌ $$f missing inputs:"; missing=$$((missing + 1)); }; \
		grep -q "^outputs:" "$$f" || { echo "  ❌ $$f missing outputs:"; missing=$$((missing + 1)); }; \
	done; \
	if [ "$$missing" -eq 0 ]; then \
		echo "  ✅ All contracts declare inputs: and outputs:"; \
	else \
		echo "  $$missing contract(s) fail M2 lint"; exit 1; \
	fi


dashboard: ## Launch live agent-progress dashboard on http://localhost:8765
	@if [ ! -x ui/dashboard/.venv/bin/uvicorn ]; then \
		echo "Creating dashboard venv..."; \
		python3 -m venv ui/dashboard/.venv; \
		ui/dashboard/.venv/bin/pip install --quiet fastapi 'uvicorn[standard]' duckdb pyyaml; \
	fi
	@if lsof -ti tcp:8765 >/dev/null 2>&1; then \
		echo "Dashboard already running on :8765 (use 'make dashboard-stop' to stop)"; \
	else \
		echo "Starting dashboard on http://localhost:8765 ..."; \
		nohup ui/dashboard/run.sh > /tmp/orchestrator-dashboard.log 2>&1 & \
		sleep 1; \
		echo "  log: /tmp/orchestrator-dashboard.log"; \
	fi

dashboard-stop: ## Stop the live dashboard server
	@if lsof -ti tcp:8765 >/dev/null 2>&1; then \
		lsof -ti tcp:8765 | xargs kill 2>/dev/null && echo "Stopped."; \
	else \
		echo "Dashboard not running."; \
	fi
