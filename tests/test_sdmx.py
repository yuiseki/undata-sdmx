import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import sdmx  # noqa: E402

FX = HERE / "fixtures"


def test_dataflows_come_from_the_list():
    flows = sdmx.parse_dataflows((FX / "dataflow.xml").read_bytes())
    assert len(flows) == 15
    e = flows["DF_UNDATA_ENERGY"]
    assert (e.agency, e.id, e.version) == ("UNSD", "DF_UNDATA_ENERGY", "1.2")
    assert e.name == "UNSD Energy Statistics"
    assert e.dsd == ("UNSD", "DSD_ENERGY", "1.2")
    assert e.ref == "UNSD,DF_UNDATA_ENERGY,1.2"
    assert e.csv_id == "UNSD:DF_UNDATA_ENERGY(1.2)"
    assert flows["NA_MAIN"].dsd == ("ESTAT", "NA_MAIN", "1.9")
    assert flows["DF_SEEA_AEA"].dsd == ("ESTAT", "SEEAAIR", "1.3")


def test_scope_is_taken_from_the_list_and_a_missing_flow_stops_the_run():
    flows = sdmx.parse_dataflows((FX / "dataflow.xml").read_bytes())
    chosen = sdmx.select_flows(flows, sdmx.SCOPE)
    assert [f.id for f in chosen] == [
        "DF_UNDATA_ENERGY", "DF_UNData_EnergyBalance", "NA_MAIN", "DF_SEEA_AEA", "DF_SEEA_ENERGY",
    ]  # fmt: skip
    with pytest.raises(SystemExit, match="DF_GONE"):
        sdmx.select_flows(flows, ["DF_UNDATA_ENERGY", "DF_GONE"])


def test_scope_leaves_out_other_agencies_and_the_broken_flows():
    for out in ("DF_UNData_UNFCC", "DF_UNDATA_WDI", "DF_UNData_UIS", "DF_UNDATA_MDG", "DF_SDG_GLH"):
        assert out not in sdmx.SCOPE


def test_datastructure_components_in_order():
    dsd = sdmx.parse_datastructure((FX / "datastructure_energy.xml").read_bytes())
    assert dsd.dimensions == ["FREQ", "REF_AREA", "COMMODITY", "TRANSACTION"]
    assert dsd.time == "TIME_PERIOD"
    assert dsd.measure == "OBS_VALUE"
    assert dsd.attributes == ["UNIT_MULT", "UNIT_MEASURE", "OBS_STATUS", "CONVERSION_FACTOR"]
    assert dsd.csv_header() == [
        "DATAFLOW", "FREQ", "REF_AREA", "COMMODITY", "TRANSACTION", "TIME_PERIOD", "OBS_VALUE",
        "UNIT_MULT", "UNIT_MEASURE", "OBS_STATUS", "CONVERSION_FACTOR",
    ]  # fmt: skip


def test_datastructure_components_carry_codelist_and_concept():
    dsd = sdmx.parse_datastructure((FX / "datastructure_energy.xml").read_bytes())
    c = {x.id: x for x in dsd.components}
    assert c["REF_AREA"].codelist == ("UNSD", "CL_AREA_NRG", "1.2")
    assert c["REF_AREA"].role == "dimension" and c["REF_AREA"].position == 2
    assert c["REF_AREA"].concept_name == "Reference Area"
    assert c["TIME_PERIOD"].codelist is None and c["TIME_PERIOD"].role == "time"
    assert c["CONVERSION_FACTOR"].codelist is None
    assert c["CONVERSION_FACTOR"].assignment == "Conditional"
    assert c["OBS_STATUS"].codelist == ("SDMX", "CL_OBS_STATUS", "2.1")


def test_codelists():
    cls = sdmx.parse_codelists((FX / "datastructure_energy.xml").read_bytes())
    assert ("UNSD", "CL_AREA_NRG", "1.2") in cls
    freq = cls[("SDMX", "CL_FREQ", "2.0")]
    assert freq.name == "Frequency"
    assert [c.id for c in freq.codes] == ["A", "S", "Q"]
    assert freq.codes[0].name == "Annual"


def test_available_constraint():
    av = sdmx.parse_available((FX / "availableconstraint_energy.xml").read_bytes())
    assert av.observations == 1919234
    assert av.values["REF_AREA"] == ["004", "008", "012", "016"]
    assert av.values["FREQ"] == ["A"]
    assert av.time_range == ("1990-01-01T00:00:00", "2024-12-31T23:59:59")


def test_available_constraint_without_metrics_is_refused():
    xml = (FX / "availableconstraint_energy.xml").read_text(encoding="utf-8")
    xml = xml.replace("sdmx_metrics", "something_else")
    with pytest.raises(ValueError, match="sdmx_metrics"):
        sdmx.parse_available(xml.encode())


@pytest.mark.parametrize(
    ("dims", "area", "key"),
    [
        (["FREQ", "REF_AREA", "COMMODITY", "TRANSACTION"], "392", ".392.."),
        (["REF_AREA", "COMMODITY", "TRANSACTION", "UNIT"], "392", "392..."),
        (
            ["FREQ", "ADJUSTMENT", "REF_AREA", "COUNTERPART_AREA", "REF_SECTOR",
             "COUNTERPART_SECTOR", "ACCOUNTING_ENTRY", "STO", "INSTR_ASSET", "ACTIVITY",
             "EXPENDITURE", "UNIT_MEASURE", "PRICES", "TRANSFORMATION"],
            "JP",
            "..JP...........",
        ),
    ],
)  # fmt: skip
def test_data_key_puts_the_area_in_its_position(dims, area, key):
    assert sdmx.data_key(dims, "REF_AREA", area) == key
