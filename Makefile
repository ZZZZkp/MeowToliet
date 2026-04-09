PYTHON ?= python3

.PHONY: test lint run worker scheduler

test:
	PYTHONPATH=src $(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests

run:
	PYTHONPATH=src $(PYTHON) -m uvicorn meow_toilet.app.main:app --reload

worker:
	PYTHONPATH=src $(PYTHON) -m meow_toilet.workers.jobs

scheduler:
	PYTHONPATH=src $(PYTHON) -m meow_toilet.scheduler.service

