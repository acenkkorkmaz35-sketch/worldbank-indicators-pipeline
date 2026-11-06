"""Command line entry point.

    wb-pipeline run        # ingest -> dbt build (models, snapshot, DQ tests) -> CSV export
    wb-pipeline ingest     # API -> raw layer only
    wb-pipeline transform  # dbt build only (re-processes raw without calling the API)
    wb-pipeline export     # reporting views -> CSV
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .export import export_dq_results, export_reports
from .ingest import run_ingestion
from .transform import run_dbt_build


def _transform(cfg) -> bool:
    result = run_dbt_build(cfg)
    export_dq_results(result.tests, cfg.output_dir)
    failed = [t for t in result.tests if t.status in ("fail", "error")]
    warned = [t for t in result.tests if t.status == "warn"]
    logging.info(
        "data-quality: %s tests, %s failed, %s warnings", len(result.tests), len(failed), len(warned)
    )
    for t in failed + warned:
        logging.warning("  %s %s (%s rows)", t.status.upper(), t.name, t.failures)
    return result.success


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wb-pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "ingest", "transform", "export"])
    parser.add_argument("--config", help="path to pipeline YAML (default: config/pipeline.yml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)

    if args.command in ("run", "ingest"):
        run_ingestion(cfg)
    if args.command in ("run", "transform"):
        if not _transform(cfg):
            logging.error("dbt build failed - reporting outputs were not refreshed")
            return 1
    if args.command in ("run", "export"):
        export_reports(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
