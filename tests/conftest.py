from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from wb_pipeline.api_client import WorldBankClient
from wb_pipeline.config import PipelineConfig, load_config

from fake_api import FakeWorldBank, base_observations

TEST_COUNTRIES = ["BRA", "IND", "USA"]
TEST_YEARS = (2021, 2023)


def make_config(tmp_path: Path, countries=None, years=TEST_YEARS, per_page=1000) -> PipelineConfig:
    """Production config with a small scope and an isolated warehouse/output under tmp_path."""
    prod = load_config()
    data = prod.model_dump()
    data["scope"]["countries"] = countries or TEST_COUNTRIES
    data["scope"]["years"] = {"start": years[0], "end": years[1]}
    data["source"].update(per_page=per_page, max_retries=3, backoff_seconds=0)
    data["warehouse"]["path"] = str(tmp_path / "warehouse.duckdb")
    data["output"]["dir"] = str(tmp_path / "output")
    return PipelineConfig.model_validate(data)


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    # The warehouse path must come from the test config, never from the developer's shell.
    monkeypatch.delenv("WB_DUCKDB_PATH", raising=False)


@pytest.fixture
def cfg(tmp_path) -> PipelineConfig:
    return make_config(tmp_path)


@pytest.fixture
def fake_api() -> FakeWorldBank:
    years = range(TEST_YEARS[0], TEST_YEARS[1] + 1)
    return FakeWorldBank(observations=base_observations(TEST_COUNTRIES, years))


def client_for(cfg: PipelineConfig, fake: FakeWorldBank) -> WorldBankClient:
    return WorldBankClient(cfg.source, session=fake, sleep=lambda s: None)


def query(cfg: PipelineConfig, sql: str) -> list[tuple]:
    con = duckdb.connect(str(cfg.warehouse_path))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()
