.PHONY: dev backend frontend build test test-py test-fe lint screenshots seed-demo install start stop status

PY := uv run --no-sync

install:
	@mkdir -p $(HOME)/.venvs
	@test -d $(HOME)/.venvs/dataset-genie || uv venv --python 3.11 $(HOME)/.venvs/dataset-genie
	@test -e .venv || ln -s $(HOME)/.venvs/dataset-genie .venv
	uv pip install -e ".[dev]"
	@chflags nohidden $(HOME)/.venvs/dataset-genie/lib/python3.11/site-packages/*.pth 2>/dev/null || true
	@echo "$(CURDIR)/backend" > $(HOME)/.venvs/dataset-genie/lib/python3.11/site-packages/genie_dev.pth
	@mkdir -p $(HOME)/.cache/dataset-genie
	@test -e frontend/node_modules || ln -s $(HOME)/.cache/dataset-genie/node_modules frontend/node_modules
	cd frontend && npm install

dev: ## backend (reload) + vite with /api proxy
	@trap 'kill 0' INT TERM; \
	$(PY) uvicorn genie.main:app --reload --port 8765 --app-dir backend & \
	(cd frontend && npm run dev) & \
	wait

backend:
	$(PY) uvicorn genie.main:app --reload --port 8765 --app-dir backend

build: ## single runnable app: built UI served by the backend
	cd frontend && npm run build
	@echo "Run: uv run genie serve"

test: test-py test-fe

test-py:
	$(PY) pytest -q

test-fe:
	cd frontend && npx tsc -b && (npm run test --if-present)

lint:
	$(PY) ruff check backend tests
	cd frontend && npx tsc -b

seed-demo: ## demo project "Linux Incident Triage" with realistic fake data (idempotent; honours GENIE_HOME)
	$(PY) python scripts/seed_demo.py

screenshots: ## Playwright captures of all 11 screens into docs/screenshots (needs `make dev` running; falls back to ?mock=1)
	cd frontend && npx playwright install chromium >/dev/null 2>&1 || true
	cd frontend && npx -y tsx ../scripts/screenshots.ts

start: ## run the built app in the background on :8765 (scripts/start.sh --dev for hot reload)
	@scripts/start.sh

stop: ## stop whatever scripts/start.sh or make dev left running
	@scripts/stop.sh

status:
	@curl -sf http://localhost:8765/api/health && echo "  running on :8765" || echo "not running"
