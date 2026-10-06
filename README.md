# undata-sdmx

A reproducible copy of the dataflows of UNSD's own data that the UNdata SDMX
API serves: energy statistics, energy balances, national accounts main
aggregates, and the SEEA air emission and physical energy flow accounts.

- API: <https://data.un.org/Host.aspx?Content=API> (REST base
  `http://data.un.org/ws/rest/`, which now redirects to
  `https://data.un.org/legacy/ws/rest/`; the scripts use the latter)
- Producer: United Nations Statistics Division (UNSD), Development Data Section
- Dataset: <https://huggingface.co/datasets/yuiseki/undata-sdmx>

## What is fetched

Everything is discovered from the API itself:

1. `dataflow/all/all/latest`, the list of dataflows. The five flows in
   `sdmx.SCOPE` must be in it; agency and version are taken from it.
2. `datastructure/<agency>/<id>/<version>?references=children` for each
   flow's data structure definition (DSD), with its concept schemes and
   codelists.
3. `availableconstraint/<flow>/all/all/all`: the codes in use per
   dimension, the time range, and the stated number of observations.
4. `data/<flow>/<key>` once per reference area listed in 3, the key fixing
   `REF_AREA` and leaving every other dimension open, requested as SDMX-CSV
   (`Accept: text/csv`).

| dataflow | agency, version | name |
|---|---|---|
| `DF_UNDATA_ENERGY` | UNSD 1.2 | UNSD Energy Statistics |
| `DF_UNData_EnergyBalance` | UNSD 1.0 | Energy Balance DataFlow |
| `NA_MAIN` | ESTAT 1.9 | NA Main Aggregates |
| `DF_SEEA_AEA` | ESTAT 1.3 | Air emission accounts |
| `DF_SEEA_ENERGY` | ESTAT 1.3 | Physical energy flow accounts |

The ESTAT flows use Eurostat's structure definitions, but the data is what
UNSD serves at data.un.org.

Not fetched:

