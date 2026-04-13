PYTHON ?= python3

.PHONY: test docker-test lint run worker scheduler migrate

test:
	PYTHONPATH=src $(PYTHON) -m pytest

docker-test:
	docker compose run --rm test

lint:
	$(PYTHON) -m ruff check src tests

run:
	PYTHONPATH=src $(PYTHON) -m uvicorn meow_toilet.app.main:app --reload

worker:
	PYTHONPATH=src $(PYTHON) -m meow_toilet.workers.jobs --loop

scheduler:
	PYTHONPATH=src $(PYTHON) -m meow_toilet.scheduler.service --loop

migrate:
	PYTHONPATH=src $(PYTHON) -m alembic upgrade head
