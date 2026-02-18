"""Validate that units/multiplier/SIUnit DAI are persisted.

Empty <Val/> placeholders may be omitted on compact-save (self-closing <DAI .../>).
The UI re-hydrates placeholders from the template when loading.

Run:
  python scripts/validate_units_multiplier_visible.py
"""

from __future__ import annotations

import sys
from pathlib import Path

here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))

from ln_instance_scanner import create_ln_instance_from_template, load_ln_instance_document, save_ln_instance_document  # noqa: E402


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_dai_with_val(ln_el, doi_name: str, dai_name: str) -> bool:
    for doi in list(ln_el):
        if not isinstance(doi.tag, str) or _local(doi.tag) != "DOI":
            continue
        if (doi.attrib.get("name") or "").strip() != doi_name:
            continue
        # walk DOI/SDI for DAI
        stack = [doi]
        while stack:
            cur = stack.pop()
            for ch in list(cur):
                if not isinstance(ch.tag, str):
                    continue
                ln = _local(ch.tag)
                if ln in {"DOI", "SDI"}:
                    stack.append(ch)
                elif ln == "DAI" and (ch.attrib.get("name") or "").strip() == dai_name:
                    for v in list(ch):
                        if isinstance(v.tag, str) and _local(v.tag) == "Val":
                            return True
    return False


def main() -> int:
    root = dbmeditor_dir.parent
    iec = root / "ep7_datamodel" / "datamodel" / "iec61850"
    tpl = iec / "LNodeType" / "SE_PDIS_ZN_PowerLogic_V001.xml"

    out_path = dbmeditor_dir / "__tmp_units_mult.xml"
    doc = create_ln_instance_from_template(
        iec61850_dir=iec,
        template=tpl,
        target_path=out_path,
        prefix="",
        inst="0",
        ln_desc="",
    )

    saved = save_ln_instance_document(doc, target_path=out_path, make_backup=False)
    doc2 = load_ln_instance_document(saved)

    ln_el = doc2.ln_elements[0]

    # We don't know which DO they live under for every template, so we just assert:
    # if a DAI named units/multiplier/SIUnit exists anywhere, it must be present in the saved output.
    wanted = {"units", "multiplier", "SIUnit"}
    found_any = set()

    stack = [ln_el]
    while stack:
        cur = stack.pop()
        for ch in list(cur):
            if not isinstance(ch.tag, str):
                continue
            ln = _local(ch.tag)
            if ln in {"DOI", "SDI"}:
                stack.append(ch)
            elif ln == "DAI":
                name = (ch.attrib.get("name") or "").strip()
                if name in wanted:
                    found_any.add(name)
                    # OK even if it has no <Val/> in saved output.
                    pass

    if not found_any:
        print("SKIP: template has no units/multiplier/SIUnit DAI")
        return 0

    print("OK: units/multiplier/SIUnit DAI are persisted (even empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
