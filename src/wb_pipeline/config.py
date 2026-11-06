"""Typed, validated pipeline configuration loaded from YAML."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

def _repo_root() -> Path:
    """Project root (holds config/ and dbt/). Works for editable installs, regular installs
    run from the project directory (e.g. Docker WORKDIR), or an explicit WB_PIPELINE_HOME."""
    if os.environ.get("WB_PIPELINE_HOME"):
        return Path(os.environ["WB_PIPELINE_HOME"]).resolve()
    source_tree = Path(__file__).resolve().parents[2]
    if (source_tree / "dbt" / "dbt_project.yml").exists():
        return source_tree
    return Path.cwd()


REPO_ROOT = _repo_root()
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "pipeline.yml"

_ISO3 = re.compile(r"^[A-Z]{3}$")
_INDICATOR = re.compile(r"^[A-Z0-9]+(\.[A-Z0-9]+)+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceConfig(_Strict):
    base_url: str = "https://api.worldbank.org/v2"
    per_page: int = Field(1000, ge=1, le=32500)
    timeout_seconds: float = Field(30, gt=0)
    max_retries: int = Field(5, ge=0, le=10)
    backoff_seconds: float = Field(1.0, ge=0)


class YearRange(_Strict):
    start: int = Field(ge=1960)
    end: int = Field(le=2100)

    @model_validator(mode="after")
    def _ordered(self) -> "YearRange":
        if self.start > self.end:
            raise ValueError(f"years.start ({self.start}) must be <= years.end ({self.end})")
        return self

    @property
    def years(self) -> list[int]:
        return list(range(self.start, self.end + 1))

    @property
    def api_date_param(self) -> str:
        return f"{self.start}:{self.end}"


class Aggregation(_Strict):
    method: Literal["sum", "ratio", "weighted_avg"]
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    weight: Optional[str] = None

    @model_validator(mode="after")
    def _required_fields(self) -> "Aggregation":
        if self.method == "ratio" and not (self.numerator and self.denominator):
            raise ValueError("ratio aggregation needs numerator and denominator")
        if self.method == "weighted_avg" and not self.weight:
            raise ValueError("weighted_avg aggregation needs weight")
        return self


class ValidRange(_Strict):
    min: Optional[float] = None
    max: Optional[float] = None


class IndicatorConfig(_Strict):
    code: str
    short_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    unit: str
    aggregation: Aggregation
    valid_range: ValidRange = ValidRange()

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = v.strip().upper()
        if not _INDICATOR.match(v):
            raise ValueError(f"invalid World Bank indicator code: {v!r}")
        return v


class ScopeConfig(_Strict):
    years: YearRange
    countries: list[str] = Field(min_length=1)
    indicators: list[IndicatorConfig] = Field(min_length=1)

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        out = [c.strip().upper() for c in v]
        bad = [c for c in out if not _ISO3.match(c)]
        if bad:
            raise ValueError(f"countries must be ISO3 codes, got: {bad}")
        if len(set(out)) != len(out):
            raise ValueError("duplicate country codes in scope")
        return out

    @model_validator(mode="after")
    def _indicator_refs(self) -> "ScopeConfig":
        codes = [i.code for i in self.indicators]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate indicator codes in scope")
        names = [i.short_name for i in self.indicators]
        if len(set(names)) != len(names):
            raise ValueError("duplicate indicator short_name in scope")
        for ind in self.indicators:
            agg = ind.aggregation
            for ref in (agg.numerator, agg.denominator, agg.weight):
                if ref and ref not in codes:
                    raise ValueError(
                        f"{ind.code}: aggregation references {ref}, which is not an indicator in scope"
                    )
        return self


class QualityConfig(_Strict):
    gdp_per_capita_tolerance: float = Field(0.005, gt=0, lt=1)


class WarehouseConfig(_Strict):
    path: str = "data/warehouse.duckdb"


class OutputConfig(_Strict):
    dir: str = "output"


class PipelineConfig(_Strict):
    source: SourceConfig = SourceConfig()
    scope: ScopeConfig
    quality: QualityConfig = QualityConfig()
    warehouse: WarehouseConfig = WarehouseConfig()
    output: OutputConfig = OutputConfig()

    # -- derived helpers -------------------------------------------------
    @property
    def warehouse_path(self) -> Path:
        raw = os.environ.get("WB_DUCKDB_PATH") or self.warehouse.path
        p = Path(raw)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def output_dir(self) -> Path:
        p = Path(self.output.dir)
        return p if p.is_absolute() else REPO_ROOT / p

    def fingerprint(self) -> str:
        """Stable hash of the effective config, stored with every run for lineage."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


def load_config(path: str | Path | None = None) -> PipelineConfig:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return PipelineConfig.model_validate(data)
