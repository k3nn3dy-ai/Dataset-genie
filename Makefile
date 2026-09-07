.PHONY: dev backend frontend build test test-py test-fe lint screenshots seed-demo install

PY := uv run

install:
	uv venv --python 3.11 .venv || true
	uv pip install -e ".[dev]"
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

seed-demo: ## demo project with realistic fake data for screenshots
	$(PY) python scripts/seed_demo.py

screenshots: ## Playwright captures of all screens into docs/screenshots
	cd frontend && npx playwright install chromium >/dev/null 2>&1 || true
	cd frontend && npx tsx ../scripts/screenshots.ts
