.DEFAULT_GOAL := help

# The disposable database the integration tests migrate and truncate. It is
# deliberately not port 5432: those tables are dropped and refilled, and a
# local development database must never be the one that happens to answer.
PG_TEST_CONTAINER := aion-db-test
PG_TEST_PORT := 54329
PG_TEST_URL := postgresql://postgres:postgres@localhost:$(PG_TEST_PORT)/postgres

# A named variable rather than the ordinary connection setting, because these
# tests destroy what they find. An address must be given the meaning "this
# database may be wiped" before anything wipes it.
#
# Defaulted, not required: the container above is what it names, and
# `pg-test-ready` starts it. Set it in the environment - in CI, or for a
# PostgreSQL of your own - and nothing here touches Docker at all.
POSTGRES_TEST_URL ?= $(PG_TEST_URL)

# Read once, here, before anything below gets a chance to rebind the variable
# for one target's recipe. A target-specific `export ... :=` (used below to
# keep the variable out of plain `make tests`) creates its own binding with
# origin "file" inside that recipe, so asking $(origin POSTGRES_TEST_URL)
# inside with_pg_test itself would always answer "file" - this is computed
# where the only binding in scope is still the real one.
POSTGRES_TEST_URL_IS_EXTERNAL := $(filter environment command line,$(origin POSTGRES_TEST_URL))

.PHONY: help tests tests-integration tests-all pg-test-up pg-test-down deps-install deps-lock deps-lock-regenerate deps-sync deps-set-branch deps-set-local deps-set-local-revert deps-use-local deps-use-remote deps-verify deps-verify-clean

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

tests: ## Run unit tests for all libs (make tests ARGS="aion-sdk -- -k platform_link")
	./scripts/tests.py $(ARGS)

# Run a command with a database under it, and take the database away again.
#
# The container is started before the command and stopped after it, whatever
# the command's outcome - the exit status is carried across the teardown, so a
# failing suite still fails the target. Set PG_TEST_KEEP=1 to leave the
# container up between runs while debugging one.
#
# An externally supplied POSTGRES_TEST_URL means someone else owns the
# database - CI, or a PostgreSQL of your own - and then nothing here touches
# Docker at all. Checked by origin, not by value: a value that happens to
# match the default is still someone else's database if it came from the
# environment, and string equality cannot tell the two apart.
define with_pg_test
	if [ -n "$(POSTGRES_TEST_URL_IS_EXTERNAL)" ]; then \
		echo "[pg] using POSTGRES_TEST_URL from the environment"; \
		$(1); \
	else \
		$(MAKE) --no-print-directory pg-test-up || exit 1; \
		$(1); status=$$?; \
		if [ -n "$(PG_TEST_KEEP)" ]; then \
			echo "[pg] $(PG_TEST_CONTAINER) left running (PG_TEST_KEEP)"; \
		else \
			$(MAKE) --no-print-directory pg-test-down; \
		fi; \
		exit $$status; \
	fi
endef

# The variable is exported only for the two targets that run against it. A
# global export would hand the default container's address to plain `make
# tests`, and a test module that checks the variable's presence rather than
# asking scripts/tests.py's marker filter - the belt to that suite's braces -
# would see a database that was never started.
tests-integration: export POSTGRES_TEST_URL := $(POSTGRES_TEST_URL)
tests-integration: ## Run integration tests; run before you commit
	@$(call with_pg_test,./scripts/tests.py --integration $(ARGS))

tests-all: export POSTGRES_TEST_URL := $(POSTGRES_TEST_URL)
tests-all: ## Run unit and integration tests together
	@$(call with_pg_test,./scripts/tests.py --all $(ARGS))

pg-test-up: ## Start the disposable PostgreSQL and wait for it
	@if docker exec $(PG_TEST_CONTAINER) pg_isready -U postgres >/dev/null 2>&1; then \
		echo "[pg] $(PG_TEST_CONTAINER) is already running"; \
		exit 0; \
	fi; \
	docker rm -f $(PG_TEST_CONTAINER) >/dev/null 2>&1 || true; \
	docker run --rm --name $(PG_TEST_CONTAINER) -e POSTGRES_PASSWORD=postgres \
		-p 127.0.0.1:$(PG_TEST_PORT):5432 -d postgres:16 >/dev/null || exit 1; \
	echo "[pg] waiting for $(PG_TEST_CONTAINER) on port $(PG_TEST_PORT)"; \
	for attempt in $$(seq 1 60); do \
		docker exec $(PG_TEST_CONTAINER) pg_isready -U postgres >/dev/null 2>&1 && exit 0; \
		sleep 0.5; \
	done; \
	echo "[pg] $(PG_TEST_CONTAINER) did not become ready" >&2; \
	exit 1

pg-test-down: ## Stop the disposable PostgreSQL
	@docker stop $(PG_TEST_CONTAINER) >/dev/null 2>&1 \
		&& echo "[pg] $(PG_TEST_CONTAINER) stopped" \
		|| echo "[pg] $(PG_TEST_CONTAINER) was not running"

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
