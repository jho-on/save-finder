VENV ?= .venv
PY := $(VENV)/bin/python
SF := $(VENV)/bin/save-finder

.PHONY: setup test lint format check db-init import stats serve

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check .

format:
	$(VENV)/bin/ruff format .

check:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/pytest

db-init:
	$(SF) db init

stats:
	$(SF) stats

serve:
	$(SF) serve
