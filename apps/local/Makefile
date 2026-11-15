VENV ?= .venv
PY := $(VENV)/bin/python
SF := $(VENV)/bin/save-finder

.PHONY: setup setup-ai test lint format check db-init import stats serve

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

# Install the local AI extras (faster-whisper, open-clip, ollama client)
# and pull the configured Ollama model when possible.
setup-ai:
	@if command -v uv >/dev/null 2>&1; then \
		uv sync --all-extras; \
	else \
		if [ ! -d $(VENV) ]; then python3 -m venv $(VENV); fi; \
		$(PY) -m pip install --upgrade pip; \
		$(PY) -m pip install -e ".[dev,asr,vision,llm]"; \
	fi
	@if command -v ollama >/dev/null 2>&1; then \
		ollama pull qwen3:8b; \
	else \
		echo "Ollama não encontrado. Instale em https://ollama.com e rode: ollama pull qwen3:8b"; \
	fi
	-$(SF) doctor

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
