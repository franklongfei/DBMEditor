"""Validate FltA SDO expansion: phsA/phsB/phsC should all be generated.

This is a structural check to prevent regressions where visited_do caching
skips expanding sibling SDOs that share the same DOType.

Run:
  python scripts/validate_fltA_phs_generation.py

Note:
- Requires access to the IEC61850 type directories and the LNodeType template
  in your repo layout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parents[0]
sys.path.insert(0, str(PROJECT_ROOT))

from iec61850_scanner import scan_type_catalog, load_lnode_type  # noqa: E402
from ln_instance_scanner import create_ln_instance_from_template  # noqa: E402


def _find_repo_iec61850_dir() -> Path:
    # Try common layouts under the repo root.
    candidates = [
        PROJECT_ROOT / "ep7_datamodel" / "datamodel" / "iec61850",
        PROJECT_ROOT.parent / "ep7_datamodel" / "datamodel" / "iec61850",
        PROJECT_ROOT.parent.parent / "ep7_datamodel" / "datamodel" / "iec61850",
    ]
    for c in candidates:
        if (c / "DOType").exists() and (c / "DAType").exists() and (c / "LNodeType").exists():
            return c
    raise RuntimeError("Cannot locate iec61850_dir (ep7_datamodel/datamodel/iec61850)")


def _iter_named_children(parent, tag_local: str):
    import xml.etree.ElementTree as ET

    def local_name(t: str) -> str:
        return t.split("}")[-1] if "}" in t else t

    for ch in list(parent):
        if isinstance(ch.tag, str) and local_name(ch.tag) == tag_local:
            yield ch


def _find_doi(ln_el, do_name: str):
    for doi in _iter_named_children(ln_el, "DOI"):
        if (doi.attrib.get("name") or "").strip() == do_name:
            return doi
    return None


def _has_sdi(doi_el, sdi_name: str) -> bool:
    for sdi in _iter_named_children(doi_el, "SDI"):
        if (sdi.attrib.get("name") or "").strip() == sdi_name:
            return True
    return False


def main() -> int:
    iec61850_dir = _find_repo_iec61850_dir()
    cat = scan_type_catalog(iec61850_dir)

    # Pick a template that exists and likely includes FltA; adjust here if your repo uses a different one.
    # We try a few common ones.
    candidate_ids = [
        "SE_PDIS_ZN_PowerLogic_V001",
        "SE_PDIS_PowerLogic_V001",
    ]

    template_info = None
    for tid in candidate_ids:
        template_info = next((x for x in cat.lnode_types if x.id == tid), None)
        if template_info:
            break

    if template_info is None:
        print("SKIP: No candidate LNodeType template found for validation")
        return 0

    tmpl = load_lnode_type(template_info)

    out_path = PROJECT_ROOT / "testcase" / "__tmp_generated" / "__tmp_fltA.xml"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = create_ln_instance_from_template(
        iec61850_dir=iec61850_dir,
        template=tmpl,
        target_path=out_path,
        prefix="ZN",
        inst="0",
        create_empty_val_for_edit=False,
    )

    ln_el = doc.ln_elements[0]
    doi = _find_doi(ln_el, "FltA")
    if doi is None:
        print("SKIP: template does not contain DOI FltA")
        return 0

    missing = [n for n in ("phsA", "phsB", "phsC") if not _has_sdi(doi, n)]
    if missing:
        print(f"FAIL: FltA missing SDI: {missing}")
        return 1

    print("OK: FltA contains phsA/phsB/phsC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
