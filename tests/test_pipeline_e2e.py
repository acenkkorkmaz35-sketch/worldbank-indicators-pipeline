"""End-to-end: fake API -> raw -> dbt (models, snapshot, DQ tests) -> reporting, fully offline.

These tests are the evidence for the properties the brief cares about most: idempotent
re-processing, explicit missing-data semantics, corrections / late data, DQ failures that
stop bad data, and scope changes through configuration only.
"""

from __future__ import annotations

import pytest

from wb_pipeline.export import export_reports
from wb_pipeline.ingest import run_ingestion
from wb_pipeline.transform import run_dbt_build

from conftest import client_for, make_config, query
from fake_api import NOT_RETURNED, FakeWorldBank, base_observations

pytestmark = pytest.mark.integration


def build(cfg, tmp_path):
    return run_dbt_build(
        cfg, ["--target-path", str(tmp_path / "dbt_target"), "--log-path", str(tmp_path / "dbt_logs")]
    )


def run_all(cfg, fake, tmp_path):
    run_ingestion(cfg, client_for(cfg, fake))
    return build(cfg, tmp_path)


def failing(result):
    return {t.name for t in result.tests if t.status in ("fail", "error")}


def warnings(result):
    return {t.name for t in result.tests if t.status == "warn"}


# ---------------------------------------------------------------------------------------------

def test_full_build_then_rerun_is_idempotent(cfg, fake_api, tmp_path):
    first = run_all(cfg, fake_api, tmp_path)
    assert first.success, failing(first)
    assert not warnings(first)

    fct_before = query(cfg, "select indicator_value_id, row_hash, last_changed_at from core.fct_indicator_value order by 1")
    assert len(fct_before) == 3 * 4 * 3  # countries x indicators x years

    second = run_all(cfg, fake_api, tmp_path)
    assert second.success

    fct_after = query(cfg, "select indicator_value_id, row_hash, last_changed_at from core.fct_indicator_value order by 1")
    assert fct_after == fct_before                                   # not a single row touched
    assert query(cfg, "select count(*) from snapshots.snap_indicator_value")[0][0] == 36
    assert query(cfg, "select count(*) from reporting.rpt_source_revisions")[0][0] == 0


def test_missing_values_are_explicit_never_zero(cfg, tmp_path):
    obs = base_observations(["BRA", "IND", "USA"], range(2021, 2024))
    obs[("SP.DYN.LE00.IN", "IND", 2023)] = NOT_RETURNED   # not published yet
    obs[("SP.DYN.LE00.IN", "BRA", 2022)] = None           # null in source, gap in series
    fake = FakeWorldBank(observations=obs)

    result = run_all(cfg, fake, tmp_path)
    assert result.success, failing(result)

    rows = query(cfg, """
        select country_iso3, year, value, is_missing, missing_reason
        from core.fct_indicator_value where indicator_code = 'SP.DYN.LE00.IN' and is_missing
        order by 1, 2""")
    assert rows == [("BRA", 2022, None, True, "null_in_source"),
                    ("IND", 2023, None, True, "not_returned_by_source")]

    gaps = query(cfg, "select country_iso3, year, gap_type from reporting.rpt_missing_data order by 1")
    assert gaps == [("BRA", 2022, "gap_in_series"), ("IND", 2023, "not_yet_published")]

    yoy = dict(query(cfg, """
        select country_iso3 || '-' || year, yoy_status from reporting.rpt_yoy_change
        where indicator_code = 'SP.DYN.LE00.IN' and country_iso3 = 'BRA'"""))
    assert yoy == {"BRA-2021": "no_prior_year_in_scope", "BRA-2022": "current_value_missing",
                   "BRA-2023": "prior_value_missing"}

    # gap-tolerant change still available for BRA 2023 (vs 2021)
    assert query(cfg, """select last_available_prior_year from reporting.rpt_yoy_change
                        where indicator_code = 'SP.DYN.LE00.IN' and country_iso3 = 'BRA' and year = 2023""")[0][0] == 2021

    # latest-year comparison: 2023 is latest-any, 2021 is the latest complete year
    latest = query(cfg, """select distinct latest_year, latest_complete_year from reporting.rpt_latest_year_comparison
                          where indicator_code = 'SP.DYN.LE00.IN'""")
    assert latest == [(2023, 2021)]

    # income-group row with a missing input is flagged incomplete, not silently averaged
    lmc = query(cfg, """select countries_in_group, countries_contributing, group_value, is_complete
                       from reporting.rpt_income_group_by_year
                       where income_group_code = 'LMC' and indicator_code = 'SP.DYN.LE00.IN' and year = 2023""")
    assert lmc == [(1, 0, None, False)]


