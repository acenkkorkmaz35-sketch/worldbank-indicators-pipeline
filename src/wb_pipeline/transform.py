"""Run the dbt project (models + snapshots + data tests) against the configured warehouse."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from .config import REPO_ROOT, PipelineConfig

log = logging.getLogger(__name__)

DBT_DIR = REPO_ROOT / "dbt"


@dataclass(frozen=True)
class TestOutcome:
    name: str
    severity: str
    status: str
    failures: int
    message: str


@dataclass(frozen=True)
class BuildResult:
    success: bool
    tests: list[TestOutcome]


def dbt_args(cfg: PipelineConfig) -> list[str]:
    dbt_vars = {"ratio_reconciliation_tolerance": cfg.quality.ratio_reconciliation_tolerance}
    return [
        "--project-dir",
        str(DBT_DIR),
        "--profiles-dir",
        str(DBT_DIR),
        "--vars",
        json.dumps(dbt_vars),
    ]


def run_dbt_build(cfg: PipelineConfig, extra_args: list[str] | None = None) -> BuildResult:
    # Imported lazily: dbt is heavy and not needed for ingestion-only commands/tests.
    from dbt.cli.main import dbtRunner

    os.environ["WB_DUCKDB_PATH"] = str(cfg.warehouse_path)
    args = ["build", *dbt_args(cfg), *(extra_args or [])]
    log.info("dbt %s", " ".join(args))
    res = dbtRunner().invoke(args)

    tests: list[TestOutcome] = []
    if res.result is not None and hasattr(res.result, "results"):
        for r in res.result.results:
            node = r.node
            if getattr(node, "resource_type", None) and str(node.resource_type).endswith("test"):
                tests.append(
                    TestOutcome(
                        name=node.name,
                        severity=str(node.config.severity).lower(),
                        status=str(r.status),
                        failures=int(r.failures or 0),
                        message=(r.message or "").replace("\n", " ")[:300],
                    )
                )
    if res.exception is not None:
        log.error("dbt raised: %s", res.exception)
    return BuildResult(success=bool(res.success), tests=sorted(tests, key=lambda t: t.name))
