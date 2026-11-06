"""Command line entry point.

    wb-pipeline run        # ingest -> dbt build (models, snapshot, DQ tests) -> CSV export
    wb-pipeline ingest     # API -> raw layer only
    wb-pipeline transform  # dbt build only (re-processes raw without calling the API)
    wb-pipeline export     # reporting views -> CSV

Exit codes: 0 ok · 1 data-quality / dbt failure · 2 usage or config error · 3 source (API) failure
"""

from __future__ import annotations

import argparse
import logging
import sys

import duckdb
from pydantic import ValidationError

from .api_client import WorldBankAPIError
from .config import PipelineConfig, load_config
from .export import export_dq_results, export_reports
from .ingest import run_ingestion
from .transform import run_dbt_build

log = logging.getLogger("wb_pipeline")

EXIT_OK, EXIT_DQ, EXIT_USAGE, EXIT_SOURCE = 0, 1, 2, 3


class UsageError(RuntimeError):
    pass


def _require_ingested(cfg: PipelineConfig) -> None:
    """transform/export need at least one successful ingestion in the warehouse."""
    if not cfg.warehouse_path.exists():
        raise UsageError(f"no warehouse at {cfg.warehouse_path} - run `wb-pipeline ingest` (or `run`) first")
    con = duckdb.connect(str(cfg.warehouse_path))
    try:
        has_run = (
            con.execute(
                "select count(*) from information_schema.tables "
                "where table_schema = 'raw' and table_name = 'ingestion_run'"
            ).fetchone()[0]
            and con.execute("select count(*) from raw.ingestion_run where status = 'success'").fetchone()[0]
        )
    finally:
        con.close()
    if not has_run:
        raise UsageError("no successful ingestion run in the warehouse - run `wb-pipeline ingest` first")


def _transform(cfg: PipelineConfig) -> bool:
    result = run_dbt_build(cfg)
    export_dq_results(result.tests, cfg.output_dir)
    failed = [t for t in result.tests if t.status in ("fail", "error")]
    warned = [t for t in result.tests if t.status == "warn"]
    log.info("data-quality: %s tests, %s failed, %s warnings", len(result.tests), len(failed), len(warned))
    for t in failed + warned:
        log.warning("  %s %s (%s rows)", t.status.upper(), t.name, t.failures)
    return result.success


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wb-pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["run", "ingest", "transform", "export"])
    parser.add_argument("--config", help="path to pipeline YAML (default: config/pipeline.yml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
        if args.command in ("run", "ingest"):
            run_ingestion(cfg)
        else:
            _require_ingested(cfg)
        if args.command in ("run", "transform") and not _transform(cfg):
            log.error(
                "dbt build failed - reporting outputs were NOT refreshed; "
                "failing rows are in schema dq_failures (see output/dq_test_results.csv)"
            )
            return EXIT_DQ
        if args.command in ("run", "export"):
            export_reports(cfg)
        return EXIT_OK
    except (FileNotFoundError, ValidationError, UsageError) as exc:
        log.error("%s", exc)
        return EXIT_USAGE
    except WorldBankAPIError as exc:
        # The run is recorded as failed in raw.ingestion_run; the warehouse still serves the
        # last successful run, so re-running later is safe.
        log.error("source failure, nothing published: %s", exc)
        return EXIT_SOURCE


if __name__ == "__main__":
    sys.exit(main())
