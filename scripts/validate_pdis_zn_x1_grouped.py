"""Validate grouped setting for SE_PDIS_ZN_PowerLogic_V001 / X1.

Expectation:
- X1 DO is SE_ASG_SGFLOAT (ASG) with DA setMag fc="SE" bType="Struct" -> DAType has BDA f.
- Therefore the generated instance should contain:
    DOI:X1/SDI:setMag/DAI:f with 8 <Val sGroup="1".."8"> children.

Run:
  python scripts/validate_pdis_zn_x1_grouped.py
"""

from __future__ import annotations

import sys
from pathlib import Path

here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))

from ln_instance_scanner import create_ln_instance_from_template, extract_value_refs


def main() -> int:
    root = dbmeditor_dir.parent
    iec = root / "ep7_datamodel" / "datamodel" / "iec61850"
    tpl = iec / "LNodeType" / "SE_PDIS_ZN_PowerLogic_V001.xml"

    doc = create_ln_instance_from_template(
        iec61850_dir=iec,
        template=tpl,
        target_path=dbmeditor_dir / "__tmp_pdis_zn_x1.xml",
        prefix="",
        inst="0",
        ln_desc="",
    )

    refs = extract_value_refs(doc, 0, sort=False)
    x1_refs = [r for r in refs if r.path.startswith("DOI:X1/SDI:setMag/DAI:f/")]
    paths = [r.path for r in x1_refs]
    exp = [f"DOI:X1/SDI:setMag/DAI:f/Val:sGroup={i}" for i in range(1, 9)]

    if paths != exp:
        print("FAIL: unexpected X1 setMag/f paths")
        print("got:")
        for p in paths:
            print(" ", p)
        print("expected:")
        for p in exp:
            print(" ", p)
        return 2

    # Also ensure X1 exposes the ASG editables.
    all_paths = [r.path for r in refs if r.path.startswith("DOI:X1/")]
    needed = {
        "minVal": any("/SDI:minVal/DAI:f" in p for p in all_paths),
        "maxVal": any("/SDI:maxVal/DAI:f" in p for p in all_paths),
        "stepSize": any("/SDI:stepSize/DAI:f" in p for p in all_paths),
    }
    missing = [k for k, ok in needed.items() if not ok]
    if missing:
        print("FAIL: missing X1 editable DAI in value refs:", missing)
        for p in sorted(all_paths):
            if any(x in p for x in ("minVal", "maxVal", "stepSize")):
                print(" ", p)
        return 4

    # If group 1 has a default value, expect all groups to be initialized to the same default.
    texts: dict[str, str] = {}
    for r in x1_refs:
        ve = r.val_elements[0]
        sg = (ve.attrib.get("sGroup") or "").strip()
        texts[sg] = (ve.text or "")

    t1 = (texts.get("1") or "").strip()
    if t1:
        for i in range(2, 9):
            ti = (texts.get(str(i)) or "").strip()
            if ti != t1:
                print("FAIL: expected X1 setMag/f default replicated to all sGroups")
                for j in range(1, 9):
                    print(f"  sGroup={j}: {repr((texts.get(str(j)) or '').strip())}")
                return 3

    print("OK: SE_PDIS_ZN X1 setMag/f is grouped (sGroup=1..8)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
