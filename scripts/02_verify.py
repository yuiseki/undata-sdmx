#!/usr/bin/env python3
"""Check every run under data/raw/ against its MANIFEST.json, and the Parquet against the newest.

For each run:
  - every file has the size and sha256 in the manifest
  - every structure file is well-formed XML
  - every SDMX-CSV answer, read with the csv module, has the header its DSD
    gives, the right DATAFLOW and REF_AREA on every row, and as many rows as
    the manifest records
  - every reference area the run planned has its file, and no slice is
    recorded as failed
  - the rows of each dataflow add up to the observation count the API
    stated for it (annotation "sdmx_metrics" of the available constraint)

For the Parquet that scripts/03_export_parquet.py made from the newest
complete run:
  - each <dataflow>.parquet has the CSV's columns in order, each of the type
    column_type gives its name, and every cell equal to the CSV's (read with
    the csv module, in the same file and row order; text as text, numbers as
    Python reads them, empty and "NA" as NULL)
  - every code in a dimension column is in its codelist in codelists.parquet
  - a DuckDB with extension autoloading turned off opens every file
  - no row group carries a bloom filter

Prints one ok/FAIL line per check and exits non-zero on any failure.

    uv run python scripts/02_verify.py
"""

import csv
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import sdmx

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PQ = ROOT / "data" / "parquet"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


download = _load("download", "01_download.py")
export = _load("export", "03_export_parquet.py")


def typed(name: str, raw: str):
    """What the Parquet should hold for one CSV cell."""
    t = export.column_type(name)
    if raw == "" or (t == "DOUBLE" and raw in export.MISSING):
        return None
    if t == "INTEGER":
        return int(raw)
    if t == "DOUBLE":
        return float(raw)
    return raw


class _Checks:
    def __init__(self, prefix: str):
        self.prefix, self.failures = prefix, 0

    def __call__(self, ok: bool, what: str) -> None:
        print(("ok   " if ok else "FAIL ") + f"{self.prefix}/{what}", flush=True)
        self.failures += not ok


def verify_raw(d: Path) -> int:
    check = _Checks(d.name)
    manifest = json.loads((d / "MANIFEST.json").read_text())
    files = manifest["files"]
    flows = sdmx.parse_dataflows((d / "structure" / "dataflow.xml").read_bytes())
    dsds = {}
    bad_hash = []
    for rel, e in files.items():
        p = d / rel
        if not p.exists() or p.stat().st_size != e["bytes"] or download.sha256(p) != e["sha256"]:
            bad_hash.append(rel)
    check(not bad_hash, f"{len(files)} files: size and sha256 as in the manifest "
                        f"({bad_hash[:5] or 'all match'})")  # fmt: skip
    for rel, e in files.items():
        if rel in bad_hash:
            continue
        if e["kind"] != "data":
            try:
                ET.parse(d / rel)
            except ET.ParseError as ex:
                check(False, f"{rel}: not well-formed XML ({ex})")
    check(not manifest.get("failed"), f"no failed slices ({sorted(manifest.get('failed', {}))[:10]})")

    for flow_id, info in manifest.get("dataflows", {}).items():
        dsd = dsds.setdefault(flow_id, sdmx.parse_datastructure(
            (d / info["datastructure_file"]).read_bytes()))  # fmt: skip
        entries = {r: e for r, e in files.items()
                   if e["kind"] == "data" and e["dataflow"] == flow_id}  # fmt: skip
        missing = sorted(set(info["areas"]) - {e["ref_area"] for e in entries.values()})
        check(not missing, f"{flow_id}: a file for each of {len(info['areas'])} areas "
                           f"({missing[:10] or 'none missing'})")  # fmt: skip
        wrong, total = [], 0
        for rel, e in sorted(entries.items()):
            if rel in bad_hash:
                continue
            try:
                n = download.check_csv(d / rel, dsd, flows[flow_id].csv_id, e["ref_area"])
            except (ValueError, UnicodeDecodeError, csv.Error) as ex:
                wrong.append(f"{rel}: {ex}")
                continue
            total += n
            if n != e["rows"]:
                wrong.append(f"{rel}: {n} rows, the manifest says {e['rows']}")
        check(not wrong, f"{flow_id}: {len(entries)} SDMX-CSV files read with the csv module, "
                         f"header, DATAFLOW, REF_AREA and row counts ({wrong[:3] or 'all ok'})")
        stated = info["stated_observations"]
        check(total == stated, f"{flow_id}: {total:,} rows, the API states {stated:,} observations")
    return check.failures


