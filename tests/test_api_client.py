import json

import pytest

from wb_pipeline.api_client import WorldBankAPIError, WorldBankClient

from conftest import client_for, make_config
from fake_api import ERROR_ENVELOPE, FakeWorldBank, timeout_error

DATE = "2021:2023"


def test_paging_collects_every_page(tmp_path, fake_api):
    cfg = make_config(tmp_path, per_page=2)
    pages = client_for(cfg, fake_api).fetch_observations("SP.POP.TOTL", ["BRA", "IND", "USA"], DATE)

    assert [p.page for p in pages] == [1, 2, 3, 4, 5]  # 9 rows / 2 per page
    assert sum(len(p.rows) for p in pages) == 9
    assert len({p.request_url for p in pages}) == 5  # each page traceable to its URL
    keys = {(r["countryiso3code"], r["date"]) for p in pages for r in p.rows}
    assert len(keys) == 9  # no row lost or duplicated


def test_request_url_is_deterministic(cfg, fake_api):
    client = client_for(cfg, fake_api)
    a = client.fetch_observations("SP.POP.TOTL", ["USA", "BRA"], DATE)[0].request_url
    b = client.fetch_observations("SP.POP.TOTL", ["BRA", "USA"], DATE)[0].request_url
    assert a == b  # country order in config must not change raw-layer identity


def test_error_envelope_with_http_200_is_an_error(cfg):
    fake = FakeWorldBank(failures={"/country/XXX": [ERROR_ENVELOPE]})
    with pytest.raises(WorldBankAPIError, match="120:Invalid value"):
        client_for(cfg, fake).fetch_countries(["XXX"])


def test_transient_failures_are_retried_with_backoff(cfg, fake_api):
    fake_api.failures = {"SP.POP.TOTL": [503, timeout_error(), "<html>busy</html>"]}
    delays = []
    client = WorldBankClient(
        cfg.source.model_copy(update={"backoff_seconds": 1.0}), session=fake_api, sleep=delays.append
    )

    pages = client.fetch_observations("SP.POP.TOTL", ["BRA"], DATE)

    assert sum(len(p.rows) for p in pages) == 3
    assert len(fake_api.calls) == 4  # 3 failures + 1 success
    assert len(delays) == 3
    assert delays[0] < delays[1] < delays[2]  # exponential backoff


def test_gives_up_after_max_retries(cfg, fake_api):
    fake_api.failures = {"SP.POP.TOTL": [503] * 10}
    with pytest.raises(WorldBankAPIError, match="giving up after 4 attempts"):
        client_for(cfg, fake_api).fetch_observations("SP.POP.TOTL", ["BRA"], DATE)
    assert len(fake_api.calls) == cfg.source.max_retries + 1


def test_client_errors_fail_fast(cfg, fake_api):
    fake_api.failures = {"SP.POP.TOTL": [404]}
    with pytest.raises(WorldBankAPIError, match="HTTP 404"):
        client_for(cfg, fake_api).fetch_observations("SP.POP.TOTL", ["BRA"], DATE)
    assert len(fake_api.calls) == 1


def test_row_count_must_match_paging_metadata(cfg):
    lying = json.dumps([{"page": 1, "pages": 1, "per_page": 1000, "total": 5}, [{"x": 1}]])
    fake = FakeWorldBank(failures={"SP.POP.TOTL": [lying]})
    with pytest.raises(WorldBankAPIError, match="expected 5 rows"):
        client_for(cfg, fake).fetch_observations("SP.POP.TOTL", ["BRA"], DATE)


def test_paging_metadata_changing_mid_request_is_rejected(tmp_path, fake_api):
    cfg = make_config(tmp_path, per_page=2)
    page2_with_new_total = json.dumps([{"page": 2, "pages": 6, "per_page": 2, "total": 11}, [{}, {}]])
    fake_api.failures = {"page=2&": [page2_with_new_total]}
    with pytest.raises(WorldBankAPIError, match="paging metadata changed"):
        client_for(cfg, fake_api).fetch_observations("SP.POP.TOTL", ["BRA", "IND", "USA"], DATE)


def test_empty_result_is_valid(cfg):
    fake = FakeWorldBank(observations={})  # API answers [meta(total=0), null]
    pages = client_for(cfg, fake).fetch_observations("SP.POP.TOTL", ["BRA"], DATE)
    assert len(pages) == 1 and pages[0].rows == [] and pages[0].total == 0
