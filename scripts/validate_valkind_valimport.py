"""Validate that valKind/valImport metadata is preserved in generated instances.

We use SE_PDIS_ZN_PowerLogic_V001 as a stable template.
Expectation example:
- X1 is SE_ASG_SGFLOAT... and contains DA 'd' with valKind="RO" valImport="false" in DOType.
- Generated instance should therefore contain DAI 'd' with the same attributes.

Run:
  python scripts/validate_valkind_valimport.py
"""

from __future__ import annotations

import sys
from pathlib import Path

here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))

from ln_instance_scanner import create_ln_instance_from_template


def find_dai(root_ln, *, doi_name: str, dai_name: str):
    # Simple walk DOI -> DAI (no SDI handling needed for this check)
    for doi in list(root_ln):
        if not isinstance(doi.tag, str) or doi.tag.split('}', 1)[-1] != 'DOI':
            continue
        if (doi.attrib.get('name') or '').strip() != doi_name:
            continue
        for ch in list(doi):
            if not isinstance(ch.tag, str) or ch.tag.split('}', 1)[-1] != 'DAI':
                continue
            if (ch.attrib.get('name') or '').strip() == dai_name:
                return ch
    return None


def main() -> int:
    root = dbmeditor_dir.parent
    iec = root / 'ep7_datamodel' / 'datamodel' / 'iec61850'
    tpl = iec / 'LNodeType' / 'SE_PDIS_ZN_PowerLogic_V001.xml'

    doc = create_ln_instance_from_template(
        iec61850_dir=iec,
        template=tpl,
        target_path=dbmeditor_dir / '__tmp_valkind_valimport.xml',
        prefix='',
        inst='0',
        ln_desc='',
    )
    ln = doc.ln_elements[0]

    dai_d = find_dai(ln, doi_name='X1', dai_name='d')
    if dai_d is None:
        print('FAIL: missing DOI:X1/DAI:d')
        return 2

    vk = (dai_d.attrib.get('valKind') or '').strip()
    vi = (dai_d.attrib.get('valImport') or '').strip()

    if vk != 'RO' or vi.lower() != 'false':
        print('FAIL: unexpected attributes on DOI:X1/DAI:d', {'valKind': vk, 'valImport': vi})
        return 2

    print('OK: valKind/valImport preserved on DOI:X1/DAI:d')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
