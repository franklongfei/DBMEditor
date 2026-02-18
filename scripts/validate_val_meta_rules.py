"""Validate valKind/valImport filling rules for generated instances.

Rules:
1) Template overrides per attribute: if template defines valKind and/or valImport, keep those.
2) If either attribute is missing, fill it from FC:
    - FC in {SE, SP}: valKind=Set valImport=true
    - Else: valKind=RO valImport=false

This script checks:
- A real template (SE_PDIS_ZN) for:
  - X1/setMag/f should get Set/true (FC inherited from setMag fc=SE)
    - SetMod/dataNs should NOT be generated in LNDM instances
- A synthetic single-DO template built from an arbitrary DOType that contains
    a DA with only one of {valKind,valImport}, ensuring we fill the missing one
    based on FC.

Run:
  python scripts/validate_val_meta_rules.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
import xml.etree.ElementTree as ET

here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))

from iec61850_scanner import DOItem, LNodeTypeInfo, LNodeTypeModel
from ln_instance_scanner import create_ln_instance_from_template


def local(tag: str) -> str:
    return tag.split('}', 1)[1] if tag.startswith('{') else tag


def find_path(ln: ET.Element, want: list[tuple[str, str]]):
    # want is sequence like [('DOI','X1'),('SDI','setMag'),('DAI','f')]
    cur = ln
    for t, name in want:
        found = None
        for ch in list(cur):
            if not isinstance(ch.tag, str) or local(ch.tag) != t:
                continue
            if (ch.attrib.get('name') or '').strip() != name:
                continue
            found = ch
            break
        if found is None:
            return None
        cur = found
    return cur


def assert_meta(el: ET.Element, vk: str, vi: str, *, label: str) -> None:
    got_vk = (el.attrib.get('valKind') or '').strip()
    got_vi = (el.attrib.get('valImport') or '').strip()
    if got_vk != vk or got_vi.lower() != vi.lower():
        raise AssertionError(f"{label}: expected valKind={vk} valImport={vi}, got valKind={got_vk} valImport={got_vi}")


def _defaults_from_fc(fc: str) -> tuple[str, str]:
    fc0 = (fc or '').strip().upper()
    if fc0 in {'SE', 'SP'}:
        return ('Set', 'true')
    return ('RO', 'false')


def find_partial_template_do_type(do_type_dir: Path) -> tuple[str, str, str, str, str, str]:
    """Return (do_type_id, da_name, which, template_vk, template_vi, fc)."""
    for p in sorted(do_type_dir.glob('*.xml')):
        try:
            t = ET.parse(p)
        except Exception:
            continue
        r = t.getroot()
        ns = r.tag.split('}', 1)[0][1:] if r.tag.startswith('{') else ''
        q = (lambda x: f"{{{ns}}}{x}") if ns else (lambda x: x)
        do = r.find(f".//{q('DOType')}")
        if do is None:
            continue
        do_id = (do.attrib.get('id') or '').strip() or p.stem
        for da in list(do):
            if not isinstance(da.tag, str) or local(da.tag) != 'DA':
                continue
            btype = (da.attrib.get('bType') or '').strip().lower()
            if btype == 'struct':
                continue
            name = (da.attrib.get('name') or '').strip()
            if not name:
                continue
            vk = (da.attrib.get('valKind') or '').strip()
            vi = (da.attrib.get('valImport') or '').strip()
            fc = (da.attrib.get('fc') or '').strip()
            if vk and not vi:
                return (do_id, name, 'valKind-only', vk, vi, fc)
            if vi and not vk:
                return (do_id, name, 'valImport-only', vk, vi, fc)
    raise RuntimeError('No DOType with partial valKind/valImport found in dataset')


def main() -> int:
    root = dbmeditor_dir.parent
    iec = root / 'ep7_datamodel' / 'datamodel' / 'iec61850'

    # Real template checks
    tpl = iec / 'LNodeType' / 'SE_PDIS_ZN_PowerLogic_V001.xml'
    doc = create_ln_instance_from_template(iec61850_dir=iec, template=tpl, target_path=Path('x'), prefix='', inst='0', ln_desc='')
    ln = doc.ln_elements[0]

    # X1 / setMag (fc=SE, struct) -> f leaf should be Set/true
    f_dai = find_path(ln, [('DOI', 'X1'), ('SDI', 'setMag'), ('DAI', 'f')])
    if f_dai is None:
        print('FAIL: missing X1/setMag/f')
        return 2
    assert_meta(f_dai, 'Set', 'true', label='X1/setMag/f')

    # SetMod / dataNs should not exist
    dn_dai = find_path(ln, [('DOI', 'SetMod'), ('DAI', 'dataNs')])
    if dn_dai is not None:
        print('FAIL: unexpected SetMod/dataNs exists (should not be generated)')
        return 1

    # Partial-template-definition check using a synthetic one-DO LNodeTypeModel
    do_id, da_name, which, template_vk, template_vi, fc = find_partial_template_do_type(iec / 'DOType')

    model = LNodeTypeModel(
        info=LNodeTypeInfo(id='__TMP__', ln_class='ZZZ', desc='', file_path=Path('')),
        lnode_attrib={'lnClass': 'ZZZ', 'id': '__TMP__'},
        dos=[DOItem(name='T1', do_type=do_id)],
        privates=[],
    )

    doc2 = create_ln_instance_from_template(iec61850_dir=iec, template=model, target_path=Path('x2'), prefix='', inst='0', ln_desc='')
    ln2 = doc2.ln_elements[0]
    dai = find_path(ln2, [('DOI', 'T1'), ('DAI', da_name)])
    if dai is None:
        print('FAIL: missing synthetic DOI:T1/DAI:', da_name, 'from DOType', do_id)
        return 2

    got_vk = (dai.attrib.get('valKind') or '').strip()
    got_vi = (dai.attrib.get('valImport') or '').strip()

    default_vk, default_vi = _defaults_from_fc(fc)

    if which == 'valKind-only':
        if got_vk != template_vk or got_vi.lower() != default_vi.lower():
            print(
                'FAIL: expected template valKind preserved and valImport filled from FC, got',
                {'valKind': got_vk, 'valImport': got_vi},
                'expected',
                {'valKind': template_vk, 'valImport': default_vi},
                'FC',
                fc,
                'DOType',
                do_id,
                'DA',
                da_name,
            )
            return 2
    else:
        if got_vi.lower() != template_vi.lower() or got_vk != default_vk:
            print(
                'FAIL: expected template valImport preserved and valKind filled from FC, got',
                {'valKind': got_vk, 'valImport': got_vi},
                'expected',
                {'valKind': default_vk, 'valImport': template_vi},
                'FC',
                fc,
                'DOType',
                do_id,
                'DA',
                da_name,
            )
            return 2

    print('OK: rules validated (real template + partial-template case)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
