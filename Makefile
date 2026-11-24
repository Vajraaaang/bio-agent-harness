SHELL := /bin/sh
.DEFAULT_GOAL := help
PROJECT := $(CURDIR)
VENV ?= $(PROJECT)/.venv
READINESS_VENV ?= $(PROJECT)/.pytest-readiness-locked-env
PYTHON_VERSION ?= 3.13
VERIFY_PYTHON ?= $(shell if test -x "$(READINESS_VENV)/bin/python"; then printf '%s\n' "$(READINESS_VENV)/bin/python"; else printf '%s\n' "$(VENV)/bin/python"; fi)
UV_SYNC = uv sync --project "$(PROJECT)" --locked --python "$(PYTHON_VERSION)"
PYTEST_ROOT := $(PROJECT)/.pytest-workspaces
export PYTHONPATH := $(PROJECT)/src:$(PROJECT)
VERIFY_SQLITE_LIB ?= $(or $(DYLD_LIBRARY_PATH),$(wildcard /opt/homebrew/opt/sqlite/lib))
VERIFY := DYLD_LIBRARY_PATH="$(VERIFY_SQLITE_LIB)" "$(VERIFY_PYTHON)" -P
RUFF ?= $(VERIFY) -m ruff
MYPY ?= $(VERIFY) -m mypy
export PATH := $(VENV)/bin:$(PATH)
export VECLIB_MAXIMUM_THREADS ?= 1
export OPENBLAS_NUM_THREADS ?= 1
export OMP_NUM_THREADS ?= 1
export MKL_NUM_THREADS ?= 1
export SINGLE_THREAD ?= 1
export NUMBA_NUM_THREADS ?= 1
export NUMBA_THREADING_LAYER ?= workqueue

.PHONY: help install sync-runtimes check-platform check-sqlite-wal test lint typecheck test-fake-cli test-wheel-custody test-rna-release controlled-demo demo sweep pilot
help: ## Show local commands; pilot and sweep invoke live models
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "%-18s %s\n", $$1, $$2}' "$(PROJECT)/Makefile"

