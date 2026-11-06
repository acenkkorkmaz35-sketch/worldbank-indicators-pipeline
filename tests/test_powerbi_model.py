"""Contract between the Power BI semantic model (powerbi/, TMDL) and the star-schema export.

Power BI Desktop is not available in CI, so this checks what can be checked offline: every
column the model reads exists in the exported CSV, relationships point at real columns,
every referenced table has a definition, measures only reference model objects, and the
project's JSON files parse.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from wb_pipeline.export import BI_TABLES

REPO = Path(__file__).resolve().parents[1]
PBI = REPO / "powerbi"
DEF = PBI / "WorldBankIndicators.SemanticModel" / "definition"
SAMPLE = REPO / "output" / "powerbi"


def tmdl_tables() -> dict[str, dict]:
    tables = {}
    for f in sorted((DEF / "tables").glob("*.tmdl")):
        text = f.read_text(encoding="utf-8")
        name = re.search(r"^table (\w+)$", text, re.M).group(1)
        tables[name] = {
            "file": f.stem,
            "columns": re.findall(r"^\tcolumn (\w+)$", text, re.M),
            "source_columns": re.findall(r"^\t\tsourceColumn: (\w+)$", text, re.M),
            "measures": re.findall(r"^\tmeasure '?([^'=]+?)'? =", text, re.M),
            "csv": re.findall(r'WbCsv\("([\w.]+)"\)', text),
            "text": text,
        }
    return tables


def test_tables_match_the_export_and_model_refs():
    tables = tmdl_tables()
    refs = re.findall(r"^ref table (\w+)$", (DEF / "model.tmdl").read_text(encoding="utf-8"), re.M)
    assert sorted(refs) == sorted(tables) == sorted(BI_TABLES)
    for name, t in tables.items():
        assert t["file"] == name
        assert t["csv"] == [f"{name}.csv"]


@pytest.mark.parametrize("table", sorted(BI_TABLES))
def test_every_model_column_exists_in_the_exported_csv(table):
    with open(SAMPLE / f"{table}.csv", encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    t = tmdl_tables()[table]
    assert t["columns"] == t["source_columns"]
    assert t["columns"] == header, "model columns must match the export, in order"


def test_relationships_point_at_existing_columns():
    tables = tmdl_tables()
    text = (DEF / "relationships.tmdl").read_text(encoding="utf-8")
    pairs = re.findall(r"fromColumn: (\w+)\.(\w+)\n\ttoColumn: (\w+)\.(\w+)", text)
    assert len(pairs) == 3
    for ft, fc, tt, tc in pairs:
        assert ft == "fct_indicator_value"
        assert fc in tables[ft]["columns"] and tc in tables[tt]["columns"]


def test_measures_reference_only_model_objects():
    tables = tmdl_tables()
    measures = {m for t in tables.values() for m in t["measures"]}
    assert {"Indicator Value", "YoY %", "Completeness %"} <= measures
    columns = {(t, c) for t, spec in tables.items() for c in spec["columns"]}
    text = tables["fct_indicator_value"]["text"]
    for table, column in re.findall(r"\b(dim_\w+|fct_\w+)\[(\w+)\]", text):
        assert (table, column) in columns, f"{table}[{column}] is not a model column"
    for ref in re.findall(r"(?<![\w\]])\[([^\]@]+)\]", text):
        assert ref in measures or any(ref == c for _, c in columns), f"[{ref}] is not a measure"


def test_project_files_are_valid_json():
    for f in [*PBI.rglob("*.pbip"), *PBI.rglob("*.pbir"), *PBI.rglob("*.pbism"), *PBI.rglob("report.json")]:
        json.loads(f.read_text(encoding="utf-8"))
    pbir = json.loads((PBI / "WorldBankIndicators.Report" / "definition.pbir").read_text(encoding="utf-8"))
    assert (PBI / "WorldBankIndicators.Report" / pbir["datasetReference"]["byPath"]["path"]).is_dir()


def test_tmdl_uses_tab_indentation():
    for f in DEF.rglob("*.tmdl"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            assert not line.startswith(" "), f"{f.name}:{n} indents with spaces (TMDL expects tabs)"
