"""CLI contract: exit codes and failure behaviour (all offline)."""

import duckdb
import pytest

from wb_pipeline import ingest as wb_ingest
from wb_pipeline.cli import EXIT_DQ, EXIT_OK, EXIT_SOURCE, EXIT_USAGE, main

from conftest import NETWORK_ATTEMPTS, make_config, write_config
from fake_api import FakeWorldBank, base_observations


def test_transform_without_ingestion_is_a_usage_error(tmp_path):
    path = write_config(make_config(tmp_path), tmp_path / "pipeline.yml")
    assert main(["transform", "--config", str(path)]) == EXIT_USAGE


def test_invalid_config_is_a_usage_error(tmp_path):
    bad = tmp_path / "pipeline.yml"
    bad.write_text("scope: {years: {start: 2025, end: 2018}, countries: [BRA], indicators: []}\n")
    assert main(["ingest", "--config", str(bad)]) == EXIT_USAGE


def test_unreachable_source_fails_cleanly_and_records_the_run(tmp_path):
    cfg = make_config(tmp_path)
    path = write_config(cfg, tmp_path / "pipeline.yml")

    # network is blocked by the autouse fixture: the client retries, then gives up
    assert main(["run", "--config", str(path)]) == EXIT_SOURCE
    assert NETWORK_ATTEMPTS, "the guard should have intercepted the attempted API call"

    con = duckdb.connect(str(cfg.warehouse_path))
    status, error = con.execute("select status, error from raw.ingestion_run").fetchone()
    con.close()
    assert status == "failed" and "giving up" in error
    assert not (cfg.output_dir / "rpt_yoy_change.csv").exists()  # nothing published


def _serve(monkeypatch, fake):
    """Make the CLI's own ingestion talk to the in-memory API."""
    real = wb_ingest.WorldBankClient
    monkeypatch.setattr(
        wb_ingest, "WorldBankClient", lambda source: real(source, session=fake, sleep=lambda s: None)
    )


@pytest.mark.integration
def test_run_succeeds_end_to_end_and_publishes(tmp_path, monkeypatch, fake_api):
    cfg = make_config(tmp_path)
    path = write_config(cfg, tmp_path / "pipeline.yml")
    _serve(monkeypatch, fake_api)

    assert main(["run", "--config", str(path)]) == EXIT_OK
    assert (cfg.output_dir / "rpt_yoy_change.csv").exists()
    assert (cfg.output_dir / "_manifest.json").exists()
    assert (cfg.output_dir / "powerbi" / "fct_indicator_value.csv").exists()

    # replay from raw with a full rebuild of the MERGE-loaded fact: same content, no API call
    calls = len(fake_api.calls)
    assert main(["transform", "--full-refresh", "--config", str(path)]) == EXIT_OK
    assert len(fake_api.calls) == calls
    con = duckdb.connect(str(cfg.warehouse_path))
    assert con.execute(
        "select count(*), count(distinct indicator_value_id) from core.fct_indicator_value"
    ).fetchone() == (36, 36)
    con.close()


@pytest.mark.integration
def test_data_quality_failure_exits_1_and_publishes_nothing(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    path = write_config(cfg, tmp_path / "pipeline.yml")
    obs = base_observations(["BRA", "IND", "USA"], range(2021, 2024))
    obs[("NY.GDP.PCAP.CD", "IND", 2022)] *= 2  # breaks GDP per capita = GDP / population
    _serve(monkeypatch, FakeWorldBank(observations=obs))

    assert main(["run", "--config", str(path)]) == EXIT_DQ
    assert not (cfg.output_dir / "rpt_yoy_change.csv").exists()
    results = (cfg.output_dir / "dq_test_results.csv").read_text(encoding="utf-8")
    assert "assert_ratio_indicators_reconcile,error,fail" in results