def test_zero_prior_value_gives_null_pct_change(cfg, tmp_path):
    obs = base_observations(["BRA", "IND", "USA"], range(2021, 2024))
    obs[("NY.GDP.MKTP.CD", "USA", 2021)] = 0.0
    obs[("NY.GDP.PCAP.CD", "USA", 2021)] = 0.0   # keep the ratio consistent
    result = run_all(cfg, FakeWorldBank(observations=obs), tmp_path)
    assert result.success, failing(result)

    row = query(cfg, """select prior_year_value, abs_change, pct_change, yoy_status
                       from reporting.rpt_yoy_change
                       where country_iso3 = 'USA' and indicator_code = 'NY.GDP.MKTP.CD' and year = 2022""")[0]
    assert row[0] == 0.0 and row[1] > 0 and row[2] is None and row[3] == "prior_value_zero"


def test_corrections_and_late_arriving_values_are_merged_and_audited(cfg, tmp_path):
    obs = base_observations(["BRA", "IND", "USA"], range(2021, 2024))
    obs[("SP.DYN.LE00.IN", "IND", 2023)] = NOT_RETURNED
    fake = FakeWorldBank(observations=obs)
    assert run_all(cfg, fake, tmp_path).success
    before = dict(query(cfg, "select indicator_value_id, last_changed_at from core.fct_indicator_value"))

    # next release: IND 2023 arrives late, USA 2022 life expectancy is revised
    fake.observations[("SP.DYN.LE00.IN", "IND", 2023)] = 70.5
    fake.observations[("SP.DYN.LE00.IN", "USA", 2022)] = 77.0
    fake.last_updated = "2026-11-01"
    assert run_all(cfg, fake, tmp_path).success

    after = dict(query(cfg, "select indicator_value_id, last_changed_at from core.fct_indicator_value"))
    changed = {k for k in after if after[k] != before[k]}
    assert len(after) == 36 and len(changed) == 2          # MERGE touched exactly the two rows

    assert query(cfg, """select value, missing_reason from core.fct_indicator_value
                        where country_iso3 = 'IND' and indicator_code = 'SP.DYN.LE00.IN' and year = 2023""") == [(70.5, None)]

    revisions = query(cfg, """select country_iso3, year, change_type, old_value, new_value
                             from reporting.rpt_source_revisions order by 1""")
    assert revisions == [("IND", 2023, "late_arriving_value", None, 70.5),
                         ("USA", 2022, "value_revised", 78.1, 77.0)]
    assert query(cfg, "select count(*) from snapshots.snap_indicator_value")[0][0] == 38


def test_inconsistent_related_indicators_fail_the_build(cfg, tmp_path):
    obs = base_observations(["BRA", "IND", "USA"], range(2021, 2024))
    obs[("NY.GDP.PCAP.CD", "BRA", 2022)] *= 1.10   # 10% off GDP / population
    result = run_all(cfg, FakeWorldBank(observations=obs), tmp_path)

    assert not result.success
    assert "assert_ratio_indicators_reconcile" in failing(result)


def test_out_of_range_values_fail_the_build(cfg, tmp_path):
    obs = base_observations(["BRA", "IND", "USA"], range(2021, 2024))
    obs[("SP.DYN.LE00.IN", "USA", 2023)] = 780.0    # unit/decimal error upstream
    result = run_all(cfg, FakeWorldBank(observations=obs), tmp_path)
    assert "assert_values_within_configured_range" in failing(result)


def test_ratio_component_gap_is_a_warning_not_a_failure(cfg, tmp_path):
    obs = base_observations(["BRA", "IND", "USA"], range(2021, 2024))
    obs[("NY.GDP.MKTP.CD", "IND", 2023)] = None     # GDP missing, per capita present
    result = run_all(cfg, FakeWorldBank(observations=obs), tmp_path)
    assert result.success
    assert "warn_ratio_components_coverage_mismatch" in warnings(result)


def test_adding_and_removing_a_country_is_config_only(tmp_path):
    fake = FakeWorldBank(observations=base_observations(["BRA", "IND", "USA", "DEU"], range(2021, 2024)))

    cfg3 = make_config(tmp_path, countries=["BRA", "IND", "USA"])
    assert run_all(cfg3, fake, tmp_path).success

    cfg4 = make_config(tmp_path, countries=["BRA", "IND", "USA", "DEU"])
    assert run_all(cfg4, fake, tmp_path).success
    assert query(cfg4, "select count(*), count(distinct country_iso3) from core.fct_indicator_value")[0] == (48, 4)
    assert query(cfg4, "select income_group_code from core.dim_country where country_iso3 = 'DEU'")[0][0] == "HIC"

    cfg2 = make_config(tmp_path, countries=["BRA", "USA"])
    assert run_all(cfg2, fake, tmp_path).success
    assert query(cfg2, "select count(*), count(distinct country_iso3) from core.fct_indicator_value")[0] == (24, 2)


def test_reports_export_to_csv(cfg, fake_api, tmp_path):
    assert run_all(cfg, fake_api, tmp_path).success
    files = export_reports(cfg)
    assert {f.name for f in files} >= {
        "rpt_missing_data.csv", "rpt_yoy_change.csv",
        "rpt_latest_year_comparison.csv", "rpt_income_group_by_year.csv",
    }
    yoy = (cfg.output_dir / "rpt_yoy_change.csv").read_text(encoding="utf-8").splitlines()
    assert yoy[0].startswith("country_iso3,") and len(yoy) == 1 + 36
