#!/usr/bin/env python3
"""Turn the newest complete run under data/raw/ into Parquet.

    data/parquet/<dataflow>.parquet   every SDMX-CSV answer of the flow, one row per observation
    data/parquet/codelists.parquet    every code of every codelist the flows' DSDs use, with its label
    data/parquet/components.parquet   every dimension, attribute and measure of each flow, with
                                      its concept name and codelist
    data/parquet/compilers.parquet    rows per dataflow, compiling organisation and reference
                                      area, whether they are published and why

Only the newest run whose MANIFEST.json says "complete" is exported: the API
overwrites its data in place, so the newest run is the current one.

A dataflow's Parquet holds the columns of its SDMX-CSV in their order
(DATAFLOW, the dimensions, TIME_PERIOD, OBS_VALUE, the attributes), its area
files read in the manifest's order (by REF_AREA code), rows in the order the
API sent them. Labels are not joined in: join a column to codelists.parquet
through components.parquet (see the card).

Types come from the column names, never from inference. OBS_VALUE,
CONVERSION_FACTOR and PRE_BREAK_VALUE are DOUBLE; UNIT_MULT and DECIMALS are
INTEGER; every other column (codes, TIME_PERIOD such as 1990 or 2020-Q1,
comments) is text. Each value of a typed column is checked with a regular
expression before the cast; a value that does not fit stops the run with the
dataflow, the column and the value. An empty cell is NULL, and so are "NA"
and ":" in a numeric column, which the answers write in OBS_VALUE for an
observation without a value (the raw files keep them).

Only rows whose compiling organisation allows redistribution under its own
terms are written. NA_MAIN carries the organisation that compiled each row
in COMPILING_ORG (Eurostat, the OECD, UN organisations, the IMF); UNdata's
terms do not speak for them, so compiler_status() reads each (organisation,
reference area) under that organisation's terms. A flow without
COMPILING_ORG, or a row where it is empty, is UNSD's under the UNdata terms.
An organisation it does not know is left out until its terms are checked.
compilers.parquet keeps the counts and the reason for every pair, published
or not.

    uv run python scripts/03_export_parquet.py
"""

import json
import sys
from pathlib import Path

import sdmx

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "parquet"

DOUBLE_COLUMNS = {"OBS_VALUE", "CONVERSION_FACTOR", "PRE_BREAK_VALUE"}
INTEGER_COLUMNS = {"UNIT_MULT", "DECIMALS"}
# written in OBS_VALUE for an observation without a value: "NA" by the API, ":" (the
# Eurostat convention) in some SEEA answers
MISSING = ("NA", ":")
INTEGER_RE = "-?[0-9]+"
NUMBER_RE = r"-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?"
COPY = "format parquet, compression zstd, write_bloom_filter false"


def column_type(name: str) -> str:
    if name in DOUBLE_COLUMNS:
        return "DOUBLE"
    if name in INTEGER_COLUMNS:
        return "INTEGER"
    return "VARCHAR"


def _expr(label: str, name: str) -> str:
    c, t = f'"{name}"', column_type(name)
    if t == "VARCHAR":
        return c
    pattern, what = (INTEGER_RE, "an integer") if t == "INTEGER" else (NUMBER_RE, "a number")
    missing = " ".join(f"when {c} = '{v}' then null" for v in MISSING) if t == "DOUBLE" else ""
    return (
        f"case when {c} is null then null {missing} "
        f"when regexp_full_match({c}, '{pattern}') then cast({c} as {t}) "
        f"else error('{label}: {name} is not {what}: ' || {c}) end"
    )


def select_sql(con, files: list[Path], header: list[str], label: str) -> str:
    """All rows of these SDMX-CSV files, in order, typed by column name."""
    paths = "[" + ", ".join(repr(str(p)) for p in files) + "]"
    src = (
        f"read_csv({paths}, header = true, all_varchar = true, strict_mode = true, "
        "delim = ',', quote = '\"', escape = '\"', union_by_name = false)"
    )
    names = con.sql(f"select * from {src} limit 0").columns
    if names != header:
        raise SystemExit(f"{label}: header {names} is not the DSD's {header}")
    cols = [f'{_expr(label, n)} as "{n}"' for n in names]
    return f"select {', '.join(cols)} from {src}"


