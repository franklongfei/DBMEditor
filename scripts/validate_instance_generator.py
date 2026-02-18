"""Validation helper for LN instance generation rules.

Checks:
- DA/BDAs with fc in {ST, MX} are not emitted as instance DAI.
- LangRef IDs defined in type definitions are not re-defined in instances.

Run from the DBMEditor folder:
  python scripts/validate_instance_generator.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable


# When running as `python scripts/validate_instance_generator.py`, sys.path[0]
# points at `DBMEditor/scripts`, so add `DBMEditor` for sibling imports.
here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))


from iec61850_scanner import load_lnode_type, scan_type_catalog
from ln_instance_scanner import create_ln_instance_from_template


LANGREF_TYPE = "SchneiderElectric-PowerLogic-LangRef"


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _iter_desc(e) -> Iterable:
    yield e
    for c in list(e):
        yield from _iter_desc(c)


def _find_first_dai_by_path(ln, doi_name: str, dai_name: str):
    for doi in list(ln):
        if not isinstance(doi.tag, str) or _local(doi.tag) != "DOI":
            continue
        if (doi.attrib.get("name") or "") != doi_name:
            continue
        for dai in list(doi):
            if not isinstance(dai.tag, str) or _local(dai.tag) != "DAI":
                continue
            if (dai.attrib.get("name") or "") == dai_name:
                return dai
    return None


def _has_langref_id(dai) -> bool:
    for p in list(dai):
        if not isinstance(p.tag, str) or _local(p.tag) != "Private":
            continue
        if p.attrib.get("type") != LANGREF_TYPE:
            continue
        if (p.text or "").strip():
            return True
    return False


def main() -> int:
    root = dbmeditor_dir.parent

    iec61850_dir = root / "ep7_datamodel" / "datamodel" / "iec61850"
    if not iec61850_dir.exists():
        print(f"ERROR: Missing iec61850 dir: {iec61850_dir}")
        print("Adjust script root detection or open the workspace that contains ep7_datamodel.")
        return 2

    catalog = scan_type_catalog(iec61850_dir)

    # Prefer a known template if present; otherwise pick any LNodeType.
    preferred_id = "SE_PSCH_Teleprotection_PowerLogic_V001"
    info = next((x for x in catalog.lnode_types if x.id == preferred_id), None)
    if info is None:
        info = catalog.lnode_types[0]
        print(f"NOTE: Preferred template not found; using {info.id}")

    template = load_lnode_type(info)

    doc = create_ln_instance_from_template(
        iec61850_dir=iec61850_dir,
        template=template,
        target_path=root / "__tmp_validate.xml",
        prefix="AID",
        inst="0",
        ln_desc="validate",
    )

    ln = doc.ln_elements[0]

    # Check 1: For a DO/DA combo known to have LangRef in types (NamPlt/vendor in LPL),
    # confirm the instance does NOT contain a LangRef ID.
    vendor_dai = _find_first_dai_by_path(ln, "NamPlt", "vendor")
    if vendor_dai is None:
        print("WARN: Did not find DAI NamPlt/vendor in generated instance (template may not include LPL).")
    else:
        if _has_langref_id(vendor_dai):
            print("FAIL: Generated instance re-defined LangRef ID on NamPlt/vendor")
            return 1
        print("OK: No LangRef ID emitted on NamPlt/vendor")

    # Check 2: Ensure no DAI has an fc attribute of ST or MX (and optionally warn if found).
    # (Our generator skips creation, so this should always be 0.)
    bad = []
    for e in _iter_desc(ln):
        if not isinstance(getattr(e, "tag", None), str) or _local(e.tag) != "DAI":
            continue
        fc = (e.attrib.get("fc") or "").strip().upper()
        if fc in {"ST", "MX"}:
            bad.append((e.attrib.get("name"), fc))

    if bad:
        print(f"FAIL: Found {len(bad)} DAI elements with fc in ST/MX: {bad[:10]}")
        return 1

    print("OK: No DAI with fc=ST/MX present")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
