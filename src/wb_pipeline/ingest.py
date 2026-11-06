"""Extract + load: World Bank API -> raw layer in DuckDB.

Design
------
* ``raw.api_response`` is **content-addressed**: ``response_id = sha256(request_url + body)``.
  Re-fetching unchanged data inserts nothing new; a changed body is a new row, so every
  historical payload stays available for audit and replay.
* ``raw.api_fetch_log`` records *every* fetch (run, request, page -> response_id). This is how
  the staging layer knows which payload is current for each logical request.
* A logical request (all pages) is written in a single transaction, so a request is either fully
  present for a run or absent - a failure half way through paging never leaves a partial result.
* ``meta.scope_*`` mirrors the YAML scope; dbt builds the expected country x indicator x year
  spine from it. It is only replaced after all requests of a run succeed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .api_client import ApiPage, WorldBankClient
from .config import PipelineConfig

log = logging.getLogger(__name__)

DDL = """
create schema if not exists raw;
create schema if not exists meta;

create table if not exists raw.ingestion_run (
    run_id              varchar primary key,
    started_at          timestamptz not null,
    finished_at         timestamptz,
    status              varchar not null,          -- running | success | failed
    config_fingerprint  varchar not null,
    config_json         json not null,
    error               varchar
);

create table if not exists raw.api_response (
    response_id          varchar primary key,      -- sha256(request_url, newline, body)
    request_key          varchar not null,         -- logical request, page-independent
    endpoint             varchar not null,         -- country | indicator | observation
    entity               varchar,                  -- indicator code where applicable
    request_url          varchar not null,
    page                 integer not null,
    pages                integer not null,
    total                integer not null,
    source_last_updated  date,
    payload_sha256       varchar not null,
    payload              json not null,            -- body exactly as received
    first_fetched_at     timestamptz not null,
    first_run_id         varchar not null
);

create table if not exists raw.api_fetch_log (
    run_id         varchar not null,
    request_key    varchar not null,
    page           integer not null,
    response_id    varchar not null,
    http_status    integer not null,
    fetched_at     timestamptz not null,
    primary key (run_id, request_key, page)
);

create table if not exists meta.scope_country (
    country_iso3  varchar primary key,
    sort_order    integer not null
);

create table if not exists meta.scope_year (
    year  integer primary key
);

create table if not exists meta.scope_indicator (
    indicator_code      varchar primary key,
    short_name          varchar not null,
    unit                varchar not null,
    aggregation_method  varchar not null,
    numerator_code      varchar,
    denominator_code    varchar,
    weight_code         varchar,
    valid_min           double,
    valid_max           double,
    sort_order          integer not null
);
"""


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(DDL)
    return con


def response_id(page: ApiPage) -> str:
    return hashlib.sha256(f"{page.request_url}\n{page.body_text}".encode()).hexdigest()


def write_request(con: duckdb.DuckDBPyConnection, run_id: str, pages: list[ApiPage]) -> int:
    """Persist all pages of one logical request atomically. Returns # of *new* payloads."""
    new = 0
    con.execute("begin transaction")
    try:
        for p in pages:
            rid = response_id(p)
            inserted = con.execute(
                """
                insert into raw.api_response
                select ?, ?, ?, ?, ?, ?, ?, ?, try_cast(? as date), ?, ?::json, ?, ?
                where not exists (select 1 from raw.api_response where response_id = ?)
                returning response_id
                """,
                [
                    rid, p.request_key, p.endpoint, p.entity, p.request_url, p.page, p.pages,
                    p.total, p.source_last_updated,
                    hashlib.sha256(p.body_text.encode()).hexdigest(), p.body_text,
                    p.fetched_at, run_id, rid,
                ],
            ).fetchall()
            new += len(inserted)
            con.execute(
                "insert into raw.api_fetch_log values (?, ?, ?, ?, ?, ?)",
                [run_id, p.request_key, p.page, rid, p.http_status, p.fetched_at],
            )
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    return new


def write_scope(con: duckdb.DuckDBPyConnection, cfg: PipelineConfig) -> None:
    s = cfg.scope
    con.execute("begin transaction")
    try:
        con.execute("delete from meta.scope_country")
        con.executemany(
            "insert into meta.scope_country values (?, ?)",
            [[c, i] for i, c in enumerate(s.countries)],
        )
        con.execute("delete from meta.scope_year")
        con.executemany("insert into meta.scope_year values (?)", [[y] for y in s.years.years])
        con.execute("delete from meta.scope_indicator")
        con.executemany(
            "insert into meta.scope_indicator values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    i.code, i.short_name, i.unit, i.aggregation.method,
                    i.aggregation.numerator, i.aggregation.denominator, i.aggregation.weight,
                    i.valid_range.min, i.valid_range.max, n,
                ]
                for n, i in enumerate(s.indicators)
            ],
        )
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise


def run_ingestion(cfg: PipelineConfig, client: WorldBankClient | None = None) -> str:
    """Fetch everything in scope and land it in the raw layer. Returns the run_id."""
    client = client or WorldBankClient(cfg.source)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    con = connect(cfg.warehouse_path)
    con.execute(
        "insert into raw.ingestion_run values (?, ?, null, 'running', ?, ?::json, null)",
        [run_id, datetime.now(timezone.utc), cfg.fingerprint(), json.dumps(cfg.model_dump(mode="json"))],
    )
    log.info("ingestion run %s started -> %s", run_id, cfg.warehouse_path)

    scope = cfg.scope
    stats = {"requests": 0, "pages": 0, "new_payloads": 0}
    try:
        requests_to_run = [lambda: client.fetch_countries(scope.countries)]
        for ind in scope.indicators:
            requests_to_run.append(lambda code=ind.code: client.fetch_indicator_metadata(code))
            requests_to_run.append(
                lambda code=ind.code: client.fetch_observations(
                    code, scope.countries, scope.years.api_date_param
                )
            )
        for fetch in requests_to_run:
            pages = fetch()
            stats["requests"] += 1
            stats["pages"] += len(pages)
            stats["new_payloads"] += write_request(con, run_id, pages)

        write_scope(con, cfg)
        con.execute(
            "update raw.ingestion_run set status = 'success', finished_at = ? where run_id = ?",
            [datetime.now(timezone.utc), run_id],
        )
        log.info(
            "ingestion run %s succeeded: %s requests, %s pages, %s new payload(s)",
            run_id, stats["requests"], stats["pages"], stats["new_payloads"],
        )
        return run_id
    except Exception as exc:
        con.execute(
            "update raw.ingestion_run set status = 'failed', finished_at = ?, error = ? where run_id = ?",
            [datetime.now(timezone.utc), str(exc)[:2000], run_id],
        )
        log.error("ingestion run %s failed: %s", run_id, exc)
        raise
    finally:
        con.close()