def codelist_rows(paths: list[Path]) -> list[tuple]:
    """(agency, id, version, codelist name, code, name, description, parent), deduplicated."""
    seen: dict[tuple, list[tuple]] = {}
    for p in paths:
        for key, cl in sdmx.parse_codelists(p.read_bytes()).items():
            rows = [(*key, cl.name, c.id, c.name, c.description, c.parent) for c in cl.codes]
            if key in seen and seen[key] != rows:
                raise SystemExit(f"codelist {','.join(key)} differs between structure files")
            seen[key] = rows
    return [r for key in sorted(seen) for r in seen[key]]


def component_rows(flow: sdmx.Dataflow, dsd: sdmx.DataStructure) -> list[tuple]:
    out = []
    for c in dsd.components:
        cl = c.codelist or (None, None, None)
        out.append((flow.id, flow.version, c.id, c.role, c.position, c.assignment,
                    c.attached_to, c.concept_name, *cl))  # fmt: skip
    return out


CODELISTS_DDL = (
    "codelist_agency varchar, codelist_id varchar, codelist_version varchar, "
    "codelist_name varchar, code varchar, name varchar, description varchar, parent varchar"
)
COMPONENTS_DDL = (
    "dataflow varchar, dataflow_version varchar, component varchar, role varchar, "
    "position integer, assignment varchar, attached_to varchar, concept_name varchar, "
    "codelist_agency varchar, codelist_id varchar, codelist_version varchar"
)


UNDATA_TERMS = "https://data.un.org/Host.aspx?Content=UNdataUse"
EUROSTAT_TERMS = "https://ec.europa.eu/eurostat/help/copyright-notice"
# Countries whose Eurostat data may be reused commercially: EU members, EFTA
# members and official EU candidate countries (Eurostat's copyright notice).
# Kosovo (XK) is a potential candidate, not a candidate, so it is not here.
EUROSTAT_NON_EU_COMMERCIAL_OK = {
    "AL", "BA", "ME", "MK", "RS", "TR", "UA", "MD", "GE",  # candidates
    "CH", "IS", "LI", "NO",  # EFTA
}  # fmt: skip
EU_AND_AGGREGATES = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
    "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    "B5", "B6", "I8",  # EU27, EU28 and euro area aggregates
}  # fmt: skip


def compiler_status(org: str | None, area: str) -> tuple[str, bool, str]:
    """(organisation, published, reason) for rows of this COMPILING_ORG and REF_AREA."""
    if org is None:
        return "UNSD", True, f"UNSD's own data under the UNdata terms of use; {UNDATA_TERMS}"
    if org == "1B0":
        return ("UN organisations", True,
                f"UN organisations' data on UNdata, under the UNdata terms of use; {UNDATA_TERMS}")  # fmt: skip
    if org == "6O0":
        return ("OECD", True, "OECD terms allow reuse, including commercial, with attribution; "
                "https://www.oecd.org/en/about/terms-conditions.html")  # fmt: skip
    if org == "4D0":
        if area in EU_AND_AGGREGATES or area in EUROSTAT_NON_EU_COMMERCIAL_OK:
            return ("Eurostat", True, "Eurostat statistical data may be reused, commercially "
                    f"too, with the source acknowledged; {EUROSTAT_TERMS}")  # fmt: skip
        return ("Eurostat", False, "Eurostat data of countries other than EU members, EFTA "
                f"members and official EU candidates may not be reused commercially; {EUROSTAT_TERMS}")  # fmt: skip
    if org == "1C0":
        return ("IMF", False, "IMF data need permission for commercial reuse and for "
                "re-publishing a substantial amount; https://www.imf.org/en/about/copyright-and-terms")  # fmt: skip
    return "unchecked", False, "the compiling organisation's terms are not checked yet"


