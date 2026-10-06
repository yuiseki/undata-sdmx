import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))
_spec = importlib.util.spec_from_file_location("verify", HERE.parent / "scripts" / "02_verify.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

FX = HERE / "fixtures"


@pytest.mark.parametrize(
    ("name", "raw", "want"),
    [
        ("REF_AREA", "392", "392"),
        ("COMMENT_TS", "", None),
        ("OBS_VALUE", "7985", 7985.0),
        ("OBS_VALUE", "1.5E-05", 1.5e-05),
        ("OBS_VALUE", "NA", None),
        ("OBS_VALUE", ":", None),
        ("OBS_VALUE", "", None),
        ("UNIT_MULT", "3", 3),
        ("CONVERSION_FACTOR", "23.4310278", 23.4310278),
    ],
)
def test_a_csv_cell_is_read_as_the_parquet_should_hold_it(name, raw, want):
    got = m.typed(name, raw)
    assert got == want and type(got) is type(want)


def _run(tmp_path, rows=11, failed=None, tamper=False):
    d = tmp_path / "2026-10-06"
    (d / "structure").mkdir(parents=True)
    (d / "data" / "DF_UNDATA_ENERGY").mkdir(parents=True)
    files = {}
    for src, rel, extra in (
        ("dataflow.xml", "structure/dataflow.xml", {"kind": "dataflows"}),
        ("datastructure_energy.xml", "structure/datastructure_UNSD_DSD_ENERGY_1.2.xml",
         {"kind": "datastructure"}),
        ("availableconstraint_energy.xml", "structure/availableconstraint_DF_UNDATA_ENERGY.xml",
         {"kind": "availableconstraint"}),
        ("data_DF_UNDATA_ENERGY.csv", "data/DF_UNDATA_ENERGY/392.csv",
         {"kind": "data", "dataflow": "DF_UNDATA_ENERGY", "ref_area": "392", "rows": rows}),
    ):  # fmt: skip
        shutil.copy(FX / src, d / rel)
        b = (d / rel).read_bytes()
        files[rel] = {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest(), **extra}
    if tamper:
        (d / "data" / "DF_UNDATA_ENERGY" / "392.csv").write_bytes(b"x")
    manifest = {
        "complete": True,
        "files": files,
        "failed": failed or {},
        "dataflows": {
            "DF_UNDATA_ENERGY": {
                "datastructure_file": "structure/datastructure_UNSD_DSD_ENERGY_1.2.xml",
                "areas": ["392"],
                "stated_observations": 11,
            }
        },
    }
    (d / "MANIFEST.json").write_text(json.dumps(manifest))
    return d


def test_a_sound_run_passes(tmp_path):
    assert m.verify_raw(_run(tmp_path)) == 0


def test_a_changed_file_fails(tmp_path):
    assert m.verify_raw(_run(tmp_path, tamper=True)) > 0


def test_a_row_count_unlike_the_manifest_fails(tmp_path):
    assert m.verify_raw(_run(tmp_path, rows=12)) > 0


def test_a_failed_slice_fails(tmp_path):
    failed = {"data/DF_UNDATA_ENERGY/004.csv": {"status": "failed", "error": "HTTP 500"}}
    assert m.verify_raw(_run(tmp_path, failed=failed)) > 0


def test_a_missing_area_fails(tmp_path):
    d = _run(tmp_path)
    man = json.loads((d / "MANIFEST.json").read_text())
    man["dataflows"]["DF_UNDATA_ENERGY"]["areas"].append("004")
    (d / "MANIFEST.json").write_text(json.dumps(man))
    assert m.verify_raw(d) > 0


def test_rows_unlike_the_stated_observations_fail(tmp_path):
    d = _run(tmp_path)
    man = json.loads((d / "MANIFEST.json").read_text())
    man["dataflows"]["DF_UNDATA_ENERGY"]["stated_observations"] = 12
    (d / "MANIFEST.json").write_text(json.dumps(man))
    assert m.verify_raw(d) > 0


@pytest.mark.parametrize(
    ("org", "area", "want"),
    [("4D0", "DE", True), ("4D0", "XK", False), ("1C0", "TH", False), ("", "392", True)],
)
def test_a_csv_row_is_expected_in_the_parquet_only_when_its_compiler_is_published(org, area, want):
    header = ["DATAFLOW", "REF_AREA", "OBS_VALUE", "COMPILING_ORG"]
    assert m.published_row(header, ["NA_MAIN", area, "1", org]) is want


def test_a_row_of_a_flow_without_compiling_org_is_published():
    assert m.published_row(["DATAFLOW", "REF_AREA", "OBS_VALUE"], ["F", "392", "1"]) is True
