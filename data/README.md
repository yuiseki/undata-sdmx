---
license: other
license_name: undata-terms-of-use
license_link: https://data.un.org/Host.aspx?Content=UNdataUse
language:
- en
task_categories:
- tabular-regression
- time-series-forecasting
tags:
- united-nations
- unsd
- undata
- sdmx
- energy
- energy-balance
- national-accounts
- seea
- air-emissions
size_categories:
- 1M<n<10M
configs:
- config_name: 'DF_UNDATA_ENERGY'
  data_files: parquet/DF_UNDATA_ENERGY.parquet
  default: true
- config_name: 'DF_UNData_EnergyBalance'
  data_files: parquet/DF_UNData_EnergyBalance.parquet
- config_name: 'NA_MAIN'
  data_files: parquet/NA_MAIN.parquet
- config_name: 'DF_SEEA_AEA'
  data_files: parquet/DF_SEEA_AEA.parquet
- config_name: 'DF_SEEA_ENERGY'
  data_files: parquet/DF_SEEA_ENERGY.parquet
- config_name: 'codelists'
  data_files: parquet/codelists.parquet
- config_name: 'components'
  data_files: parquet/components.parquet
---

# undata-sdmx

Five dataflows of the UNdata SDMX API of the United Nations Statistics
Division (UNSD), complete as the API served them on 2026-10-06: the UNSD
Energy Statistics, the UNSD energy balances, the national accounts main
aggregates, and the SEEA air emission and physical energy flow accounts.
5,457,708 observations in all, with the data structure definitions and
codelists that describe them.

