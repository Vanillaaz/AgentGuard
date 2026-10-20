.PHONY: backend dashboard test demo
backend:
	python -m uvicorn backend.app.main:app --reload
dashboard:
	cd dashboard && npm install && npm run dev
test:
	python -m pytest -q
demo:
	python -m backend.app.demo_cli
