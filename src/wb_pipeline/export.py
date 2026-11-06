"""Export the reporting layer (and the DQ run summary) to CSV."""

from __future__ import annotations

import csv
import logging
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
    written: list[Path] = []
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
            written.append(target)
    finally:
        con.close()
    return written


def export_dq_results(tests: list[TestOutcome], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "dq_test_results.csv"
    with open(target, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["test_name", "severity", "status", "failing_rows", "message"])
        for t in tests:
            w.writerow([t.name, t.severity, t.status, t.failures, t.message])
    return target
