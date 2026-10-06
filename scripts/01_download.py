#!/usr/bin/env python3
"""Fetch UNSD's own dataflows from the UNdata SDMX API, one request per reference area.

The API is documented at https://data.un.org/Host.aspx?Content=API; its base
http://data.un.org/ws/rest/ now redirects to https://data.un.org/legacy/ws/rest/,
which is used directly. Everything to fetch is discovered from the API:

  1. dataflow/all/all/latest: the list of dataflows. The flows in
     sdmx.SCOPE must be in it; their agency and version come from it.
  2. datastructure/<agency>/<id>/<version>?references=children, for each
     flow's data structure definition: the DSD with its concept schemes and
     codelists.
  3. availableconstraint/<flow>/all/all/all: the codes actually in use in
     each dimension, the time range, and the number of observations
     (annotation "sdmx_metrics").
  4. data/<flow>/<key>: one request per reference area listed in 3, the key
     fixing REF_AREA and leaving every other dimension open, with
     "Accept: text/csv" (SDMX-CSV).

The API overwrites its data in place and keeps no versions, so a run is
filed under data/raw/<fetch date YYYY-MM-DD>/ (UTC, the day the run starts)
and the newest complete run is the current one. Layout of a run:

    MANIFEST.json
    structure/dataflow.xml
    structure/datastructure_<agency>_<id>_<version>.xml
    structure/availableconstraint_<flow>.xml
    data/<flow>/<area>.csv

MANIFEST.json is rewritten after every file, so an interrupted run resumes:
without --date, the newest run is resumed if it is not complete (otherwise
a new run starts under today's date). A file whose manifest entry exists and
whose sha256 matches is not fetched again; a run that is complete makes no
request at all.

Requests are sequential, at least --delay seconds apart (default 1.5).
500, 502, 503, 504, time-outs and broken or cut-off answers are retried
after 30, 60 and 120 seconds; a slice that still fails is recorded under
"failed" in the manifest with the error, never as an empty file, and the
next run retries it. 403 and 429 stop the run at once.

    uv run python scripts/01_download.py                        # everything (resumes)
    uv run python scripts/01_download.py --estimate-only        # plan and estimate only
    uv run python scripts/01_download.py --only DF_SEEA_AEA --limit 2
"""

import argparse
import csv
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sdmx

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = "undata-sdmx/1 (+https://github.com/yuiseki/undata-sdmx)"
CSV_ACCEPT = "text/csv"
DATA_FORMAT = "SDMX-CSV 1.0 (data query with Accept: text/csv; served as text/csv; charset=utf-8)"
STRUCTURE_FORMAT = "SDMX-ML 2.1 Structure (application/vnd.sdmx.structure+xml; version=2.1)"
AREA_DIM = "REF_AREA"
BACKOFF = (30, 60, 120)
TIMEOUT = 600
SAFE_CODE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
LICENCE = (
    "UNdata Terms of Use (https://data.un.org/Host.aspx?Content=UNdataUse): \"All data and "
    "metadata provided on UNdata's website are available free of charge and may be copied "
    "freely, duplicated and further distributed provided that UNdata is cited as the reference.\""
)
# SDMX-CSV bytes per observation, measured on one area of each flow on
# 2026-10-06 (Japan, Japan, Japan, Indonesia, Austria). Only for the estimate.
BYTES_PER_OBS = {
    "DF_UNDATA_ENERGY": 72.0,
    "DF_UNData_EnergyBalance": 81.0,
    "NA_MAIN": 112.0,
    "DF_SEEA_AEA": 97.0,
    "DF_SEEA_ENERGY": 95.0,
}
SECONDS_PER_REQUEST = 4.0  # answer time, on top of the delay; only for the estimate

_last_request = 0.0


class Stop(Exception):
    """The source refused us (403/429): stop the whole run."""


class Failed(Exception):
    """This request failed for good (after retries)."""


@dataclass
class Slice:
    flow: str
    area: str
    url: str
    path: str


def dataflow_list_url() -> str:
    return f"{sdmx.BASE}/dataflow/all/all/latest"


def datastructure_url(flow: sdmx.Dataflow) -> str:
    agency, id_, version = flow.dsd
    return f"{sdmx.BASE}/datastructure/{agency}/{id_}/{version}?references=children"


def datastructure_path(flow: sdmx.Dataflow) -> str:
    return "structure/datastructure_{}_{}_{}.xml".format(*flow.dsd)


