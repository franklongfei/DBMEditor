"""Validate LN attribute ordering and LangRef placeholder behavior.

Checks:
1) Saved LN elements serialize with prefix before lnClass.
2) When LangRef is empty for DAI name="d", no empty <Private type="...LangRef"/> is written.

Run:
  python scripts/validate_ln_prefix_attr_order.py
"""

from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parents[0]
sys.path.insert(0, str(PROJECT_ROOT))

import xml.etree.ElementTree as ET

from ln_instance_scanner import LNInstanceDocument, load_ln_instance_document, save_ln_instance_document


def _assert_prefix_before_lnclass(xml_text: str) -> None:
    # Find the first LN start tag that contains both attributes.
    # We only care about textual order inside the tag.
    for line in xml_text.splitlines():
        if "<LN" not in line:
            continue
        if "prefix=\"" in line and "lnClass=\"" in line:
            assert line.index('prefix="') < line.index('lnClass="'), line
            return
    raise AssertionError("No <LN ... prefix=... lnClass=...> line found")


def _assert_no_empty_langref_private(xml_text: str) -> None:
    bad = '<Private type="SchneiderElectric-PowerLogic-LangRef" />'
    assert bad not in xml_text, "Found empty LangRef <Private .../> placeholder"


def main() -> None:
    out_dir = PROJECT_ROOT / "testcase" / "__tmp_generated"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "ZNPDIS.xml"

    ns = "http://www.iec.ch/61850/2003/SCL"
    q = lambda name: f"{{{ns}}}{name}"

    # Create a minimal LN document with intentionally "wrong" attribute insertion order.
    ln = ET.Element(q("LN"))
    ln.attrib["lnClass"] = "PDIS"
    ln.attrib["prefix"] = "ZN"
    ln.attrib["inst"] = "0"
    ln.attrib["lnType"] = "SE_PDIS_ZN_PowerLogic_V001"

    doi = ET.SubElement(ln, q("DOI"))
    doi.attrib["name"] = "Beh"

    dai = ET.SubElement(doi, q("DAI"))
    dai.attrib["name"] = "d"

    # Has value (so DAI must remain), but empty LangRef Private placeholder should be removed on save.
    val = ET.SubElement(dai, q("Val"))
    val.text = "Some label"

    pe = ET.SubElement(dai, q("Private"))
    pe.attrib["type"] = "SchneiderElectric-PowerLogic-LangRef"
    pe.text = ""  # intentionally empty

    tree = ET.ElementTree(ln)
    doc = LNInstanceDocument(file_path=out_path, tree=tree, ns=ns, ln_elements=[ln])

    saved_path = save_ln_instance_document(doc, target_path=out_path, make_backup=False)
    _ = load_ln_instance_document(saved_path)

    xml_text = saved_path.read_text(encoding="utf-8")
    _assert_prefix_before_lnclass(xml_text)
    _assert_no_empty_langref_private(xml_text)

    print("OK: prefix before lnClass, and no empty LangRef Private placeholder")


if __name__ == "__main__":
    main()