install: ## Install locked dependencies and refresh the readiness environment if present
	@if [ "$$(uname -s)" = Darwin ] && [ "$$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then \
	  echo 'Use a native arm64 terminal; this process is running under Rosetta.'; exit 1; fi
	UV_PROJECT_ENVIRONMENT="$(VENV)" $(UV_SYNC) --extra dev
	@if test -d "$(READINESS_VENV)"; then \
	  UV_PROJECT_ENVIRONMENT="$(READINESS_VENV)" $(UV_SYNC) --all-extras; \
	fi
	@$(MAKE) --no-print-directory check-platform

sync-runtimes: ## Refresh .venv and the readiness environment from the locked uv cache
	UV_PROJECT_ENVIRONMENT="$(VENV)" $(UV_SYNC) --offline --extra dev
	UV_PROJECT_ENVIRONMENT="$(READINESS_VENV)" $(UV_SYNC) --offline --all-extras
	@$(MAKE) --no-print-directory check-platform

check-platform: ## Require Python 3.13+ and native interpreter architecture
	@$(VERIFY) -c 'import platform, subprocess, sys; translated = subprocess.run(["sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True).stdout.strip() if sys.platform == "darwin" else "0"; print("Python", platform.python_version(), "on", platform.system(), platform.machine()); sys.exit("Python 3.13+ is required for local verification" if sys.version_info < (3, 13) else "Rosetta interpreter: install native arm64 Python" if translated == "1" else 0)'

check-sqlite-wal: check-platform ## Require SQLite 3.51.3+ or a documented WAL backport
	@$(VERIFY) -c 'import sqlite3; from devbench.engine.scientific_authority import require_patched_wal; print("SQLite", sqlite3.sqlite_version, flush=True); require_patched_wal()'

lint: ## Check project Python with Ruff
	$(RUFF) check "$(PROJECT)/src" "$(PROJECT)/devharness_core" "$(PROJECT)/devharness_controlled" "$(PROJECT)/devharness_server" "$(PROJECT)/rna_kernels" "$(PROJECT)/devbench_engine" "$(PROJECT)/scripts" "$(PROJECT)/tests"

typecheck: ## Check product Python with mypy
	$(MYPY) --config-file "$(PROJECT)/pyproject.toml"

test: check-sqlite-wal ## Run software tests under the patched local runtime
	@set -eu; \
	test ! -L "$(PYTEST_ROOT)"; \
	mkdir -p -m 700 "$(PYTEST_ROOT)"; \
	verify=$$(mktemp -d "$(PYTEST_ROOT)/suite.XXXXXX"); \
	trap 'find "$$verify" -type d -exec chmod u+rwx {} +; find "$$verify" -depth -delete' EXIT; \
	$(VERIFY) -m pytest -q --import-mode=importlib --basetemp="$$verify" "$(PROJECT)/tests"

test-fake-cli: check-sqlite-wal ## Run the offline single-session FakeCLI regression
	@set -eu; \
	mkdir -p -m 700 "$(PYTEST_ROOT)"; \
	verify="$(PYTEST_ROOT)/codex-verify"; \
	until mkdir "$$verify.lock" 2>/dev/null; do sleep 2; done; \
	trap 'if [ -d "$$verify" ] && [ ! -L "$$verify" ]; then find "$$verify" -type d -exec chmod u+rwx {} +; find "$$verify" -depth -delete; fi; rmdir "$$verify.lock"' EXIT; \
	DEVBENCH_RUN_LOCAL_FIXTURES=1 $(VERIFY) -m pytest -q --import-mode=importlib \
	  --basetemp="$$verify" \
	  "$(PROJECT)/tests/test_rna_investigation_backend.py" \
	  "$(PROJECT)/tests/test_rna_investigation_parent_integration.py" \
	  "$(PROJECT)/tests/test_adaptive_workflow_fake_cli.py"

test-wheel-custody: check-sqlite-wal ## Build and test an isolated non-editable wheel
	$(VERIFY) "$(PROJECT)/scripts/verify_wheel_custody.py" --project "$(PROJECT)" $(WHEEL_CUSTODY_ARGS)

test-rna-release: check-sqlite-wal ## Require installed Plugin execution and Studio readback for RNA release
	$(VERIFY) "$(PROJECT)/scripts/verify_rna_execution_release.py"

controlled-demo: ## Run real synthetic analyses, signed-plan checks, repeats and R references
	@set -eu; \
	mkdir -p "$(PROJECT)/results"; \
	demo_dir=$$(mktemp -d "$(PROJECT)/results/controlled-demo.XXXXXX"); \
	"$(VENV)/bin/python" -m devharness_controlled.qualification --output "$$demo_dir/verification"; \
	echo "Controlled numerical demo: $$demo_dir/verification/report.md"

demo: ## Run three privileged offline oracle controls (not model performance)
	@set -eu; \
	mkdir -p "$(PROJECT)/results"; \
	run_dir=$$(mktemp -d "$(PROJECT)/results/demo.XXXXXX"); \
	"$(VENV)/bin/devbench" --backend mock --condition assisted --turns 1 \
	  --task "$(PROJECT)/benchmarks/task_definitions/expanded/adversarial_alias_trap.json" \
	  --task "$(PROJECT)/benchmarks/task_definitions/expanded/adversarial_inverted_chronology.json" \
	  --task "$(PROJECT)/benchmarks/task_definitions/expanded/adversarial_oversmoothed_branches.json" \
	  --output "$$run_dir/runs.json" --audit-dir "$$run_dir/audit"; \
	"$(VENV)/bin/python" -c 'import json,sys; r=json.load(open(sys.argv[1])); assert len(r)==3 and all(x["passed"] for x in r), "Offline control failure; inspect saved runs"' "$$run_dir/runs.json"; \
	"$(VENV)/bin/devbench-report" "$$run_dir/runs.json" --output "$$run_dir/report.html"; \
	echo "Offline oracle controls only. Report: $$run_dir/report.html"

sweep: ## Launch the 12-task Code-as-Action suite through Codex; uses normal quota
	@set -eu; \
	mkdir -p "$(PROJECT)/results"; \
	run_dir=$$(mktemp -d "$(PROJECT)/results/sweep.XXXXXX"); \
	"$(VENV)/bin/devbench-live" --backend code-action --tasks 12 --timeout-sec 90 --output "$$run_dir"

pilot: ## Launch four separate live Code-as-Action participants; uses normal quota
	"$(VENV)/bin/devbench-live" --backend code-action --tasks 4 --timeout-sec 90
