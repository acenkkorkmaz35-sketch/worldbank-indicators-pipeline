"""In-memory stand-in for the World Bank API v2, used by all tests (no network).

Responses mirror the real wire format (field names, string/number types, ``[meta, data]``
envelopes, ``lastupdated``, error envelopes served with HTTP 200), and honour ``per_page`` /
``page`` so paging is exercised for real. Failures can be scripted per URL substring.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests

NOT_RETURNED = object()  # sentinel: the API omits the row entirely

COUNTRY_META = {
    "BRA": ("BR", "Brazil", "LCN", "Latin America & Caribbean ", "UMC", "Upper middle income"),
    "IND": ("IN", "India", "SAS", "South Asia", "LMC", "Lower middle income"),
    "USA": ("US", "United States", "NAC", "North America", "HIC", "High income"),
    "DEU": ("DE", "Germany", "ECS", "Europe & Central Asia", "HIC", "High income"),
}

INDICATOR_NAMES = {
    "SP.POP.TOTL": "Population, total",
    "NY.GDP.MKTP.CD": "GDP (current US$)",
    "NY.GDP.PCAP.CD": "GDP per capita (current US$)",
    "SP.DYN.LE00.IN": "Life expectancy at birth, total (years)",
}

ERROR_ENVELOPE = (
    '[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]'
)


def base_observations(countries, years) -> dict[tuple[str, str, int], Any]:
    """Internally consistent values: GDP per capita == GDP / population exactly."""
    pop = {"BRA": 210e6, "IND": 1.40e9, "USA": 330e6, "DEU": 83e6}
    gdppc = {"BRA": 9000.0, "IND": 2200.0, "USA": 70000.0, "DEU": 50000.0}
    life = {"BRA": 75.0, "IND": 70.0, "USA": 78.0, "DEU": 81.0}
    obs = {}
    for c in countries:
        for i, y in enumerate(years):
            p = round(pop[c] * (1.01**i))
            pc = gdppc[c] * (1.03**i)
            obs[("SP.POP.TOTL", c, y)] = p
            obs[("NY.GDP.PCAP.CD", c, y)] = pc
            obs[("NY.GDP.MKTP.CD", c, y)] = pc * p
            obs[("SP.DYN.LE00.IN", c, y)] = life[c] + 0.1 * i
    return obs


@dataclass
class FakeResponse:
    status_code: int
    text: str


@dataclass
class FakeWorldBank:
    observations: dict = field(default_factory=dict)
    last_updated: str = "2026-10-08"
    # url substring -> list of scripted results consumed in order; each is
    # an int (HTTP status), a str (body served with 200), or an Exception instance
    failures: dict[str, list] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    # -- requests.Session duck-type ---------------------------------------
    @property
    def headers(self) -> dict:
        return self.__dict__.setdefault("_headers", {})

    def get(self, url: str, timeout: float | None = None) -> FakeResponse:
        self.calls.append(url)
        for needle, script in self.failures.items():
            if needle in url and script:
                outcome = script.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                if isinstance(outcome, int):
                    return FakeResponse(outcome, f"<html>{outcome}</html>")
                return FakeResponse(200, outcome)
        return FakeResponse(200, self._route(url))

    # -- routing ------------------------------------------------------------
    def _route(self, url: str) -> str:
        u = urlsplit(url)  # not urlparse: it would split ";" off the last path segment
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p][1:]  # drop "v2"
        page, per_page = int(q.get("page", 1)), int(q.get("per_page", 50))

        if parts[0] == "country" and len(parts) == 2:
            codes = parts[1].split(";")
            if any(c not in COUNTRY_META for c in codes):
                return ERROR_ENVELOPE
            rows = [self._country(c) for c in codes]
            return self._paged(rows, page, per_page, last_updated=None)

        if parts[0] == "indicator" and len(parts) == 2:
            code = parts[1]
            rows = [
                {
                    "id": code,
                    "name": INDICATOR_NAMES.get(code, code),
                    "unit": "",
                    "source": {"id": "2", "value": "World Development Indicators"},
                    "sourceNote": f"Definition of {code}.",
                    "sourceOrganization": "Test org",
                    "topics": [],
                }
            ]
            return self._paged(rows, page, per_page, last_updated=None)

        if parts[0] == "country" and parts[2] == "indicator":
            codes, indicator = parts[1].split(";"), parts[3]
            start, end = (int(x) for x in q["date"].split(":"))
            rows = []
            for c in codes:  # API order: country, then year descending
                for y in range(end, start - 1, -1):
                    v = self.observations.get((indicator, c, y), NOT_RETURNED)
                    if v is NOT_RETURNED:
                        continue
                    rows.append(
                        {
                            "indicator": {
                                "id": indicator,
                                "value": INDICATOR_NAMES.get(indicator, indicator),
                            },
                            "country": {"id": COUNTRY_META[c][0], "value": COUNTRY_META[c][1]},
                            "countryiso3code": c,
                            "date": str(y),
                            "value": v,
                            "unit": "",
                            "obs_status": "",
                            "decimal": 0 if indicator == "SP.POP.TOTL" else 1,
                        }
                    )
            return self._paged(rows, page, per_page, last_updated=self.last_updated)

        return ERROR_ENVELOPE

    @staticmethod
    def _country(c: str) -> dict:
        iso2, name, rid, rname, inc, incname = COUNTRY_META[c]
        return {
            "id": c,
            "iso2Code": iso2,
            "name": name,
            "region": {"id": rid, "iso2code": "XX", "value": rname},
            "adminregion": {"id": "", "iso2code": "", "value": ""},
            "incomeLevel": {"id": inc, "iso2code": "XX", "value": incname},
            "lendingType": {"id": "IBD", "iso2code": "XF", "value": "IBRD"},
            "capitalCity": "Capital",
            "longitude": "-47.9",
            "latitude": "",
        }

    @staticmethod
    def _paged(rows: list, page: int, per_page: int, last_updated: str | None) -> str:
        total = len(rows)
        pages = max(math.ceil(total / per_page), 1) if total else 0
        chunk = rows[(page - 1) * per_page : page * per_page]
        meta = {"page": page, "pages": pages, "per_page": per_page, "total": total}
        if last_updated:
            meta.update(sourceid="2", lastupdated=last_updated)
        return json.dumps([meta, chunk if total else None])


def timeout_error() -> Exception:
    return requests.Timeout("simulated timeout")
