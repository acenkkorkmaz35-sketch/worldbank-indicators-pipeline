"""Raw-layer behaviour: idempotent landing, atomic requests, failed-run handling."""

import pytest

from wb_pipeline.api_client import WorldBankAPIError
from wb_pipeline.ingest import run_ingestion

from conftest import client_for, make_config, query


def test_rerun_with_unchanged_source_stores_no_new_payloads(cfg, fake_api):
    run_ingestion(cfg, client_for(cfg, fake_api))
    first = query(cfg, "select count(*) from raw.api_response")[0][0]
    run_ingestion(cfg, client_for(cfg, fake_api))

    assert query(cfg, "select count(*) from raw.api_response")[0][0] == first == 9
    # ...but every fetch is still logged, so each run's lineage is complete
    assert query(cfg, "select count(distinct run_id), count(*) from raw.api_fetch_log")[0] == (2, 18)
    assert query(cfg, "select list(status) from raw.ingestion_run")[0][0] == ["success", "success"]


def test_changed_source_payload_is_stored_as_new_version(cfg, fake_api):
    run_ingestion(cfg, client_for(cfg, fake_api))
    fake_api.observations[("SP.POP.TOTL", "BRA", 2023)] += 1  # source revision
    run_ingestion(cfg, client_for(cfg, fake_api))

    # one new payload for the revised request; the old one is kept for audit/replay
    assert query(cfg, "select count(*) from raw.api_response")[0][0] == 10
    assert (
        query(
            cfg,
            "select count(*) from raw.api_response where entity = 'SP.POP.TOTL' and endpoint = 'observation'",
        )[0][0]
        == 2
    )


def test_failed_run_is_recorded_and_leaves_no_partial_request(tmp_path, fake_api):
    cfg = make_config(tmp_path, per_page=2)
    run_ingestion(cfg, client_for(cfg, fake_api))  # baseline success

    # page 3 of GDP fails permanently -> GDP request must not be half written
    fake_api.failures = {"NY.GDP.MKTP.CD?date=2021:2023&format=json&page=3": [404]}
    with pytest.raises(WorldBankAPIError):
        run_ingestion(cfg, client_for(cfg, fake_api))

    failed_run = query(cfg, "select run_id from raw.ingestion_run where status = 'failed'")[0][0]
    gdp_pages = query(
        cfg,
        f"select count(*) from raw.api_fetch_log where run_id = '{failed_run}' "
        "and request_key like 'observation|indicator=NY.GDP.MKTP.CD%'",
    )[0][0]
    assert gdp_pages == 0
    assert query(cfg, "select error from raw.ingestion_run where status = 'failed'")[0][0].startswith(
        "HTTP 404"
    )


def test_scope_tables_mirror_config(cfg, fake_api):
    run_ingestion(cfg, client_for(cfg, fake_api))
    assert query(cfg, "select list(country_iso3 order by sort_order) from meta.scope_country")[0][0] == [
        "BRA",
        "IND",
        "USA",
    ]
    assert query(cfg, "select list(year order by year) from meta.scope_year")[0][0] == [2021, 2022, 2023]
    assert query(
        cfg,
        "select aggregation_method, numerator_code, denominator_code from meta.scope_indicator "
        "where indicator_code = 'NY.GDP.PCAP.CD'",
    )[0] == ("ratio", "NY.GDP.MKTP.CD", "SP.POP.TOTL")
