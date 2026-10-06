import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "publish", Path(__file__).parents[1] / "src" / "publish.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _run(d: Path, name: str, complete=True, raw=b"x", manifest_raw=b"x"):
    r = d / "raw" / name
    (r / "data" / "F").mkdir(parents=True)
    (r / "data" / "F" / "A.csv").write_bytes(raw)
    files = {"data/F/A.csv": {"sha256": hashlib.sha256(manifest_raw).hexdigest()}}
    (r / "MANIFEST.json").write_text(json.dumps({"complete": complete, "files": files}))


def _data(tmp_path, card_tables=("F",), runs=(("2026-10-06", True),), **kw):
    d = tmp_path / "data"
    (d / "parquet").mkdir(parents=True)
    (d / "parquet" / "F.parquet").write_bytes(b"PAR1")
    for name, complete in runs:
        _run(d, name, complete, **kw)
    card = "---\nconfigs:\n" + "".join(m.subset_entry(t) for t in card_tables) + "---\n"
    (d / "README.md").write_text(card + "files in raw/2026-10-06/\n")
    for f in ("LICENSE", "provenance.yaml"):
        (d / f).write_text("x")
    return d


def test_collect_lists_card_parquet_and_the_current_run(tmp_path):
    d = _data(tmp_path, runs=(("2026-09-01", True), ("2026-10-06", True), ("2026-10-07", False)))
    assert m.collect(d) == [
        "README.md", "LICENSE", "provenance.yaml", "parquet/F.parquet",
        "raw/2026-10-06/data/F/A.csv", "raw/2026-10-06/MANIFEST.json",
    ]  # fmt: skip


def test_a_parquet_the_card_does_not_declare_stops_it(tmp_path):
    with pytest.raises(SystemExit, match="'F'"):
        m.collect(_data(tmp_path, card_tables=("other",)))


def test_a_raw_file_unlike_its_manifest_stops_it(tmp_path):
    with pytest.raises(SystemExit, match="raw/2026-10-06/data/F/A.csv"):
        m.collect(_data(tmp_path, raw=b"changed"))


def test_a_card_that_does_not_name_the_current_run_stops_it(tmp_path):
    d = _data(tmp_path, runs=(("2026-10-06", True), ("2026-11-06", True)))
    with pytest.raises(SystemExit, match="2026-11-06"):
        m.collect(d)


def test_no_complete_run_stops_it(tmp_path):
    with pytest.raises(SystemExit, match="complete"):
        m.collect(_data(tmp_path, runs=(("2026-10-06", False),)))


def test_older_runs_are_removed_from_the_hub():
    assert "raw/**" in m.DELETE_PATTERNS and "parquet/**" in m.DELETE_PATTERNS
