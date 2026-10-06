#!/usr/bin/env python3
"""Push the card, the licence, the provenance, the Parquet and the current run to the Hub.

The API overwrites its data in place, so the Hub holds one run only: the
newest complete run under data/raw/. Its raw files go up unchanged after
their sha256 is checked against its MANIFEST.json, and the files of older
runs are deleted on the Hub in the same commit (the data follows upstream
corrections). The card must declare every Parquet file under data/parquet/
as a subset and must name the run (raw/<YYYY-MM-DD>/).

A raw SDMX-CSV file goes up only when every row of it is published: an
area with any row left out for its compiling organisation's terms
(published = false in parquet/compilers.parquet) keeps its file local. The
manifest goes up whole, so the withheld files stay listed with their
hashes.

    uv run python src/publish.py             # dry run: list the files and their total size
    uv run python src/publish.py --push
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPO = "yuiseki/undata-sdmx"
DELETE_PATTERNS = ["raw/**", "parquet/**"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def subset_entry(table: str) -> str:
    return f"- config_name: '{table}'\n  data_files: parquet/{table}.parquet\n"


def current_run(data: Path) -> Path:
    runs = [p.parent for p in sorted((data / "raw").glob("*/MANIFEST.json"))
            if json.loads(p.read_text()).get("complete")]  # fmt: skip
    if not runs:
        raise SystemExit("no complete run under data/raw/; run scripts/01_download.py")
    return runs[-1]


def withheld(data: Path) -> set[tuple[str, str]]:
    """(dataflow, ref_area) of the raw files with any row left out of the Parquet."""
    import duckdb

    p = data / "parquet" / "compilers.parquet"
    if not p.exists():
        raise SystemExit("no parquet/compilers.parquet; run scripts/03_export_parquet.py")
    return set(duckdb.sql(f"select dataflow, ref_area from '{p}' where not published").fetchall())


def collect(data: Path) -> list[str]:
    """The files to upload, relative to data/; stops on anything the card or a manifest refutes."""
    card = (data / "README.md").read_text(encoding="utf-8")
    files = ["README.md", "LICENSE", "provenance.yaml"]
    parquet = sorted((data / "parquet").glob("*.parquet"))
    if not parquet:
        raise SystemExit("no Parquet under data/parquet/; run scripts/03_export_parquet.py")
    for p in parquet:
        if subset_entry(p.stem) not in card:
            raise SystemExit(f"the card does not declare the subset '{p.stem}'")
        files.append(p.relative_to(data).as_posix())
    d = current_run(data)
    if f"raw/{d.name}/" not in card:
        raise SystemExit(f"the card does not name the current run raw/{d.name}/")
    hold = withheld(data)
    for name, e in json.loads((d / "MANIFEST.json").read_text())["files"].items():
        if e.get("kind") == "data" and (e["dataflow"], e["ref_area"]) in hold:
            continue
        if sha256(d / name) != e["sha256"]:
            raise SystemExit(f"raw/{d.name}/{name} is not the file in its manifest")
        files.append(f"raw/{d.name}/{name}")
    files.append(f"raw/{d.name}/MANIFEST.json")
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    files = collect(DATA)
    total = 0
    for f in files:
        total += (DATA / f).stat().st_size
    shown = [f for f in files if not f.startswith("raw/") or f.endswith("MANIFEST.json")]
    for f in shown:
        print(f"  {(DATA / f).stat().st_size:>12,}  {f}")
    print(f"  ({len(files) - len(shown)} raw files not listed)")
    print(f"{a.repo}\n  {len(files)} files, {total:,} bytes ({total / 1e6:.1f} MB)")
    print(f"  remote files matching {DELETE_PATTERNS} and not uploaded are deleted")
    if not a.push:
        print("\ndry run. pass --push to upload")
        return 0

    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(a.repo, repo_type="dataset", exist_ok=True, private=False)
    api.upload_folder(
        folder_path=str(DATA), repo_id=a.repo, repo_type="dataset", allow_patterns=files,
        delete_patterns=DELETE_PATTERNS,
    )  # fmt: skip
    print(f"\npushed to https://huggingface.co/datasets/{a.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