- `DF_UNData_UNFCC` (GHG inventory), `DF_UNDATA_WDI`, `DF_UNData_UIS` and
  `DF_UNDATA_MDG`: they answer every query with HTTP 500 ("Error executing
  generated SQL and populating SDMX model", checked 2026-10-06), and they
  hold other agencies' data.
- `DF_SDG_GLH`: UNSD publishes the SDG global database itself, at
  <https://unstats.un.org/sdgs/dataportal>; it is better taken from there.
- `DF_UNDATA_COUNTRYDATA`: answered "NoRecordsFound".
- `NASEC_IDCFINA_A/_Q` and `NASEC_IDCNFSA_A/_Q` (sector accounts): not
  surveyed; they can be added to `sdmx.SCOPE`.
- The UNdata web pages and their download handlers (not a documented
  interface).

## Versions

The API overwrites its data in place and has no versions. A run is filed
under `data/raw/<fetch date YYYY-MM-DD>/` (UTC, the day the run started),
and the newest complete run is the current one. The Parquet and the
upload are made from that run only, and an upload replaces the previous
run on the Hub: the dataset follows upstream corrections.

## Licence

UNdata's Terms of Use (<https://data.un.org/Host.aspx?Content=UNdataUse>)
say that all data and metadata on UNdata "may be copied freely, duplicated
and further distributed provided that UNdata is cited as the reference".
They do not mention commercial use, nor the rights of the organisations that
compiled the data. `NA_MAIN` names the compiling organisation on every row,
so `compiler_status()` in `scripts/03_export_parquet.py` reads each row
under that organisation's terms and the export publishes it only when they
allow redistribution, commercial use included: the IMF's rows and
Eurostat's Kosovo rows are left out, and an organisation it does not know
is left out until its terms are checked and added there.
`data/parquet/compilers.parquet` records every decision with its reason.
See `data/README.md` for the full quotes. The code in this repository is
MIT (`LICENSE`).

## How to run

```sh
uv run python scripts/01_download.py --estimate-only   # plan and estimate, no data requests
uv run python scripts/01_download.py                   # the full collection (or resume one)
uv run python scripts/02_verify.py                     # raw files against MANIFEST.json, Parquet against the CSV
uv run python scripts/03_export_parquet.py             # newest complete run -> data/parquet/
uv run python scripts/02_verify.py                     # again, now with the Parquet
uv run python -m pytest                                # unit tests
uv run ruff check scripts tests src
uv run python src/publish.py                           # dry run: the files that would go to the Hub
uv run python src/publish.py --push                    # upload to huggingface.co/datasets/yuiseki/undata-sdmx
```

The full collection is 726 data requests (one per reference area), about
480 MB of SDMX-CSV and 5.5 million observations; on 2026-10-06 it took
about 50 minutes (the estimate printed beforehand was 481 MB and 67 minutes). The script prints the plan and its estimate
before fetching.

To fetch a slice: `--only DF_SEEA_AEA,NA_MAIN` (dataflows), `--areas JP,392`
(REF_AREA codes), `--limit N` (first N areas per dataflow). A restricted
run is never marked complete.

To resume: run `01_download.py` again. Without `--date`, the newest run is
resumed if it is not complete; files whose manifest entry still matches
their sha256 are skipped, and failed slices are retried. A complete run
makes no requests at all; the next run on another day starts a new
directory. `--date YYYY-MM-DD` picks a run directory explicitly.

`01_download.py` uses the Python standard library only, sends
`User-Agent: undata-sdmx/1 (+https://github.com/yuiseki/undata-sdmx)`,
makes one request at a time at least 1.5 seconds apart (`--delay`),
retries HTTP 500/502/503/504, time-outs and cut-off answers after 30, 60
and 120 seconds, and stops at once on 403 or 429. A slice that still fails
is recorded under `failed` in the manifest with its error, never written
as an empty file; the run is then not complete, and the script exits 1.
Each answer is checked before it is kept: the header the DSD gives, the
right `DATAFLOW` and `REF_AREA` on every row, at least one row, and a final
line break.

`03_export_parquet.py` writes one Parquet per dataflow, plus
`codelists.parquet` (every code with its label) and `components.parquet`
(each flow's dimensions and attributes with their codelists), with DuckDB,
zstd and no bloom filters. Column types come from the column names; a
value that does not fit stops the run.

`02_verify.py` re-hashes every file, re-reads every CSV with the csv module,
checks that each flow's rows add up to the observation count the API
states, and compares every cell of the Parquet with the CSV.

`publish.py` uploads the card, `data/LICENSE`, `data/provenance.yaml`, the
Parquet and the current run's raw files, except the answers of areas with
any row left out (the manifest still lists them), and deletes older runs on the Hub
in the same commit. It refuses to run if the card does not declare every
Parquet file or name the current run, or a raw file differs from its
manifest.

## Layout

```
scripts/sdmx.py                read the SDMX-ML structure messages; the scope
scripts/01_download.py         discover, plan, fetch, write MANIFEST.json
scripts/02_verify.py           check raw files against MANIFEST.json and Parquet against the CSV
scripts/03_export_parquet.py   newest complete run -> Parquet
src/publish.py                 upload to the Hugging Face Hub (dry run by default)
tests/                         unit tests; tests/fixtures/ holds trimmed real API answers
data/README.md                 the dataset card (tracked)
data/LICENSE                   terms and what the Parquet changes (tracked)
data/provenance.yaml           source, run, counts, checks, modifications (tracked)
data/raw/<YYYY-MM-DD>/         a run: structure/*.xml, data/<flow>/<area>.csv, MANIFEST.json (not in git)
data/parquet/                  the Parquet of the newest complete run (not in git)
```

`MANIFEST.json` records, for each file, its URL, bytes, sha256,
Last-Modified (the API sends none), Content-Type, fetch time (UTC), number
of attempts, its kind and format and, for a data file, its dataflow,
reference area and row count; for each dataflow, its agency, version, name,
DSD, dimensions, the stated observation count, time range and codes in use,
and the areas planned; and the failed slices with their errors.
