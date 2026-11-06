PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: run setup test pipeline docs clean

## One command from a clean checkout: create venv, install, run the pipeline
run: setup pipeline

setup: $(BIN)/wb-pipeline

$(BIN)/wb-pipeline: pyproject.toml
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --quiet --upgrade pip
	$(BIN)/python -m pip install --quiet -e ".[dev]"

pipeline: setup
	$(BIN)/wb-pipeline run

test: setup
	$(BIN)/python -m pytest

docs: setup
	cd dbt && WB_DUCKDB_PATH=../data/warehouse.duckdb ../$(BIN)/dbt docs generate --profiles-dir . && \
	WB_DUCKDB_PATH=../data/warehouse.duckdb ../$(BIN)/dbt docs serve --profiles-dir .

clean:
	rm -rf data dbt/target dbt/logs
