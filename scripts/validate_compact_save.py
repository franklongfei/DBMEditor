"""Quick validation: UI-hydrated instance vs compact-on-disk save.

- Create an instance from template (full placeholders in-memory)
- Save it (save prunes empty DAI in the serialized output)
- Reload the saved file (should have fewer ValueRef rows)
- Hydrate from template (should restore full placeholder rows)

Run:
  python scripts/validate_compact_save.py
"""

from __future__ import annotations

import sys
from pathlib import Path


here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))

from iec61850_scanner import load_lnode_type, scan_type_catalog
from ln_instance_scanner import (
    create_ln_instance_from_template,
    ensure_all_dai_present_from_template,
    extract_value_refs,
    load_ln_instance_document,
    save_ln_instance_document,
)


def main() -> int:
    root = dbmeditor_dir.parent
    iec = root / "ep7_datamodel" / "datamodel" / "iec61850"
    cat = scan_type_catalog(iec)

    preferred_id = "SE_PSCH_Teleprotection_PowerLogic_V001"
    info = next((x for x in cat.lnode_types if x.id == preferred_id), None) or cat.lnode_types[0]
    model = load_lnode_type(info)

    out_path = dbmeditor_dir / "__tmp_compact_save.xml"
    if out_path.exists():
        out_path.unlink()

    doc = create_ln_instance_from_template(
        iec61850_dir=iec,
        template=model,
        target_path=out_path,
        prefix="AID",
        inst="0",
        ln_desc="validate",
    )

    n_full = len(extract_value_refs(doc, 0))

    save_ln_instance_document(doc, target_path=out_path, make_backup=False)
    disk_text = out_path.read_text(encoding="utf-8")

    loaded = load_ln_instance_document(out_path)
    n_disk = len(extract_value_refs(loaded, 0))

    ensure_all_dai_present_from_template(loaded, 0, iec61850_dir=iec, template=model)
    n_hydrated = len(extract_value_refs(loaded, 0))

    empty_val_markers = sum(1 for line in disk_text.splitlines() if "<Val" in line and "></Val>" in line or "<Val />" in line)

    print("template", info.id)
    print("ValueRef count: full(in-memory) ", n_full)
    print("ValueRef count: disk(compact)   ", n_disk)
    print("ValueRef count: hydrated(UI)    ", n_hydrated)
    print("disk empty Val markers          ", empty_val_markers)

    ok = (n_hydrated == n_full) and (n_disk <= n_full)
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
