import importlib.util
import sys
from pathlib import Path

import duckdb
import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "export", HERE.parent / "scripts" / "03_export_parquet.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
import sdmx  # noqa: E402

FX = HERE / "fixtures"
DSD = sdmx.parse_datastructure((FX / "datastructure_energy.xml").read_bytes())


@pytest.mark.parametrize(
    ("name", "sql"),
    [
        ("OBS_VALUE", "DOUBLE"),
        ("CONVERSION_FACTOR", "DOUBLE"),
        ("PRE_BREAK_VALUE", "DOUBLE"),
        ("UNIT_MULT", "INTEGER"),
        ("DECIMALS", "INTEGER"),
        ("TIME_PERIOD", "VARCHAR"),
        ("REF_AREA", "VARCHAR"),
        ("OBS_STATUS", "VARCHAR"),
        ("COMMENT_TS", "VARCHAR"),
        ("DATAFLOW", "VARCHAR"),
    ],
)
def test_column_type(name, sql):
    assert m.column_type(name) == sql


def _rel(files, label="T"):
    con = duckdb.connect()
    return con.sql(m.select_sql(con, files, DSD.csv_header(), label))


def _csv(tmp_path, rows: list[str], name="t.csv"):
    p = tmp_path / name
    p.write_text(",".join(DSD.csv_header()) + "\r\n" + "".join(r + "\r\n" for r in rows))
    return p


ROW = "UNSD:DF_UNDATA_ENERGY(1.2),A,392,0100,01,1990,{v},3,TN,A,{cf}"


def test_values_are_typed_and_codes_stay_text(tmp_path):
    rel = _rel([_csv(tmp_path, [ROW.format(v="7985", cf="23.4310278")])])
    row = dict(zip(rel.columns, rel.fetchone(), strict=True))
    assert row["REF_AREA"] == "392" and row["COMMODITY"] == "0100" and row["TIME_PERIOD"] == "1990"
    assert row["OBS_VALUE"] == 7985.0 and row["UNIT_MULT"] == 3
    assert row["CONVERSION_FACTOR"] == 23.4310278
    types = dict(zip(rel.columns, (str(t) for t in rel.types), strict=True))
    assert types["OBS_VALUE"] == "DOUBLE" and types["UNIT_MULT"] == "INTEGER"
    assert types["TRANSACTION"] == "VARCHAR"


@pytest.mark.parametrize(
    ("raw", "want"),
    [("1.5E-05", 1.5e-05), ("-2.25", -2.25), ("185815.39899999998", 185815.39899999998),
     ("NA", None), (":", None), ("", None)],
)  # fmt: skip
def test_obs_value_forms(tmp_path, raw, want):
    rel = _rel([_csv(tmp_path, [ROW.format(v=raw, cf="")])])
    assert rel.select("OBS_VALUE, CONVERSION_FACTOR").fetchone() == (want, None)


@pytest.mark.parametrize(
    ("v", "cf", "message"),
    [
        ("1,5", "", None),  # a comma makes a ragged row: the CSV reader stops
        ("..", "", r"T: OBS_VALUE is not a number: \.\."),
        (".5", "", r"T: OBS_VALUE is not a number: \.5"),
        ("n/a", "", "T: OBS_VALUE is not a number: n/a"),
        ("1", "x", "T: CONVERSION_FACTOR is not a number: x"),
    ],
)
def test_a_value_that_does_not_fit_stops_the_run(tmp_path, v, cf, message):
    with pytest.raises(duckdb.Error, match=message):
        _rel([_csv(tmp_path, [ROW.format(v=v, cf=cf)])]).fetchall()


def test_unit_mult_must_be_an_integer(tmp_path):
    row = ROW.format(v="1", cf="").replace(",3,TN,", ",3.5,TN,")
    with pytest.raises(duckdb.Error, match="T: UNIT_MULT is not an integer: 3.5"):
        _rel([_csv(tmp_path, [row])]).fetchall()


def test_files_are_read_in_the_order_given(tmp_path):
    a = _csv(tmp_path, [ROW.format(v="2", cf="")], "a.csv")
    b = _csv(tmp_path, [ROW.format(v="1", cf="")], "b.csv")
    assert [r[0] for r in _rel([b, a]).select("OBS_VALUE").fetchall()] == [1.0, 2.0]


def test_a_header_unlike_the_dsd_stops_the_run(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("DATAFLOW,FREQ\r\nX,A\r\n")
    with pytest.raises(SystemExit, match="header"):
        _rel([p])


def test_real_rows_of_every_flow_fit_their_types():
    con = duckdb.connect()
    for name in ("DF_UNDATA_ENERGY", "DF_UNData_EnergyBalance", "NA_MAIN", "DF_SEEA_AEA"):
        p = FX / f"data_{name}.csv"
        header = p.read_bytes().decode().split("\r\n")[0].split(",")
        rows = con.sql(m.select_sql(con, [p], header, name)).fetchall()
        assert rows


def test_codelist_rows():
    rows = m.codelist_rows([FX / "datastructure_energy.xml"])
    freq = [r for r in rows if r[1] == "CL_FREQ"]
    assert freq[0][:6] == ("SDMX", "CL_FREQ", "2.0", "Frequency", "A", "Annual")
    assert freq[0][6].startswith("To be used for data collected") and freq[0][7] is None
    assert len({r[:3] for r in rows}) == 7


def test_the_same_codelist_twice_with_different_codes_stops_the_run(tmp_path):
    a = FX / "datastructure_energy.xml"
    b = tmp_path / "b.xml"
    b.write_text(a.read_text(encoding="utf-8").replace(">Annual<", ">Yearly<"), encoding="utf-8")
    with pytest.raises(SystemExit, match="CL_FREQ"):
        m.codelist_rows([a, b])
    assert len(m.codelist_rows([a, a])) == len(m.codelist_rows([a]))


def test_component_rows():
    flow = sdmx.parse_dataflows((FX / "dataflow.xml").read_bytes())["DF_UNDATA_ENERGY"]
    rows = m.component_rows(flow, DSD)
    by = {r[2]: r for r in rows}
    assert by["REF_AREA"] == (
        "DF_UNDATA_ENERGY", "1.2", "REF_AREA", "dimension", 2, None, None, "Reference Area",
        "UNSD", "CL_AREA_NRG", "1.2",
    )  # fmt: skip
    assert by["OBS_STATUS"][3:7] == ("attribute", None, "Mandatory", "observation")
    assert [r[2] for r in rows][:5] == ["FREQ", "REF_AREA", "COMMODITY", "TRANSACTION", "TIME_PERIOD"]
