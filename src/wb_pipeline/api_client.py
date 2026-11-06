"""World Bank API v2 client: paging, retries, and response validation.

Notes on the API's failure modes (observed, not just documented):
* Errors are often returned with HTTP 200 and a body like
  ``[{"message": [{"id": "120", "key": "Invalid value", ...}]}]`` - status code alone is not enough.
* Occasionally the API returns HTML/XML instead of JSON under load - treated as transient.
* ``[meta, null]`` is a valid "no rows" answer, not an error.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import requests

from .config import SourceConfig

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class WorldBankAPIError(RuntimeError):
    """Non-retryable error: the API rejected the request or broke its contract."""


class TransientAPIError(RuntimeError):
    """Retryable error: timeouts, throttling, 5xx, malformed bodies."""


@dataclass(frozen=True)
class ApiPage:
    """One page of one logical request, exactly as received, plus parsed metadata."""

    request_key: str
    endpoint: str
    entity: str | None
    request_url: str
    page: int
    pages: int
    total: int
    source_last_updated: str | None
    http_status: int
    fetched_at: datetime
    body_text: str
    rows: list[dict[str, Any]] = field(repr=False)


class WorldBankClient:
    def __init__(
        self,
        cfg: SourceConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "wb-pipeline/0.1 (+research data pipeline)")
        self._sleep = sleep

    # -- public API ------------------------------------------------------
    def fetch_countries(self, countries: list[str]) -> list[ApiPage]:
        codes = ";".join(sorted(countries))
        return self._fetch_all_pages(
            request_key=f"country|countries={codes}",
            endpoint="country",
            entity=None,
            path=f"/country/{codes}",
            params={},
        )

    def fetch_indicator_metadata(self, indicator: str) -> list[ApiPage]:
        return self._fetch_all_pages(
            request_key=f"indicator|indicator={indicator}",
            endpoint="indicator",
            entity=indicator,
            path=f"/indicator/{indicator}",
            params={},
        )

    def fetch_observations(self, indicator: str, countries: list[str], date: str) -> list[ApiPage]:
        codes = ";".join(sorted(countries))
        return self._fetch_all_pages(
            request_key=f"observation|indicator={indicator}|countries={codes}|date={date}",
            endpoint="observation",
            entity=indicator,
            path=f"/country/{codes}/indicator/{indicator}",
            params={"date": date},
        )

    # -- paging ----------------------------------------------------------
    def _fetch_all_pages(
        self, request_key: str, endpoint: str, entity: str | None, path: str, params: dict
    ) -> list[ApiPage]:
        first = self._fetch_page(request_key, endpoint, entity, path, params, page=1)
        pages = [first]
        for page_no in range(2, first.pages + 1):
            page = self._fetch_page(request_key, endpoint, entity, path, params, page=page_no)
            if page.total != first.total or page.pages != first.pages:
                # Source changed while we were paging; the result set would be inconsistent.
                raise WorldBankAPIError(
                    f"{request_key}: paging metadata changed mid-request "
                    f"(total {first.total}->{page.total}, pages {first.pages}->{page.pages})"
                )
            pages.append(page)

        received = sum(len(p.rows) for p in pages)
        if received != first.total:
            raise WorldBankAPIError(
                f"{request_key}: expected {first.total} rows from metadata, received {received}"
            )
        log.info("fetched %s: %s page(s), %s row(s)", request_key, len(pages), received)
        return pages

    def _fetch_page(
        self, request_key: str, endpoint: str, entity: str | None, path: str, params: dict, page: int
    ) -> ApiPage:
        query = dict(params, format="json", per_page=self.cfg.per_page, page=page)
        # Deterministic URL (sorted params) => identical requests hash identically in the raw layer.
        url = f"{self.cfg.base_url.rstrip('/')}{path}?{urlencode(sorted(query.items()), safe=':;')}"
        status, text = self._get_with_retry(url)
        meta, rows = _parse_body(url, text)

        if int(meta.get("page", page)) != page:
            raise WorldBankAPIError(f"{url}: asked for page {page}, got page {meta.get('page')}")

        return ApiPage(
            request_key=request_key,
            endpoint=endpoint,
            entity=entity,
            request_url=url,
            page=page,
            pages=max(int(meta.get("pages") or 1), 1),
            total=int(meta.get("total") or 0),
            source_last_updated=meta.get("lastupdated"),
            http_status=status,
            fetched_at=datetime.now(UTC),
            body_text=text,
            rows=rows,
        )

    # -- transport with retries -----------------------------------------
    def _get_with_retry(self, url: str) -> tuple[int, str]:
        attempt = 0
        while True:
            try:
                return self._get_once(url)
            except TransientAPIError as exc:
                if attempt >= self.cfg.max_retries:
                    raise WorldBankAPIError(f"giving up after {attempt + 1} attempts: {url}: {exc}") from exc
                delay = self.cfg.backoff_seconds * (2**attempt) + random.uniform(0, 0.25)
                log.warning("transient error (attempt %s): %s; retrying in %.2fs", attempt + 1, exc, delay)
                self._sleep(delay)
                attempt += 1

    def _get_once(self, url: str) -> tuple[int, str]:
        try:
            resp = self.session.get(url, timeout=self.cfg.timeout_seconds)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientAPIError(f"{type(exc).__name__}: {exc}") from exc

        if resp.status_code in RETRYABLE_STATUS:
            raise TransientAPIError(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise WorldBankAPIError(f"HTTP {resp.status_code} for {url}: {resp.text[:300]}")

        text = resp.text
        try:
            json.loads(text)
        except ValueError as exc:
            # The API sometimes serves an HTML/XML error page under load.
            raise TransientAPIError(f"non-JSON body ({text[:80]!r})") from exc
        return resp.status_code, text


def _parse_body(url: str, text: str) -> tuple[dict, list[dict]]:
    body = json.loads(text)

    # Error envelope, served with HTTP 200: [{"message": [...]}] (or a bare object).
    envelope = body[0] if isinstance(body, list) and body else body
    if isinstance(envelope, dict) and "message" in envelope:
        msgs = envelope.get("message") or []
        detail = "; ".join(f"{m.get('id')}:{m.get('key')}:{m.get('value')}" for m in msgs) or str(msgs)
        raise WorldBankAPIError(f"API error for {url}: {detail}")

    if not (isinstance(body, list) and len(body) == 2 and isinstance(body[0], dict)):
        raise WorldBankAPIError(f"unexpected response shape for {url}: {text[:200]!r}")

    meta, rows = body
    if rows is None:  # valid empty result
        rows = []
    if not isinstance(rows, list):
        raise WorldBankAPIError(f"unexpected data section for {url}: {type(rows).__name__}")
    return meta, rows
