.PHONY: install-local restore-local

install-local: ## Install local aion dependencies in editable mode
	python3 scripts/install_local_aion_deps.py

restore-local: ## Restore original aion dependencies from pyproject.toml
	python3 scripts/restore_local_aion_deps.py
