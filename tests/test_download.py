import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))
_spec = importlib.util.spec_from_file_location("download", HERE.parent / "scripts" / "01_download.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
import sdmx  # noqa: E402

FX = HERE / "fixtures"
FLOWS = sdmx.parse_dataflows((FX / "dataflow.xml").read_bytes())
DSD = sdmx.parse_datastructure((FX / "datastructure_energy.xml").read_bytes())
AV = sdmx.parse_available((FX / "availableconstraint_energy.xml").read_bytes())
ENERGY = FLOWS["DF_UNDATA_ENERGY"]


def test_structure_urls():
    assert m.dataflow_list_url() == sdmx.BASE + "/dataflow/all/all/latest"
    assert m.datastructure_url(ENERGY) == (
        sdmx.BASE + "/datastructure/UNSD/DSD_ENERGY/1.2?references=children"
    )
    assert m.availableconstraint_url(ENERGY) == (
        sdmx.BASE + "/availableconstraint/UNSD,DF_UNDATA_ENERGY,1.2/all/all/all"
    )


def test_one_data_request_per_area_listed_by_the_constraint():
    slices = m.plan_flow(ENERGY, DSD, AV)
    assert [s.area for s in slices] == ["004", "008", "012", "016"]
    s = slices[0]
    assert s.url == sdmx.BASE + "/data/UNSD,DF_UNDATA_ENERGY,1.2/.004.."
    assert s.path == "data/DF_UNDATA_ENERGY/004.csv"
    assert s.flow == "DF_UNDATA_ENERGY"


def test_plan_can_be_restricted():
    assert [s.area for s in m.plan_flow(ENERGY, DSD, AV, areas=["008", "999"])] == ["008"]
    assert [s.area for s in m.plan_flow(ENERGY, DSD, AV, limit=2)] == ["004", "008"]


def test_an_area_code_that_is_not_a_safe_file_name_stops_the_plan():
    bad = sdmx.Available(1, {"REF_AREA": ["../x"]}, None)
    with pytest.raises(SystemExit, match="../x"):
        m.plan_flow(ENERGY, DSD, bad)


def test_estimate_scales_the_stated_observations_by_the_share_of_areas():
    slices = m.plan_flow(ENERGY, DSD, AV, limit=2)
    req, nbytes = m.estimate_flow(ENERGY.id, slices, AV)
    assert req == 2
    assert nbytes == round(1919234 * 2 / 4 * m.BYTES_PER_OBS["DF_UNDATA_ENERGY"])


@pytest.mark.parametrize(
    ("status", "action"),
    [(500, "retry"), (502, "retry"), (503, "retry"), (504, "retry"),
     (403, "stop"), (429, "stop"), (404, "fail"), (400, "fail")],
)  # fmt: skip
def test_what_an_http_error_leads_to(status, action):
    assert m.http_action(status) == action


def test_backoff_grows():
    assert list(m.BACKOFF) == sorted(m.BACKOFF) and m.BACKOFF[0] >= 30


def _copy(tmp_path, text: str) -> Path:
    p = tmp_path / "s.csv"
    p.write_bytes(text.encode("utf-8"))
    return p


ENERGY_CSV = (FX / "data_DF_UNDATA_ENERGY.csv").read_text(encoding="utf-8")


def test_check_csv_counts_rows():
    n = m.check_csv(FX / "data_DF_UNDATA_ENERGY.csv", DSD, ENERGY.csv_id, "392")
    assert n == 11


def test_check_csv_refuses_another_area(tmp_path):
    with pytest.raises(ValueError, match="REF_AREA"):
        m.check_csv(_copy(tmp_path, ENERGY_CSV), DSD, ENERGY.csv_id, "004")


def test_check_csv_refuses_another_dataflow(tmp_path):
    with pytest.raises(ValueError, match="DATAFLOW"):
        m.check_csv(_copy(tmp_path, ENERGY_CSV), DSD, "UNSD:DF_UNDATA_ENERGY(1.3)", "392")


def test_check_csv_refuses_a_ragged_row(tmp_path):
    text = ENERGY_CSV.rstrip("\r\n") + ",extra\r\n"
    with pytest.raises(ValueError, match="columns"):
        m.check_csv(_copy(tmp_path, text), DSD, ENERGY.csv_id, "392")


def test_check_csv_refuses_an_unexpected_header(tmp_path):
    text = ENERGY_CSV.replace("CONVERSION_FACTOR", "OTHER", 1)
    with pytest.raises(ValueError, match="header"):
        m.check_csv(_copy(tmp_path, text), DSD, ENERGY.csv_id, "392")


@pytest.mark.parametrize("body", ["", "NoRecordsFound", ENERGY_CSV.split("\n")[0] + "\n"])
def test_check_csv_refuses_an_answer_without_rows(tmp_path, body):
    with pytest.raises(ValueError):
        m.check_csv(_copy(tmp_path, body), DSD, ENERGY.csv_id, "392")


def test_check_csv_refuses_a_cut_off_last_line(tmp_path):
    with pytest.raises(ValueError, match="end"):
        m.check_csv(_copy(tmp_path, ENERGY_CSV.rstrip("\r\n")[:-3]), DSD, ENERGY.csv_id, "392")


def test_error_bodies_are_kept_short():
    assert m.short((FX / "error_500.txt").read_bytes()) == (
        "Error executing generated SQL and populating SDMX model"
    )
    assert m.short(b"x" * 1000).endswith("...") and len(m.short(b"x" * 1000)) < 300


def _run(raw: Path, name: str, complete: bool) -> None:
    (raw / name).mkdir(parents=True)
    (raw / name / "MANIFEST.json").write_text(json.dumps({"complete": complete, "files": {}}))


def test_run_dir_is_today_when_every_run_is_complete(tmp_path):
    _run(tmp_path, "2026-10-01", True)
    assert m.pick_run(tmp_path, "2026-10-06") == "2026-10-06"


def test_run_dir_resumes_the_newest_incomplete_run(tmp_path):
    _run(tmp_path, "2026-10-01", True)
    _run(tmp_path, "2026-10-05", False)
    assert m.pick_run(tmp_path, "2026-10-06") == "2026-10-05"
    assert m.pick_run(tmp_path / "none", "2026-10-06") == "2026-10-06"


def test_run_dir_does_not_resume_an_incomplete_run_older_than_a_complete_one(tmp_path):
    _run(tmp_path, "2026-10-01", False)
    _run(tmp_path, "2026-10-05", True)
    assert m.pick_run(tmp_path, "2026-10-06") == "2026-10-06"


def test_a_file_is_current_only_with_the_same_sha256(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"abc")
    entry = {"sha256": hashlib.sha256(b"abc").hexdigest(), "bytes": 3}
    assert m.is_current(tmp_path, "a.csv", entry)
    assert not m.is_current(tmp_path, "a.csv", None)
    assert not m.is_current(tmp_path, "b.csv", entry)
    (tmp_path / "a.csv").write_bytes(b"abd")
    assert not m.is_current(tmp_path, "a.csv", entry)


def test_manifest_is_written_atomically(tmp_path):
    p = tmp_path / "MANIFEST.json"
    m.write_manifest(p, {"files": {"b": 1, "a": 2}, "failed": {}})
    assert json.loads(p.read_text()) == {"files": {"a": 2, "b": 1}, "failed": {}}
    assert list(tmp_path.iterdir()) == [p]


def test_entries_added_after_a_write_are_kept(tmp_path):
    p = tmp_path / "MANIFEST.json"
    manifest = {"files": {}, "failed": {}}
    files, failed = manifest["files"], manifest["failed"]
    files["a"] = 1
    m.write_manifest(p, manifest)
    files["b"] = 2
    failed["c"] = 3
    m.write_manifest(p, manifest)
    assert json.loads(p.read_text()) == {"files": {"a": 1, "b": 2}, "failed": {"c": 3}}
