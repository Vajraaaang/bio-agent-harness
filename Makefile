SHELL := /bin/sh
.DEFAULT_GOAL := help
PROJECT := $(CURDIR)
VENV := $(PROJECT)/.venv
PYTHON_VERSION ?= 3.12
PYTHON ?= python3.12
export PATH := $(VENV)/bin:$(PATH)
export VECLIB_MAXIMUM_THREADS ?= 1
export OPENBLAS_NUM_THREADS ?= 1
export OMP_NUM_THREADS ?= 1
export MKL_NUM_THREADS ?= 1

.PHONY: help install check-platform test controlled-demo demo sweep pilot notebook
help: ## Show local commands; pilot and sweep invoke live models
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "%-18s %s\n", $$1, $$2}' "$(PROJECT)/Makefile"

install: ## Sync locked dependencies and dev tools into .venv (uv preferred)
	@if [ "$$(uname -s)" = Darwin ] && [ "$$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then \
	  echo 'Use a native arm64 terminal; this process is running under Rosetta.'; exit 1; fi
	@if command -v uv >/dev/null 2>&1; then \
	  uv sync --project "$(PROJECT)" --locked --extra dev --python "$(PYTHON_VERSION)"; \
	else \
	  echo 'uv is unavailable: pip fallback does not apply the uv lockfile.'; \
	  if [ ! -x "$(VENV)/bin/python" ]; then "$(PYTHON)" -m venv "$(VENV)"; fi; \
	  "$(VENV)/bin/python" -m pip install -e "$(PROJECT)[dev]"; \
	fi
	@$(MAKE) --no-print-directory check-platform

check-platform: ## Show OS/interpreter architecture; reject Rosetta on Apple Silicon
	@"$(VENV)/bin/python" -c 'import platform, subprocess; translated = subprocess.run(["sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True).stdout.strip() if platform.system() == "Darwin" else "0"; print("Python", platform.python_version(), "on", platform.system(), platform.machine()); assert translated != "1", "Rosetta interpreter: install native arm64 Python"'

test: ## Run all software tests; needs OS process-inspection permission
	"$(VENV)/bin/python" -m pytest "$(PROJECT)/tests" -q

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

notebook: ## Build the self-contained Colab notebook locally; no upload or model call
	"$(VENV)/bin/python" "$(PROJECT)/scripts/build_colab_notebook.py"
