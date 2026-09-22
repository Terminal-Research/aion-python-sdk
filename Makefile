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

.PHONY: help tests tests-integration tests-all tests-scenarios tests-scenarios-persistence \
	tests-scenarios-pg \
	tests-scenarios-dist tests-floors scenarios-matrix lint-imports release-check release check-env \
	dist-build dist-check dist-smoke pg-test-up pg-test-down

# `make help` lists targets in file order, under the `##@` heading above them.
# A new target goes under the heading it belongs to.
help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@ / {printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next} \
		/^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Tests

# The three suites are three directories - tests/unit, tests/integration and
# tests/scenarios - and the directory is what a target runs. tests/conftest.py
# puts the matching marker on every item, so `-m` combines suites in ARGS,
# but nothing here selects by marker. TEST_PATHS= narrows a run to part of a
# suite; ARGS= is pytest options and goes through untouched. They are two
# variables because a path in ARGS would land next to the suite's own
# directory, and the tests under it would be collected twice.
TEST_PATHS ?=

# A target takes TEST_PATHS under its own suite's directories only, checked
# here before pytest starts and before any container does: `make tests
# TEST_PATHS=tests/integration` must not run integration tests with no
# database under them, and no `-m` on the command line could hold that,
# because the last `-m` given wins and ARGS comes last. $(1) is the
# directories allowed; a path is one of them, or anything under one, node
# ids included. Both sides go through abspath first, so that `..` and `.`
# are resolved before the comparison rather than matched as path components:
# tests/unit/../integration is tests/integration, and ./tests/unit is
# tests/unit.
empty :=
space := $(empty) $(empty)
under = $(subst $(space),|,$(foreach dir,$(abspath $(1)),$(dir)|$(dir)/*))
define require_under
	for path in $(abspath $(TEST_PATHS)); do \
		case "$$path" in \
			$(call under,$(1)) ) ;; \
			*) echo "TEST_PATHS: $$path is not under $(1) - not this target's suite" >&2; exit 2 ;; \
		esac; \
	done
endef

tests: ## Run the unit suite (make tests ARGS="-k platform_link" TEST_PATHS="tests/unit/core")
	@$(call require_under,tests/unit)
	poetry run pytest $(if $(TEST_PATHS),$(TEST_PATHS),tests/unit) $(ARGS)

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
# asking pytest's marker filter - the belt to that suite's braces - would see
# a database that was never started.
tests-integration: export POSTGRES_TEST_URL := $(POSTGRES_TEST_URL)
tests-integration: ## Run integration tests; run before you commit
	@$(call require_under,tests/integration)
	@$(call with_pg_test,poetry run pytest $(if $(TEST_PATHS),$(TEST_PATHS),tests/integration) $(ARGS))

tests-all: export POSTGRES_TEST_URL := $(POSTGRES_TEST_URL)
tests-all: ## Run unit and integration tests together
	@$(call require_under,tests/unit tests/integration)
	@$(call with_pg_test,poetry run pytest $(if $(TEST_PATHS),$(TEST_PATHS),tests/unit tests/integration) $(ARGS))

# The scenario suite, tests/scenarios: a real `aion serve` per framework and
# deployment variant, driven over A2A. None of the targets above runs it - it
# starts processes and takes a minute - and these three are how it is run:
# here, before a release, and whenever a change touches what goes over the
# wire. `tests/scenarios/README.md` is the suite itself.
#
#   TAGS="smoke events"   only those suites (any of them)
#   FRAMEWORK=adk         one framework instead of all of them
#   ARGS="-x -vv"         straight through to pytest
#   KEEP_SERVE=1          leave the servers up afterwards and say where
TAGS ?=
FRAMEWORK ?=

# Without TAGS, everything except persistence: that suite needs a database
# and has `tests-scenarios-persistence` for it.
SCENARIO_TAGS := $(if $(TAGS),$(shell echo "$(TAGS)" | sed 's/  */ or /g'),not persistence)
SCENARIO_EXPR := scenario and ($(SCENARIO_TAGS))
FRAMEWORK_FILTER := $(if $(FRAMEWORK),-k "[$(FRAMEWORK)]",)

tests-scenarios: ## Run the scenarios against this working tree (TAGS=, FRAMEWORK=)
	poetry run pytest tests/scenarios -m "$(SCENARIO_EXPR)" $(FRAMEWORK_FILTER) $(ARGS)

# The scenarios that restart a server and expect the tasks to still be there.
# The database is handled the way the integration targets handle it, and by
# the same definition: an externally supplied POSTGRES_TEST_URL is used as is,
# otherwise a disposable container is started and stopped around the run. The
# target is named for what it proves rather than for the store that backs it;
# `tests-scenarios-pg` remains as a compatibility alias for existing local workflows.
tests-scenarios-persistence: export POSTGRES_TEST_URL := $(POSTGRES_TEST_URL)
tests-scenarios-persistence: ## Run the persistence scenarios against a real database
	@$(call with_pg_test,poetry run pytest tests/scenarios -m "scenario and persistence" \
		$(FRAMEWORK_FILTER) $(ARGS))

tests-scenarios-pg: tests-scenarios-persistence ## Alias for tests-scenarios-persistence

# The same scenarios, against the wheel in dist/ rather than the working tree:
# `poetry run`, because pytest and the A2A client come from this project's
# environment - the installation under test is the clean venv the script
# builds, and the agents reach it through SCENARIOS_AION_BIN.
tests-scenarios-dist: ## Run the scenarios against the built wheel in a clean venv
	poetry run ./scripts/packaging/scenarios.py $(SCENARIOS_ARGS)

##@ Checks

lint-imports: ## Check the layer contract between the aion.* subpackages
	poetry run lint-imports

# Not a test run: it collects the scenarios and reads the registries, and
# rewrites the document they describe. CI runs the same script with --check.
scenarios-matrix: ## Regenerate tests/scenarios/SCENARIOS.md from the suite
	poetry run ./scripts/scenarios_matrix.py

# `poetry run`, because the environment under inspection is the project's own -
# the script reports on whichever interpreter runs it.
check-env: ## Check the installed environment for duplicate or broken packages
	poetry run ./scripts/packaging/envcheck.py

# The other direction of the compatibility question. Every other target here
# runs against the newest release in each declared range; this one installs the
# oldest, so that `a2a-sdk>=1.1.2`, `langgraph>=1.0.0` and `google-adk>=1.20.0`
# mean what the manifest says rather than only having been typed there.
#
# uv rather than Poetry, for the one thing Poetry cannot do: `--resolution
# lowest-direct` takes every dependency this project declares to the oldest
# release the whole graph still allows, while letting their own dependencies
# resolve normally. Pinning each floor exactly instead would collide the extras
# against each other - google-adk 1.20.0 pins opentelemetry-api==1.37.0 while
# the server extra inherits >=1.33.0 from a2a-sdk, and both are true - and
# report a conflict nobody would ever install.
#
# It rewrites this environment and leaves it downgraded: `poetry install -E
# langgraph-server -E adk-server --with dev` puts it back. Run it in CI or in a
# throwaway checkout, not in the one you are working in.
tests-floors: ## Install the oldest allowed dependencies and run the unit suite
	@command -v uv >/dev/null 2>&1 || { \
		echo "tests-floors needs uv: https://docs.astral.sh/uv/getting-started/installation/" >&2; \
		exit 2; \
	}
	@echo "[floors] this downgrades the current environment; re-run poetry install afterwards"
	uv pip install --python "$$(poetry env info --path)/bin/python" \
		--resolution lowest-direct -e ".[langgraph-server,adk-server]"
	poetry run ./scripts/packaging/envcheck.py
	poetry run ./scripts/packaging/floors.py --check
	poetry run pytest tests/unit $(ARGS)

##@ Distribution

# Into an emptied dist/. check.py insists on finding exactly one wheel and one
# sdist there, and a stale artifact from an earlier version - or from the
# five-package layout this repository used to build - would trip it.
dist-build: ## Empty dist/ and build the wheel and the sdist into it
	rm -rf dist
	poetry build

dist-check: ## Check the built distributions against the packaging contract
	poetry run ./scripts/packaging/check.py

# Not `poetry run`: the point is a clean environment, and the venvs the script
# builds must inherit nothing from this project's. SMOKE_ARGS is where the
# interpreter goes - `make dist-smoke SMOKE_ARGS="--python 3.12"`.
dist-smoke: ## Install the built distributions into clean venvs and use them
	./scripts/packaging/smoke.py $(SMOKE_ARGS)

##@ Release

# Two commands that both read the version from pyproject.toml and take none.
# `release-check` runs check-env, tests, lint-imports, dist-build, dist-check,
# dist-smoke and tests-scenarios-dist in that order and publishes nothing. `release`
# runs the same gate after a preflight over git, GitHub and PyPI, asks, and
# creates the py-v* GitHub Release that starts publish-python.yml - the upload
# itself happens there, behind the reviewer of the `pypi` environment.
# RELEASE_ARGS goes to the script (`--python 3.12` picks the interpreter for
# both clean-environment steps); YES=1 answers the prompt for a run without a
# terminal.
release-check: ## Run every release check without publishing anything
	./scripts/release.py check $(RELEASE_ARGS)

release: ## Check, confirm, and publish the version in pyproject.toml
	./scripts/release.py publish $(if $(YES),--yes) $(RELEASE_ARGS)

##@ PostgreSQL

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