def availableconstraint_url(flow: sdmx.Dataflow) -> str:
    return f"{sdmx.BASE}/availableconstraint/{flow.ref}/all/all/all"


def availableconstraint_path(flow: sdmx.Dataflow) -> str:
    return f"structure/availableconstraint_{flow.id}.xml"


def plan_flow(
    flow: sdmx.Dataflow,
    dsd: sdmx.DataStructure,
    av: sdmx.Available,
    areas: list[str] | None = None,
    limit: int | None = None,
) -> list[Slice]:
    """One data request per reference area in use, in the constraint's order."""
    if AREA_DIM not in dsd.dimensions:
        raise SystemExit(f"{flow.id}: no {AREA_DIM} dimension in {dsd.id}")
    codes = av.values.get(AREA_DIM) or []
    if not codes:
        raise SystemExit(f"{flow.id}: the constraint lists no {AREA_DIM}")
    out = []
    for code in codes:
        if not SAFE_CODE.fullmatch(code) or ".." in code:
            raise SystemExit(f"{flow.id}: {AREA_DIM} code {code!r} is not a safe file name")
        if areas is not None and code not in areas:
            continue
        key = sdmx.data_key(dsd.dimensions, AREA_DIM, code)
        out.append(Slice(flow.id, code, f"{sdmx.BASE}/data/{flow.ref}/{key}",
                         f"data/{flow.id}/{code}.csv"))  # fmt: skip
    return out[:limit] if limit is not None else out


def estimate_flow(flow_id: str, slices: list[Slice], av: sdmx.Available) -> tuple[int, int]:
    """(requests, bytes) for these slices: the stated observations shared evenly by area."""
    share = len(slices) / len(av.values[AREA_DIM])
    return len(slices), round(av.observations * share * BYTES_PER_OBS[flow_id])


def http_action(status: int) -> str:
    if status in (403, 429):
        return "stop"
    if status in (500, 502, 503, 504):
        return "retry"
    return "fail"


def short(body: bytes, n: int = 200) -> str:
    text = " ".join(body.decode("utf-8", "replace").split())
    return text if len(text) <= n else text[:n] + "..."


def check_csv(path: Path, dsd: sdmx.DataStructure, csv_id: str, area: str) -> int:
    """Rows of one SDMX-CSV answer, after checking its shape; ValueError if it is wrong."""
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError("the answer does not end with a line break (cut off at the end?)")
    want = dsd.csv_header()
    pos = want.index(AREA_DIM)
    with open(path, newline="", encoding="utf-8", errors="strict") as f:
        r = csv.reader(f)
        header = next(r, None)
        if header != want:
            raise ValueError(f"header {short(','.join(header or []).encode())} is not {want}")
        n = 0
        for row in r:
            n += 1
            if len(row) != len(want):
                raise ValueError(f"row {n} has {len(row)} columns, the header {len(want)}")
            if row[0] != csv_id:
                raise ValueError(f"row {n}: DATAFLOW is {row[0]}, not {csv_id}")
            if row[pos] != area:
                raise ValueError(f"row {n}: {AREA_DIM} is {row[pos]}, not {area}")
    if n == 0:
        raise ValueError("no rows")
    return n


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_current(d: Path, rel: str, entry: dict | None) -> bool:
    p = d / rel
    return bool(entry) and p.exists() and p.stat().st_size == entry["bytes"] and (
        sha256(p) == entry["sha256"]
    )


def write_manifest(path: Path, manifest: dict) -> None:
    """Write a sorted copy; the caller's dicts stay the same objects."""
    out = dict(manifest)
    out["files"] = dict(sorted(manifest["files"].items()))
    out["failed"] = dict(sorted(manifest.get("failed", {}).items()))
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def pick_run(raw: Path, today: str) -> str:
    """Resume the newest run if it is incomplete, else start one dated today."""
    runs = sorted(p.parent.name for p in raw.glob("*/MANIFEST.json"))
    if runs:
        newest = runs[-1]
        if not json.loads((raw / newest / "MANIFEST.json").read_text()).get("complete"):
            return newest
    return today


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url: str, dest: Path, accept: str | None, delay: float) -> dict:
    """One GET into dest via dest.part. Raises urllib/http errors as they come."""
    global _last_request
    wait = delay - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
            length = r.headers.get("Content-Length")
            lm = r.headers.get("Last-Modified")
            ctype = r.headers.get("Content-Type")
    finally:
        _last_request = time.monotonic()
    got = tmp.stat().st_size
    if length is not None and got != int(length):
        tmp.unlink()
        raise ValueError(f"got {got} bytes, the server stated {length}")
    return {"tmp": tmp, "last_modified": lm, "content_type": ctype, "bytes": got}


