.DEFAULT_GOAL := help

.PHONY: help deps-install-dev deps-install deps-lock deps-lock-regenerate deps-sync

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

deps-install-dev: ## Install local dependencies in editable mode for development
	./scripts/deps/install-dev.py

deps-install: ## Install dependencies from lock files for all packages
	./scripts/deps/install.py

deps-lock: ## Update lock files for all packages (incremental)
	./scripts/deps/lock.py

deps-lock-regenerate: ## Regenerate lock files from scratch (ignores existing locks)
	./scripts/deps/lock.py --regenerate

deps-sync: ## Sync dependencies with lock files (removes unlocked packages)
	./scripts/deps/sync.py