def split_by_compiler(con, table: str, flow_id: str) -> tuple[list[tuple], str]:
    """Rows per (COMPILING_ORG, REF_AREA) with the decision, and a predicate for the published rows."""
    has = "COMPILING_ORG" in con.sql(f"select * from {table} limit 0").columns
    org = "COMPILING_ORG" if has else "null::varchar"
    counts = con.sql(
        f"select {org} as o, REF_AREA, count(*) from {table} group by all order by 2, 1"
    ).fetchall()
    rows, keep = [], []
    for o, area, n in counts:
        name, published, reason = compiler_status(o, area)
        rows.append((flow_id, o, name, area, n, published, reason))
        if published:
            keep.append(f"{area}|{o or ''}")
    if len(keep) == len(counts):
        return rows, "true"
    if not keep:
        return rows, "false"
    key = f"REF_AREA || '|' || coalesce({org}, '')"
    return rows, f"{key} in ({', '.join(repr(k) for k in keep)})"


COMPILERS_DDL = (
    "dataflow varchar, compiling_org varchar, organisation varchar, ref_area varchar, "
    "rows bigint, published boolean, reason varchar"
)


def current_run(raw: Path) -> Path:
    runs = [p.parent for p in sorted(raw.glob("*/MANIFEST.json"))
            if json.loads(p.read_text()).get("complete")]  # fmt: skip
    if not runs:
        raise SystemExit("no complete run under data/raw/; run scripts/01_download.py")
    return runs[-1]


def flow_files(d: Path, manifest: dict, flow_id: str) -> list[Path]:
    """The data files of one flow, checked against the areas the run planned."""
    entries = {k: e for k, e in manifest["files"].items()
               if e.get("kind") == "data" and e["dataflow"] == flow_id}  # fmt: skip
    want = set(manifest["dataflows"][flow_id]["areas"])
    have = {e["ref_area"] for e in entries.values()}
    if have != want:
        raise SystemExit(f"{flow_id}: areas missing {sorted(want - have)[:10]}")
    return [d / k for k in sorted(entries)]


def main() -> int:
    import duckdb

    d = current_run(RAW)
    manifest = json.loads((d / "MANIFEST.json").read_text())
    flows = sdmx.parse_dataflows((d / "structure" / "dataflow.xml").read_bytes())
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("set memory_limit = '8GB'")
    components, dsd_files, compilers = [], [], []
    for flow_id, info in manifest["dataflows"].items():
        flow = flows[flow_id]
        dsd_path = d / info["datastructure_file"]
        dsd_files.append(dsd_path)
        dsd = sdmx.parse_datastructure(dsd_path.read_bytes())
        components += component_rows(flow, dsd)
        out = OUT / f"{flow_id}.parquet"
        # one insert per file, in the manifest's order, and the copy sorted by
        # rowid: a filtered copy of a million rows wrote its row groups out of
        # order even with preserve_insertion_order on
        files = flow_files(d, manifest, flow_id)
        con.execute("drop table if exists flow_rows")
        for i, f in enumerate(files):
            sql = select_sql(con, [f], dsd.csv_header(), flow_id)
            con.execute(f"create table flow_rows as {sql}" if i == 0
                        else f"insert into flow_rows {sql}")  # fmt: skip
        rows, keep = split_by_compiler(con, "flow_rows", flow_id)
        compilers += rows
        con.execute(
            f"copy (select * from flow_rows where {keep} order by rowid) to '{out}' ({COPY})"
        )
        con.execute("drop table flow_rows")
        n = con.sql(f"select count(*) from '{out}'").fetchone()[0]
        total = sum(r[4] for r in rows)
        print(f"{d.name}: {out.name} {n:,} of {total:,} rows published, "
              f"{out.stat().st_size:,} bytes", flush=True)  # fmt: skip

    for name, ddl, rows in (
        ("codelists", CODELISTS_DDL, codelist_rows(sorted(set(dsd_files)))),
        ("components", COMPONENTS_DDL, components),
        ("compilers", COMPILERS_DDL, compilers),
    ):
        con.execute(f"create or replace table t ({ddl})")
        con.executemany(f"insert into t values ({', '.join('?' * len(rows[0]))})", rows)
        out = OUT / f"{name}.parquet"
        con.execute(f"copy t to '{out}' ({COPY})")
        print(f"{d.name}: {out.name} {len(rows):,} rows, {out.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
