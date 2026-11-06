from __future__ import annotations

import socket
from pathlib import Path

import duckdb
import pytest
import yaml

from wb_pipeline.api_client import WorldBankClient
from wb_pipeline.config import PipelineConfig, load_config

from fake_api import FakeWorldBank, base_observations

TEST_COUNTRIES = ["BRA", "IND", "USA"]
TEST_YEARS = (2021, 2023)


def make_config(
    tmp_path: Path, countries=None, years=TEST_YEARS, per_page=1000, extra_indicators=()
) -> PipelineConfig:
    """Production config with a small scope and an isolated warehouse/output under tmp_path."""
    prod = load_config()
    data = prod.model_dump()
    data["scope"]["countries"] = countries or TEST_COUNTRIES
    data["scope"]["years"] = {"start": years[0], "end": years[1]}
    data["scope"]["indicators"] += list(extra_indicators)
    data["source"].update(per_page=per_page, max_retries=3, backoff_seconds=0)
    data["warehouse"]["path"] = str(tmp_path / "warehouse.duckdb")
    data["output"]["dir"] = str(tmp_path / "output")
    return PipelineConfig.model_validate(data)


def write_config(cfg: PipelineConfig, path: Path) -> Path:
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return path


NETWORK_ATTEMPTS: list = []


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests must never reach the live API (or anything else): any outbound socket connection
    fails like an unreachable network, and is recorded so tests can assert on it."""

    def blocked(self, address, *args, **kwargs):
        NETWORK_ATTEMPTS.append(address)
        raise OSError(f"network access disabled in tests (attempted {address!r})")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    # The warehouse path must come from the test config, never from the developer's shell.
    monkeypatch.delenv("WB_DUCKDB_PATH", raising=False)
    NETWORK_ATTEMPTS.clear()
    yield


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
