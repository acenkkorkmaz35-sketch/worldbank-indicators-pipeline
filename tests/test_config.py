import pytest
from pydantic import ValidationError

from wb_pipeline.config import PipelineConfig, load_config


def _base() -> dict:
    return load_config().model_dump()


def test_production_config_is_valid():
    cfg = load_config()
    assert cfg.scope.countries == ["BRA", "CAN", "DEU", "GBR", "IND", "JPN", "MEX", "USA", "ZAF"]
    assert cfg.scope.years.years == list(range(2018, 2025))
    assert {i.code for i in cfg.scope.indicators} == {
        "SP.POP.TOTL",
        "NY.GDP.MKTP.CD",
        "NY.GDP.PCAP.CD",
        "SP.DYN.LE00.IN",
    }


def test_country_codes_are_normalised_and_validated():
    data = _base()
    data["scope"]["countries"] = [" bra ", "usa"]
    assert PipelineConfig.model_validate(data).scope.countries == ["BRA", "USA"]

    data["scope"]["countries"] = ["BR"]
    with pytest.raises(ValidationError, match="ISO3"):
        PipelineConfig.model_validate(data)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["scope"]["years"].update(start=2025, end=2018), "must be <="),
        (lambda d: d["scope"]["countries"].append("BRA"), "duplicate country"),
        (
            lambda d: d["scope"]["indicators"][2]["aggregation"].update(denominator="XX.UNKNOWN"),
            "not an indicator in scope",
        ),
        (lambda d: d["scope"]["indicators"][3]["aggregation"].update(weight=None), "needs weight"),
        (lambda d: d.update(unexpected_key=1), "Extra inputs"),
    ],
)
def test_invalid_configs_are_rejected(mutate, message):
    data = _base()
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        PipelineConfig.model_validate(data)


def test_fingerprint_changes_with_scope():
    a = load_config()
    data = a.model_dump()
    data["scope"]["countries"] = data["scope"]["countries"] + ["FRA"]
    assert PipelineConfig.model_validate(data).fingerprint() != a.fingerprint()
