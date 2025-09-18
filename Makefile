.PHONY: install-local restore-local

install-deps: ## Install poetry dependencies in all libs in one command
	python3 scripts/refresh_aion_deps.py

install-local: ## Install local aion dependencies in editable mode
	python3 scripts/install_local_aion_deps.py

restore-local: ## Restore original aion dependencies from pyproject.toml
	python3 scripts/restore_local_aion_deps.py
