"""Export the reporting layer (and the DQ run summary) to CSV."""

from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .config import PipelineConfig
from .transform import TestOutcome

log = logging.getLogger(__name__)

# view -> deterministic sort order, so exported files diff cleanly between runs
REPORTS: dict[str, str] = {
    "rpt_missing_data": "indicator_code, country_iso3, year",
    "rpt_coverage_summary": "indicator_code, country_iso3",
    "rpt_yoy_change": "indicator_code, country_iso3, year",
    "rpt_latest_year_comparison": "indicator_code, rank_at_latest_year nulls last, country_iso3",
    "rpt_income_group_by_year": "indicator_code, year, is_all_countries_row, income_group_code",
    "rpt_source_revisions": "indicator_code, country_iso3, year, changed_at",
}


def export_reports(cfg: PipelineConfig, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, int]] = []
    # Not read_only: dbt-duckdb may still hold this file open in-process, and DuckDB only
    # shares an in-process database between connections opened with the same configuration.
    con = duckdb.connect(str(cfg.warehouse_path))
    try:
        for view, order_by in REPORTS.items():
            target = out_dir / f"{view}.csv"
            rel = f"select * from reporting.{view} order by {order_by}"
            con.execute(f"copy ({rel}) to '{target.as_posix()}' (header, delimiter ',')")
            n = con.execute(f"select count(*) from reporting.{view}").fetchone()[0]
            log.info("exported %-28s %4d rows -> %s", view, n, target)
            written.append((target, n))
        written_paths = [t for t, _ in written]
        written_paths.append(_write_manifest(con, cfg, out_dir, written))
    finally:
        con.close()
    return written_paths


def _write_manifest(
    con: duckdb.DuckDBPyConnection, cfg: PipelineConfig, out_dir: Path, files: list[tuple[Path, int]]
) -> Path:
    """Ties the exported files to the ingestion run and source release they were built from."""
    run_id, started_at, fingerprint = con.execute(
        """
        select run_id, started_at, config_fingerprint from raw.ingestion_run
        where status = 'success' order by started_at desc, run_id desc limit 1
        """
    ).fetchone()
    source_versions = con.execute(
        """
        select entity, max(source_last_updated)::varchar
        from staging.stg_wb__current_responses
        where endpoint = 'observation' group by entity order by entity
        """
    ).fetchall()
    manifest = {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ingestion_run_id": run_id,
        "ingestion_started_at": started_at.isoformat(),
        "config_fingerprint": fingerprint,
        "source_last_updated_by_indicator": dict(source_versions),
        "files": {t.name: {"rows": n} for t, n in files},
        "lineage_hint": "source_response_id columns -> raw.api_response.response_id "
        "(exact payload + request_url)",
    }
    target = out_dir / "_manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target


def export_dq_results(tests: list[TestOutcome], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "dq_test_results.csv"
    with open(target, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["test_name", "severity", "status", "failing_rows", "message"])
        for t in tests:
            w.writerow([t.name, t.severity, t.status, t.failures, t.message])
    return target
