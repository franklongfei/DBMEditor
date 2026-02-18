"""Validate InRef placeholders are persisted.

Requirements:
1) setSrcRef and purpose should always appear in generated LNDM files (even if empty)

Note: empty <Val/> placeholders may be omitted on compact-save (self-closing <DAI .../>).
The UI re-hydrates placeholders from the template when loading.

This validator asserts that for a known template, every DOI whose name starts with
"InRef" contains:
- DAI name="setSrcRef" (Val optional)
- DAI name="purpose"  (Val optional)

Run:
  python scripts/validate_inref_purpose_setsrcref.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import xml.etree.ElementTree as ET

here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))

from ln_instance_scanner import create_ln_instance_from_template, save_ln_instance_document  # noqa: E402


def local(tag: str) -> str:
    return tag.split('}', 1)[1] if tag.startswith('{') else tag


def find_child(parent: ET.Element, tag_local: str, name: str) -> ET.Element | None:
    for ch in list(parent):
        if not isinstance(ch.tag, str) or local(ch.tag) != tag_local:
            continue
        if (ch.attrib.get('name') or '').strip() != name:
            continue
        return ch
    return None


def main() -> int:
    root = dbmeditor_dir.parent
    iec = root / 'ep7_datamodel' / 'datamodel' / 'iec61850'

    tpl = iec / 'LNodeType' / 'SE_PDIS_ZN_PowerLogic_V001.xml'
    doc = create_ln_instance_from_template(
        iec61850_dir=iec,
        template=tpl,
        target_path=dbmeditor_dir / '__tmp_inref_validate.xml',
        prefix='',
        inst='0',
        ln_desc='',
    )

    out_path = save_ln_instance_document(doc, target_path=dbmeditor_dir / '__tmp_inref_validate_saved.xml', make_backup=False)
    tree = ET.parse(out_path)
    r = tree.getroot()

    # Find the first LN element.
    ln = None
    for el in r.iter():
        if isinstance(el.tag, str) and local(el.tag) in {'LN', 'LN0', 'LNode'}:
            ln = el
            break
    if ln is None:
        print('FAIL: no LN element found')
        return 2

    inref_count = 0
    for doi in list(ln):
        if not isinstance(doi.tag, str) or local(doi.tag) != 'DOI':
            continue
        name = (doi.attrib.get('name') or '').strip()
        if not name.startswith('InRef'):
            continue
        inref_count += 1

        dai_src = find_child(doi, 'DAI', 'setSrcRef')
        dai_pur = find_child(doi, 'DAI', 'purpose')

        if dai_src is None:
            print('FAIL:', name, 'missing DAI:setSrcRef')
            return 2
        if dai_pur is None:
            print('FAIL:', name, 'missing DAI:purpose')
            return 2

    if inref_count == 0:
        print('WARN: no InRef DOI found in template output (template may have changed)')

    print('OK: InRef purpose/setSrcRef placeholders are present and persisted')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