def fetch(url: str, dest: Path, accept: str | None, delay: float, check=None) -> dict:
    """GET with retries; check(tmp_path) may raise ValueError for a broken answer."""
    errors = []
    part = dest.with_name(dest.name + ".part")
    for attempt in range(len(BACKOFF) + 1):
        if attempt:
            time.sleep(BACKOFF[attempt - 1])
        try:
            got = _get(url, dest, accept, delay)
            extra = check(got["tmp"]) if check else {}
        except urllib.error.HTTPError as e:
            part.unlink(missing_ok=True)
            body = short(e.read() or b"")
            msg = f"HTTP {e.code} {body}".strip()
            action = http_action(e.code)
            if action == "stop":
                raise Stop(f"{url}: {msg}") from e
            errors.append(msg)
            if action == "fail":
                break
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException, ValueError, UnicodeDecodeError) as e:  # fmt: skip
            part.unlink(missing_ok=True)
            errors.append(f"{type(e).__name__}: {e}")
            continue
        got["tmp"].replace(dest)
        entry = {
            "url": url,
            "bytes": got["bytes"],
            "sha256": sha256(dest),
            "last_modified": got["last_modified"],
            "content_type": got["content_type"],
            "fetched_at": now(),
            "attempts": attempt + 1,
        }
        entry.update(extra or {})
        return entry
    raise Failed("; ".join(errors))


def _structure(d: Path, manifest: dict, rel: str, url: str, kind: str, delay: float) -> bytes:
    """A structure file: reused if current, else fetched. A failure stops the run."""
    files = manifest["files"]
    if not is_current(d, rel, files.get(rel)):
        try:
            entry = fetch(url, d / rel, None, delay, check=lambda p: _check_xml(p))
        except Failed as e:
            raise SystemExit(f"{url}: {e}") from e
        entry.update({"kind": kind, "format": STRUCTURE_FORMAT})
        files[rel] = entry
        write_manifest(d / "MANIFEST.json", manifest)
        print(f"{rel}: {entry['bytes']:,} bytes", flush=True)
    return (d / rel).read_bytes()


