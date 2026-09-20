.PHONY: install api worker scheduler test frontend
install:
	python -m pip install -e '.[test]'
api:
	uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
worker:
	python -m backend.app.workers.runtime
scheduler:
	python -m backend.app.scheduler
test:
	pytest
frontend:
	cd frontend && npm install && npm run dev -- --host 0.0.0.0
