# Morning competitive-intelligence + market-analytics product.
# Local-first: everything runs on fixtures with zero API keys.
#
#   make setup         one-time: python venv + pip install + npm install
#   make dev           run the pipeline, then serve the dashboard (vite)
#   make run-pipeline  regenerate web/public/brief.json (+ per-universe briefs, email html)
#   make test          unit + smoke tests
#   make eval          gate evals (handoff gate, fuse attribution, no-look-ahead)
#
# Scheduling is plain cron against the single entrypoint, e.g.:
#   45 6 * * 1-5  cd /path/to/repo && make run-pipeline
#
# Env knobs (see pipeline/providers/registry.py):
#   BRIEF_PROVIDERS=fixture|real      BRIEF_CLASSIFIER=auto|fixture|rules|anthropic

PY      := .venv/bin/python
PIP     := .venv/bin/pip
PYTEST  := .venv/bin/pytest

.PHONY: setup dev run-pipeline web serve test eval build clean

setup:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	cd web && npm install --no-audit --no-fund

run-pipeline:
	$(PY) -m pipeline.orchestrator

dev: run-pipeline
	cd web && npm run dev

web:
	cd web && npm run dev

# Production-style serving (what Render runs): built UI + artifact refreshes
# + ship/refresh endpoints from a single process on $PORT (default 8000).
serve: build
	$(PY) -m pipeline.serve

test:
	$(PYTEST) pipeline/tests
	cd web && npx tsc -b

eval:
	$(PYTEST) pipeline/evals -v

build: run-pipeline
	cd web && npm run build

clean:
	rm -rf out web/dist