def _check_xml(p: Path) -> dict:
    import xml.etree.ElementTree as ET

    root = ET.parse(p).getroot()
    prepared = root.findtext(f"{sdmx.M}Header/{sdmx.M}Prepared")
    return {"prepared": prepared}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated dataflow ids (default: all of sdmx.SCOPE)")
    ap.add_argument("--areas", help="comma-separated REF_AREA codes")
    ap.add_argument("--limit", type=int, help="at most N areas per dataflow")
    ap.add_argument("--date", help="run directory YYYY-MM-DD (default: resume or today)")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests (>= 1)")
    ap.add_argument("--estimate-only", action="store_true", help="plan and estimate, no data")
    a = ap.parse_args()
    if a.delay < 1:
        raise SystemExit("--delay must be at least 1 second")
    only = a.only.split(",") if a.only else None
    if only and set(only) - set(sdmx.SCOPE):
        raise SystemExit(f"not in scope: {sorted(set(only) - set(sdmx.SCOPE))}")
    areas = a.areas.split(",") if a.areas else None
    restricted = bool(only or areas or a.limit is not None)

    run = a.date or pick_run(RAW, datetime.now(UTC).strftime("%Y-%m-%d"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run):
        raise SystemExit(f"--date {run}: not YYYY-MM-DD")
    d = RAW / run
    d.mkdir(parents=True, exist_ok=True)
    mpath = d / "MANIFEST.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {"files": {}, "failed": {}}
    manifest.setdefault("failed", {})
    manifest.update({
        "run": run,
        "api": sdmx.BASE,
        "api_documentation": "https://data.un.org/Host.aspx?Content=API",
        "data_format": DATA_FORMAT,
        "structure_format": STRUCTURE_FORMAT,
        "split": f"one data request per {AREA_DIM} code in the flow's available constraint",
        "user_agent": UA,
        "licence": LICENCE,
    })  # fmt: skip
    manifest.setdefault("started_at", now())
    print(f"run {run} ({'existing' if mpath.exists() else 'new'}) in {d}", flush=True)

    flows = sdmx.parse_dataflows(
        _structure(d, manifest, "structure/dataflow.xml", dataflow_list_url(), "dataflows", a.delay)
    )
    chosen = sdmx.select_flows(flows, only or sdmx.SCOPE)
    stated = manifest.setdefault("dataflows", {})
    plans: list[tuple[sdmx.Dataflow, sdmx.DataStructure, list[Slice]]] = []
    for f in chosen:
        dsd = sdmx.parse_datastructure(
            _structure(d, manifest, datastructure_path(f), datastructure_url(f),
                       "datastructure", a.delay)  # fmt: skip
        )
        av = sdmx.parse_available(
            _structure(d, manifest, availableconstraint_path(f), availableconstraint_url(f),
                       "availableconstraint", a.delay)  # fmt: skip
        )
        stated[f.id] = {
            "agency": f.agency,
            "version": f.version,
            "name": f.name,
            "datastructure": ",".join(f.dsd),
            "datastructure_file": datastructure_path(f),
            "dimensions": dsd.dimensions,
            "stated_observations": av.observations,
            "stated_time_range": list(av.time_range) if av.time_range else None,
            "stated_codes_in_use": {k: len(v) for k, v in av.values.items()},
            "areas": av.values[AREA_DIM],
        }
        plans.append((f, dsd, plan_flow(f, dsd, av, areas, a.limit)))

    files, failed = manifest["files"], manifest["failed"]
    total_req = total_bytes = todo_req = todo_bytes = 0
    print(f"{'dataflow':<26}{'areas':>7}{'to fetch':>10}{'stated obs':>13}{'est. bytes':>15}")
    for f, _dsd, slices in plans:
        av = sdmx.Available(stated[f.id]["stated_observations"], {AREA_DIM: stated[f.id]["areas"]},
                            None)  # fmt: skip
        todo = [s for s in slices if not is_current(d, s.path, files.get(s.path))]
        req, nb = estimate_flow(f.id, slices, av)
        _, tb = estimate_flow(f.id, todo, av)
        total_req, total_bytes, todo_req, todo_bytes = (
            total_req + req, total_bytes + nb, todo_req + len(todo), todo_bytes + tb)
        print(f"{f.id:<26}{len(slices):>7}{len(todo):>10}{av.observations:>13,}{nb:>15,}")
    minutes = todo_req * (a.delay + SECONDS_PER_REQUEST) / 60
    print(f"plan: {total_req} data requests, ~{total_bytes / 1e6:,.0f} MB in all; "
          f"to fetch now: {todo_req} requests, ~{todo_bytes / 1e6:,.0f} MB, ~{minutes:,.0f} min",
          flush=True)  # fmt: skip
    if a.estimate_only:
        write_manifest(mpath, manifest)
        return 0

    stopped = None
    done = 0
    for f, dsd, slices in plans:
        for s in slices:
            if is_current(d, s.path, files.get(s.path)):
                failed.pop(s.path, None)
                continue
            try:
                entry = fetch(s.url, d / s.path, CSV_ACCEPT, a.delay,
                              check=lambda p, s=s, dsd=dsd, f=f: {
                                  "rows": check_csv(p, dsd, f.csv_id, s.area)})  # fmt: skip
            except Stop as e:
                stopped = str(e)
                break
            except Failed as e:
                failed[s.path] = {"url": s.url, "status": "failed", "error": str(e),
                                  "dataflow": s.flow, "ref_area": s.area, "at": now()}  # fmt: skip
                print(f"FAILED {s.path}: {e}", flush=True)
                write_manifest(mpath, manifest)
                continue
            entry.update({"kind": "data", "format": DATA_FORMAT, "dataflow": s.flow,
                          "ref_area": s.area})  # fmt: skip
            files[s.path] = entry
            failed.pop(s.path, None)
            write_manifest(mpath, manifest)
            done += 1
            print(f"{s.path}: {entry['rows']:,} rows, {entry['bytes']:,} bytes "
                  f"({done}/{todo_req})", flush=True)  # fmt: skip
        if stopped:
            break

    all_here = all(is_current(d, s.path, files.get(s.path)) for _, _, sl in plans for s in sl)
    manifest["complete"] = bool(not restricted and not stopped and not failed and all_here)
    if manifest["complete"]:
        manifest.setdefault("completed_at", now())
    write_manifest(mpath, manifest)
    rows = sum(e.get("rows", 0) for e in files.values())
    print(f"run {run}: {done} fetched, {len(failed)} failed, {rows:,} rows in the manifest, "
          f"complete={manifest['complete']}", flush=True)  # fmt: skip
    if stopped:
        print(f"STOPPED: {stopped}", flush=True)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