Source: UNdata, United Nations Statistics Division, http://data.un.org
(SDMX API, https://data.un.org/Host.aspx?Content=API). Reuse is allowed
under UNdata's Terms of Use provided UNdata is cited; the terms do not
mention commercial use (see Licence). Part of `NA_MAIN` is marked in the
data itself as compiled by Eurostat, the OECD or the IMF (see Things to
know).

## The data follows upstream corrections

The API overwrites its data in place and keeps no versions; UNSD's terms
say it "periodically incorporates, without notice, revisions, updates and
improvements". This dataset therefore holds one run, the newest, and is
replaced as a whole when it is fetched again; older runs are not kept here.
The current run is `raw/2026-10-06/` (fetched 2026-10-06, 04:00 to 04:50
UTC). The date of the run is the date the values were read from the API,
not a release date: the API states none.

## Subsets

| subset | rows | values | reference areas | time | what it is |
|---|---:|---:|---:|---|---|
| `DF_UNDATA_ENERGY` | 1,919,234 | 1,919,234 | 245 | 1990-2024 | UNSD Energy Statistics (UNSD 1.2): production, trade and use of 75 energy commodities, 216 transactions, in physical units |
| `DF_UNData_EnergyBalance` | 1,045,891 | 1,045,891 | 218 | 1990-2023 | Energy balances (UNSD 1.0): 11 commodity groups by 53 balance items, in terajoules |
| `NA_MAIN` | 1,223,766 | 1,198,925 | 218 | 1947-Q1 to 2025-Q3 | National accounts main aggregates (Eurostat structure NA_MAIN 1.9), annual and quarterly |
| `DF_SEEA_AEA` | 134,248 | 17,148 | 7 | 1990-2022 | SEEA air emission accounts (Eurostat structure SEEAAIR 1.3) |
| `DF_SEEA_ENERGY` | 1,134,569 | 1,128,764 | 38 | 2000-2022 | SEEA physical energy flow accounts (Eurostat structure SEEANRG 1.3) |
| `codelists` | 9,907 | | | | every code of every codelist the five structures use, with its English label |
| `components` | 120 | | | | each flow's dimensions, measure and attributes, with concept name and codelist |

"rows" is the number of observations; for each flow it equals the count the
API states in its available constraint (annotation `sdmx_metrics`). "values"
is the rows whose `OBS_VALUE` is a number; the others are NULL (see below).

A reference area is a country, territory or aggregate. In the two energy
flows `REF_AREA` is a numeric M49 code (`392` is Japan); in the other three
it is a two-letter code from the IMF/SDMX area list (`JP`), which also has
aggregates such as `B6` (EU27), `I8` (euro area 19), `6O` (OECD) and `R22`
(G20).

## Columns

Each dataflow subset has the columns of its SDMX-CSV answers, in their
order: `DATAFLOW` (for example `UNSD:DF_UNDATA_ENERGY(1.2)`), the
dimensions, `TIME_PERIOD`, `OBS_VALUE`, then the attributes. Codes are
kept as codes; labels are in `codelists`.

| column | type |
|---|---|
| `OBS_VALUE`, `CONVERSION_FACTOR`, `PRE_BREAK_VALUE` | DOUBLE |
| `UNIT_MULT`, `DECIMALS` | INTEGER |
| every other column, including `TIME_PERIOD` (`1990`, `2020-Q1`) | VARCHAR |

Types come from the column names, not from inference; each value was
checked against a regular expression (`-?[0-9]+` for INTEGER,
`-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?` for DOUBLE) before the cast, and
one that did not fit would have stopped the build. An empty cell is NULL.
In `OBS_VALUE`, "NA" and ":" are NULL too: the answers write "NA" (111,167
rows of `DF_SEEA_AEA`) and ":" (573 rows of `DF_SEEA_AEA`, 40 of
`DF_SEEA_ENERGY`) for an observation without a value, and leave it empty in
5,360, 5,765 and 24,841 rows of `DF_SEEA_AEA`, `DF_SEEA_ENERGY` and
`NA_MAIN`. Those rows are kept because their attributes may say why the
value is missing (`OBS_STATUS`, for example `L`, `M` or `Q` in `NA_MAIN`).
The raw files keep the tokens as sent.

Values are in the unit of `UNIT_MEASURE` (`UNIT` in the balances) times
10 to the power `UNIT_MULT`: Japan's GDP at current prices for 2024 is
`OBS_VALUE` 609642500 with `UNIT_MEASURE` XDC (national currency) and
`UNIT_MULT` 6, that is 609,642,500 million yen. `UNIT_MULT` differs between
flows and series (0, 3 and 6 occur), so read it on the row.

`components` has (`dataflow`, `dataflow_version`, `component`, `role`
(dimension, time, measure, attribute), `position`, `assignment` (Mandatory
or Conditional), `attached_to` (observation, series or dataset),
`concept_name`, `codelist_agency`, `codelist_id`, `codelist_version`).
`codelists` has (`codelist_agency`, `codelist_id`, `codelist_version`,
`codelist_name`, `code`, `name`, `description`, `parent`). The flows use
different versions of lists with the same id (`CL_AREA` 1.8 and 1.15, for
example), so join on all three of agency, id and version, through
`components`:

```sql
-- DuckDB, straight off the Hub: Japan's 2023 energy statistics with labels
WITH lab AS (
  SELECT k.component, l.code, l.name
  FROM 'hf://datasets/yuiseki/undata-sdmx/parquet/components.parquet' k
  JOIN 'hf://datasets/yuiseki/undata-sdmx/parquet/codelists.parquet' l
    USING (codelist_agency, codelist_id, codelist_version)
  WHERE k.dataflow = 'DF_UNDATA_ENERGY'
)
SELECT d.TIME_PERIOD, c.name AS commodity, t.name AS transaction,
       d.OBS_VALUE, d.UNIT_MEASURE, d.UNIT_MULT
FROM 'hf://datasets/yuiseki/undata-sdmx/parquet/DF_UNDATA_ENERGY.parquet' d
JOIN lab c ON c.component = 'COMMODITY'   AND c.code = d.COMMODITY
JOIN lab t ON t.component = 'TRANSACTION' AND t.code = d.TRANSACTION
WHERE d.REF_AREA = '392' AND d.TIME_PERIOD = '2023'
ORDER BY commodity, transaction;

-- Japan's annual GDP at current prices, national currency
SELECT TIME_PERIOD, OBS_VALUE, UNIT_MULT
FROM 'hf://datasets/yuiseki/undata-sdmx/parquet/NA_MAIN.parquet'
WHERE REF_AREA = 'JP' AND FREQ = 'A' AND STO = 'B1GQ' AND PRICES = 'V'
  AND UNIT_MEASURE = 'XDC' AND TRANSFORMATION = 'N'
ORDER BY TIME_PERIOD;
```

## Raw files

`raw/2026-10-06/` holds the API's answers byte for byte (485 MB):

- `structure/dataflow.xml`: the API's list of 15 dataflows
- `structure/datastructure_<agency>_<id>_<version>.xml`: each DSD with its
  concept schemes and codelists (`?references=children`)
- `structure/availableconstraint_<flow>.xml`: the codes in use, the time
  range and the observation count of each flow
- `data/<flow>/<area>.csv`: one SDMX-CSV answer per reference area (726
  files, 502,911,717 bytes)
- `MANIFEST.json`: for every file its URL, bytes, sha256, Content-Type and
  fetch time (the API sends no Last-Modified), and for every flow what the
  API stated about it

The data was requested as SDMX-CSV (`Accept: text/csv`), one request per
reference area listed in the flow's available constraint, with the key
fixing `REF_AREA` and leaving every other dimension open.

## Things to know

- Who compiled the values. The data carries attributes for this in the
  three Eurostat-structured flows. In `NA_MAIN`, `COMPILING_ORG` is filled
  on every row: European Commission (including Eurostat) for 799,531 rows
  and 41 areas, OECD for 277,347 rows and 22 areas (among them the United
  States, Japan, China, India and the OECD and G20 aggregates), UN
  organisations for 88,848 rows and 97 areas, and the IMF for 58,040 rows
  and 58 areas. `DISS_ORG` (disseminating organisation) is the same except
  that it is empty for the OECD rows. `DATA_COMP` is empty. In the two SEEA
  flows `COMPILING_ORG` and `DISS_ORG` are empty; `DF_SEEA_ENERGY` covers
  38 areas: 34 European countries, the EU27 aggregate `B6`, Indonesia,
  Peru and Samoa. The two UNSD
  energy flows carry no such attribute; `FOOTNOTES` in the balances is
  empty. No structure or answer carries licence text.
- `NA_MAIN` mixes frequencies (`FREQ` A and Q), adjustments (`ADJUSTMENT`
  N and Y), price bases (`PRICES`) and units; filter on them before adding
  anything up. `COMMENT_DSET` is "Validated at 20220401_070001" on the
  17,898 rows of the aggregate `B5`.
- `DF_SEEA_AEA` has 7 areas (Australia, Canada, Colombia, Indonesia, South
  Korea, New Zealand, Ukraine), and most of its rows have no value: the
  Indonesian and Ukrainian answers list every series and fill few.
  `COMMENT_TS` notes that Indonesia's accounts "include combustion related
  emissions only and do not include fugitive emissions from fuels" (1,221
  rows) and, for other rows, "only cover energy-related emissions" (2,937).
