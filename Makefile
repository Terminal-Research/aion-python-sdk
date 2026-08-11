.DEFAULT_GOAL := help

.PHONY: help tests deps-install deps-lock deps-lock-regenerate deps-sync deps-set-branch deps-set-local deps-set-local-revert deps-use-local deps-use-remote deps-verify deps-verify-clean

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

tests: ## Run tests for all libs (make tests ARGS="aion-sdk -- -k platform_link")
	./scripts/tests.py $(ARGS)

deps-install: ## Install dependencies from lock files for all packages
	./scripts/deps/install.py

deps-lock: ## Update lock files for all packages (incremental)
	./scripts/deps/lock.py

deps-lock-regenerate: ## Regenerate lock files from scratch (ignores existing locks)
	./scripts/deps/lock.py --regenerate

deps-sync: ## Sync dependencies with lock files (removes unlocked packages)
	./scripts/deps/sync.py

deps-set-branch: ## Update git branch references (usage: make deps-set-branch BRANCH=features/my-branch)
	@if [ -z "$(BRANCH)" ]; then \
		echo "Error: BRANCH variable is required"; \
		echo "Usage: make deps-set-branch BRANCH=features/my-branch"; \
		exit 1; \
	fi
	./scripts/deps/set-branch.py $(BRANCH)

deps-set-local: ## Switch to local path dependencies (edits pyproject.toml only)
	./scripts/deps/set-local.py apply

deps-set-local-revert: ## Revert to original dependencies (edits pyproject.toml only)
	./scripts/deps/set-local.py revert

deps-use-local: ## Develop locally: switch to local paths, then lock, sync and verify
	./scripts/deps/set-local.py apply
	./scripts/deps/lock.py
	./scripts/deps/sync.py
	./scripts/deps/verify.py --clean

deps-use-remote: ## Return to git dependencies: revert, then lock, sync and verify
	./scripts/deps/set-local.py revert
	./scripts/deps/lock.py
	./scripts/deps/sync.py
	./scripts/deps/verify.py

deps-verify: ## Check that packages resolve to the working tree as declared
	./scripts/deps/verify.py

deps-verify-clean: ## Same as deps-verify, plus delete leftover .venv/src clones
	./scripts/deps/verify.py --clean
