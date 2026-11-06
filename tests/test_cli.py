"""CLI contract: exit codes and failure behaviour (all offline)."""

import duckdb

from wb_pipeline.cli import EXIT_SOURCE, EXIT_USAGE, main

from conftest import NETWORK_ATTEMPTS, make_config, write_config


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