- `CONVERSION_FACTOR` in `DF_UNDATA_ENERGY` is the factor UNSD gives with
  the value (for example the calorific value of a fuel), empty on 82,847
  rows.
- Some balance values are floating-point residues such as
  -2.7284841053187847E-12, and some are "-0"; they are kept as sent.
- Every dimension code in the five subsets is in its codelist.

## Not included

- The other dataflows of the API: `DF_UNData_UNFCC` (GHG inventory),
  `DF_UNDATA_WDI`, `DF_UNData_UIS` and `DF_UNDATA_MDG` answered every query
  with HTTP 500 and hold other agencies' data; `DF_UNDATA_COUNTRYDATA`
  returned no records; the sector accounts `NASEC_*` were not taken.
- The SDG global database (`DF_SDG_GLH`): UNSD publishes it itself at
  https://unstats.un.org/sdgs/dataportal, which is the place to take it from.
- The UNdata databases that the API does not serve (Demographic
  Statistics, National Accounts Official Country Data, Environment
  Statistics and others).

## Checked before upload

`scripts/02_verify.py` in the source repository checks:

- all 737 raw files have the size and sha256 in `MANIFEST.json`; every
  structure file is well-formed XML
- every SDMX-CSV answer, read with Python's csv module, has the header its
  DSD gives, the right `DATAFLOW` and `REF_AREA` on every row, and the row
  count in the manifest; every planned area has its file; no slice failed
- the rows of each flow add up to the observation count the API states
  (all five equal)
- every cell of each dataflow subset equals the CSV's, compared one by one
  in file and row order (text as text, numbers as Python reads them, empty,
  "NA" and ":" as NULL)
- every dimension code is in its codelist
- a DuckDB with extension autoloading turned off opens every file, and no
  row group has a bloom filter

## Licence

UNdata, Terms and Conditions of use
(https://data.un.org/Host.aspx?Content=UNdataUse, read on 2026-10-06):

> Terms of Use
>
> All data and metadata provided on UNdata’s website are available free of
> charge and may be copied freely, duplicated and further distributed
> provided that UNdata is cited as the reference.

> Disclaimers
>
> The data and metadata presented on UNdata are supplied as contained in
> the source database without any addition, subtraction, amendment, or
> modification by the United Nations Statistics Division.

> Information about the quality or limitations of the data and metadata
> should be obtained from the organization responsible for the source
> database.

The API page says "The API is governed by UNdata Terms of Use". The terms
name no standard licence and do not mention commercial use: they neither
permit nor forbid it. They also say nothing about modified or derived
works. The card declares `license: other` for this reason.

The terms speak for all data on UNdata. They do not mention the rights of
the organisations that compiled it; the `COMPILING_ORG` values above
(Eurostat, the OECD, the IMF) were not checked against those
organisations' own terms.

This dataset is not made or endorsed by the United Nations or UNSD. What
was changed: the SDMX-CSV answers were concatenated per dataflow, typed and
written as Parquet as described above, and the codelists and components
were read from the structure messages; no value was changed. The raw files
are unchanged. `LICENSE` beside this file says the same.

## Citation

As the terms ask, cite UNdata:

> UNdata, United Nations Statistics Division. SDMX API, dataflows
> DF_UNDATA_ENERGY, DF_UNData_EnergyBalance, NA_MAIN, DF_SEEA_AEA and
> DF_SEEA_ENERGY, http://data.un.org, accessed 2026-10-06.
