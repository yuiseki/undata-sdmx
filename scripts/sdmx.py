"""Read the SDMX-ML 2.1 structure messages of the UNdata SDMX API.

Shared by 01_download.py, 02_verify.py and 03_export_parquet.py. Standard
library only.

The API (https://data.un.org/Host.aspx?Content=API) is a Eurostat SDMX-RI
("NSI Web Service v8.15.1.0"). The documented base
http://data.un.org/ws/rest/ is redirected to
https://data.un.org/legacy/ws/rest/, which is used directly.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

BASE = "https://data.un.org/legacy/ws/rest"

# The dataflows of UNSD's own data that the API serves (checked 2026-10-06).
# Left out: DF_UNData_UNFCC (GHG), DF_UNDATA_WDI, DF_UNData_UIS and
# DF_UNDATA_MDG answer every query with 500 and hold other agencies' data;
# DF_SDG_GLH, because UNSD publishes the SDG global database itself
# (https://unstats.un.org/sdgs/dataportal); DF_UNDATA_COUNTRYDATA returned no
# records; NASEC_* (sector accounts) were not part of the survey.
SCOPE = ["DF_UNDATA_ENERGY", "DF_UNData_EnergyBalance", "NA_MAIN", "DF_SEEA_AEA", "DF_SEEA_ENERGY"]

M = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message}"
S = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"
C = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common}"
LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _name(el, tag: str = "Name") -> str | None:
    """The English Name (or Description) of an element, else the first one."""
    names = el.findall(C + tag)
    for n in names:
        if n.get(LANG) == "en":
            return n.text
    return names[0].text if names else None


def _ref(el) -> tuple[str, str, str] | None:
    return None if el is None else (el.get("agencyID"), el.get("id"), el.get("version"))


@dataclass
class Dataflow:
    agency: str
    id: str
    version: str
    name: str | None
    dsd: tuple[str, str, str]

    @property
    def ref(self) -> str:
        """The flow reference of a REST data query."""
        return f"{self.agency},{self.id},{self.version}"

    @property
    def csv_id(self) -> str:
        """The value of the DATAFLOW column of SDMX-CSV."""
        return f"{self.agency}:{self.id}({self.version})"


def parse_dataflows(xml: bytes) -> dict[str, Dataflow]:
    root = ET.fromstring(xml)
    out = {}
    for df in root.iter(S + "Dataflow"):
        dsd = _ref(df.find(f"{S}Structure/Ref"))
        out[df.get("id")] = Dataflow(df.get("agencyID"), df.get("id"), df.get("version"),
                                     _name(df), dsd)  # fmt: skip
    return out


def select_flows(flows: dict[str, Dataflow], ids: list[str]) -> list[Dataflow]:
    missing = [i for i in ids if i not in flows]
    if missing:
        raise SystemExit(f"not in the API's dataflow list: {', '.join(missing)}")
    return [flows[i] for i in ids]


@dataclass
class Component:
    id: str
    role: str  # dimension, time, measure, attribute
    position: int | None
    concept: tuple[str, str, str] | None  # agency, scheme id, scheme version
    concept_name: str | None
    codelist: tuple[str, str, str] | None
    assignment: str | None  # attributes only: Mandatory or Conditional
    attached_to: str | None  # attributes only: observation, series, dataset


@dataclass
class DataStructure:
    agency: str
    id: str
    version: str
    name: str | None
    components: list[Component] = field(default_factory=list)

    def _ids(self, role: str) -> list[str]:
        return [c.id for c in self.components if c.role == role]

    @property
    def dimensions(self) -> list[str]:
        return self._ids("dimension")

    @property
    def attributes(self) -> list[str]:
        return self._ids("attribute")

    @property
    def time(self) -> str:
        return self._ids("time")[0]

    @property
    def measure(self) -> str:
        return self._ids("measure")[0]

    def csv_header(self) -> list[str]:
        """The columns SDMX-CSV gives: DATAFLOW, dimensions, time, measure, attributes."""
        return ["DATAFLOW", *self.dimensions, self.time, self.measure, *self.attributes]


def _concepts(root) -> dict[tuple[str, str], str | None]:
    out = {}
    for cs in root.iter(S + "ConceptScheme"):
        for c in cs.findall(S + "Concept"):
            out[(cs.get("id"), c.get("id"))] = _name(c)
    return out


def parse_datastructure(xml: bytes) -> DataStructure:
    """The one DataStructure of a datastructure message (with its concepts)."""
    root = ET.fromstring(xml)
    concepts = _concepts(root)
    dsds = list(root.iter(S + "DataStructure"))
    if len(dsds) != 1:
        raise ValueError(f"expected one DataStructure, found {len(dsds)}")
    d = dsds[0]
    out = DataStructure(d.get("agencyID"), d.get("id"), d.get("version"), _name(d))
    roles = {"Dimension": "dimension", "TimeDimension": "time",
             "PrimaryMeasure": "measure", "Attribute": "attribute"}  # fmt: skip
    for lst in ("DimensionList", "MeasureList", "AttributeList"):
        el = d.find(f"{S}DataStructureComponents/{S}{lst}")
        if el is None:
            continue
        for comp in el:
            role = roles.get(comp.tag.removeprefix(S))
            if role is None:
                continue
            cref = comp.find(f"{S}ConceptIdentity/Ref")
            concept = None
            if cref is not None:
                concept = (cref.get("agencyID"), cref.get("maintainableParentID"),
                           cref.get("maintainableParentVersion"))  # fmt: skip
            attached = None
            if role == "attribute":
                rel = comp.find(S + "AttributeRelationship")
                kinds = {x.tag.removeprefix(S) for x in rel} if rel is not None else set()
                if "PrimaryMeasure" in kinds:
                    attached = "observation"
                elif kinds & {"Dimension", "Group"}:
                    attached = "series"
                else:
                    attached = "dataset"
            pos = comp.get("position")
            out.components.append(
                Component(
                    id=comp.get("id"),
                    role=role,
                    position=int(pos) if pos else None,
                    concept=concept,
                    concept_name=concepts.get((concept[1], cref.get("id"))) if concept else None,
                    codelist=_ref(comp.find(f"{S}LocalRepresentation/{S}Enumeration/Ref")),
                    assignment=comp.get("assignmentStatus"),
                    attached_to=attached,
                )
            )
    # SDMX-CSV orders the attributes as the DSD lists them; keep that order
    return out


@dataclass
class Code:
    id: str
    name: str | None
    description: str | None
    parent: str | None


@dataclass
class Codelist:
    agency: str
    id: str
    version: str
    name: str | None
    codes: list[Code]


def parse_codelists(xml: bytes) -> dict[tuple[str, str, str], Codelist]:
    root = ET.fromstring(xml)
    out = {}
    for cl in root.iter(S + "Codelist"):
        codes = []
        for c in cl.findall(S + "Code"):
            parent = c.find(f"{S}Parent/Ref")
            codes.append(Code(c.get("id"), _name(c), _name(c, "Description"),
                              parent.get("id") if parent is not None else None))  # fmt: skip
        key = (cl.get("agencyID"), cl.get("id"), cl.get("version"))
        out[key] = Codelist(*key, _name(cl), codes)
    return out


@dataclass
class Available:
    observations: int
    values: dict[str, list[str]]
    time_range: tuple[str, str] | None


def parse_available(xml: bytes) -> Available:
    """An availableconstraint answer: the codes in use and the observation count."""
    root = ET.fromstring(xml)
    obs = None
    for a in root.iter(C + "Annotation"):
        if a.findtext(C + "AnnotationType") == "sdmx_metrics":
            obs = int(a.findtext(C + "AnnotationTitle"))
    if obs is None:
        raise ValueError("no sdmx_metrics annotation (observation count) in the constraint")
    values, time_range = {}, None
    for kv in root.iter(C + "KeyValue"):
        tr = kv.find(C + "TimeRange")
        if tr is not None:
            time_range = (tr.findtext(C + "StartPeriod"), tr.findtext(C + "EndPeriod"))
            continue
        values[kv.get("id")] = [v.text for v in kv.findall(C + "Value")]
    return Available(obs, values, time_range)


def data_key(dimensions: list[str], dim: str, code: str) -> str:
    """A series key with one dimension fixed and every other one left open."""
    return ".".join(code if d == dim else "" for d in dimensions)