def verify_parquet(d: Path) -> int:
    import duckdb

    check = _Checks(f"parquet (from {d.name})")
    manifest = json.loads((d / "MANIFEST.json").read_text())
    con = duckdb.connect()
    con.execute("set memory_limit = '8GB'")
    plain = duckdb.connect(
        config={"autoload_known_extensions": False, "autoinstall_known_extensions": False}
    )
    outs = [PQ / "codelists.parquet", PQ / "components.parquet"]
    for p in outs:
        if not p.exists():
            check(False, f"{p.name}: missing")
            return check.failures
    codes = {}
    for agency, cid, ver, code in con.sql(
        f"select codelist_agency, codelist_id, codelist_version, code from '{outs[0]}'"
    ).fetchall():
        codes.setdefault((agency, cid, ver), set()).add(code)

    for flow_id, info in manifest["dataflows"].items():
        p = PQ / f"{flow_id}.parquet"
        if not p.exists():
            check(False, f"{p.name}: missing")
            continue
        outs.append(p)
        dsd = sdmx.parse_datastructure((d / info["datastructure_file"]).read_bytes())
        header = dsd.csv_header()
        rel = con.sql(f"select * from '{p}'")
        check(rel.columns == header, f"{p.name}: the {len(header)} SDMX-CSV columns in order")
        types = dict(zip(rel.columns, (str(t) for t in rel.types), strict=True))
        wrong = {c: t for c, t in types.items() if t != export.column_type(c)}
        check(not wrong, f"{p.name}: every column of the type its name gives ({wrong or 'all'})")

        files = export.flow_files(d, manifest, flow_id)
        cur = con.execute(f"select * from '{p}'")
        n = diff = 0
        first = None
        batch, bi = [], 0
        for f in files:
            with open(f, newline="", encoding="utf-8") as fh:
                r = csv.reader(fh)
                next(r)
                for row in r:
                    if bi == len(batch):
                        batch, bi = cur.fetchmany(100_000), 0
                        if not batch:
                            diff += 1
                            break
                    got = batch[bi]
                    bi += 1
                    n += 1
                    want = tuple(typed(c, v) for c, v in zip(header, row, strict=True))
                    if want != got:
                        diff += 1
                        first = first or (f.name, n, row, got)
        extra = len(batch) - bi + len(cur.fetchall())
        check(diff == 0 and extra == 0,
              f"{p.name}: all {n:,} rows x {len(header)} cells equal the CSV "
              f"({first or 'none differ'}{f', {extra} extra rows' if extra else ''})")  # fmt: skip

        unknown = {}
        for c in dsd.components:
            if c.role != "dimension" or c.codelist is None:
                continue
            used = {v for (v,) in con.sql(f'select distinct "{c.id}" from \'{p}\'').fetchall()}
            out = sorted(v for v in used if v not in codes.get(c.codelist, set()))
            if out:
                unknown[c.id] = out[:5]
        check(not unknown, f"{p.name}: every dimension code is in its codelist ({unknown or 'all'})")

    for p in outs:
        n = plain.sql(f"select count(*) from '{p}'").fetchone()[0]
        check(n > 0, f"{p.name}: a DuckDB without extensions opens it ({n:,} rows)")
        bloom = con.sql(
            "select count(*) filter (where bloom_filter_offset is not null) "
            f"from parquet_metadata('{p}')"
        ).fetchone()[0]
        check(bloom == 0, f"{p.name}: no bloom filters")
    return check.failures


def main() -> int:
    dirs = sorted(p.parent for p in RAW.glob("*/MANIFEST.json"))
    if not dirs:
        print("FAIL no MANIFEST.json under data/raw/")
        return 1
    failures = sum(verify_raw(d) for d in dirs)
    if PQ.exists() and any(PQ.glob("*.parquet")):
        failures += verify_parquet(export.current_run(RAW))
    else:
        print("(no Parquet under data/parquet/ yet; run scripts/03_export_parquet.py)")
    print("all ok" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
