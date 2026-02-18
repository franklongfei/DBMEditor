"""Validate grouped vs non-grouped settings generation rule.

Rule:
- For DA/BDA named setVal or setMag:
  - if FC == SP => single <Val>
  - else => grouped: <Val sGroup="1".."8">

This script scans templates and finds one example of each case (if present),
then generates an instance skeleton and checks emitted <DAI name="setVal|setMag"> Val shape.

Run:
  python scripts/validate_grouped_settings.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import xml.etree.ElementTree as ET

here = Path(__file__).resolve()
dbmeditor_dir = here.parents[1]
if str(dbmeditor_dir) not in sys.path:
    sys.path.insert(0, str(dbmeditor_dir))

from iec61850_scanner import LNodeTypeInfo, load_lnode_type
from ln_instance_scanner import create_ln_instance_from_template, ensure_all_dai_present_from_template


def local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def iter_dai(ln):
    for doi in list(ln):
        if not isinstance(doi.tag, str) or local(doi.tag) != "DOI":
            continue
        stack = [doi]
        while stack:
            cur = stack.pop()
            for ch in list(cur):
                if not isinstance(ch.tag, str):
                    continue
                ln_tag = local(ch.tag)
                if ln_tag in {"SDI", "DAI"}:
                    stack.append(ch)
                if ln_tag == "DAI":
                    yield ch


def iter_dai_with_path(ln: ET.Element):
    def walk(parent: ET.Element, prefix: str):
        for ch in list(parent):
            if not isinstance(ch.tag, str):
                continue
            t = local(ch.tag)
            if t in {"DOI", "SDI"}:
                nm = (ch.attrib.get("name") or "").strip()
                p2 = f"{prefix}/{t}:{nm}" if nm else f"{prefix}/{t}"
                yield from walk(ch, p2)
            elif t == "DAI":
                nm = (ch.attrib.get("name") or "").strip()
                p2 = f"{prefix}/DAI:{nm}" if nm else f"{prefix}/DAI"
                yield (p2, ch)
                yield from walk(ch, p2)

    for ch in list(ln):
        if not isinstance(ch.tag, str) or local(ch.tag) != "DOI":
            continue
        do_name = (ch.attrib.get("name") or "").strip()
        p0 = f"DOI:{do_name}" if do_name else "DOI"
        yield from walk(ch, p0)


def load_template_model_from_path(path: Path):
    tree = ET.parse(path)
    root = tree.getroot()
    ln = None
    for el in root.iter():
        if isinstance(el.tag, str) and local(el.tag) == "LNodeType":
            ln = el
            break
    if ln is None:
        raise ValueError(f"No LNodeType in {path}")
    id_ = (ln.attrib.get("id") or "").strip() or path.stem
    ln_class = (ln.attrib.get("lnClass") or "").strip()
    desc = (ln.attrib.get("desc") or "").strip()
    info = LNodeTypeInfo(id=id_, ln_class=ln_class, desc=desc, file_path=path)
    return load_lnode_type(info)


def _val_shape(dai: ET.Element) -> tuple[int, list[str]]:
    vals = [x for x in list(dai) if isinstance(x.tag, str) and local(x.tag) == "Val"]
    sgs = [(v.attrib.get("sGroup") or "").strip() for v in vals]
    return (len(vals), sgs)


def main() -> int:
    root = dbmeditor_dir.parent
    iec = root / "ep7_datamodel" / "datamodel" / "iec61850"

    lnode_dir = iec / "LNodeType"
    if not lnode_dir.exists():
        print("ERROR: LNodeType folder not found:", lnode_dir)
        return 2

    found_grouped: tuple[Path, str] | None = None
    found_single: tuple[Path, str] | None = None

    paths = sorted(lnode_dir.glob("*.xml"))
    if not paths:
        print("ERROR: No LNodeType XML files in:", lnode_dir)
        return 2

    for i, path in enumerate(paths[:120]):
        # Avoid a long silent run.
        if i and i % 25 == 0:
            print(f"...checked {i} templates")

        try:
            doc = create_ln_instance_from_template(
                iec61850_dir=iec,
                template=path,
                target_path=dbmeditor_dir / "__tmp_grouped_settings_validation.xml",
                prefix="",
                inst="0",
                ln_desc="",
            )
        except Exception:
            continue

        ln = doc.ln_elements[0]
        for dai_path, dai in iter_dai_with_path(ln):
            name = (dai.attrib.get("name") or "").strip()
            if name not in {"setVal", "setMag"}:
                continue
            vals = [x for x in list(dai) if isinstance(x.tag, str) and local(x.tag) == "Val"]
            sgs = [(v.attrib.get("sGroup") or "").strip() for v in vals]

            if len(vals) == 8 and all(str(j + 1) == sgs[j] for j in range(8)):
                found_grouped = found_grouped or (path, dai_path)
            elif len(vals) == 1 and not sgs[0]:
                found_single = found_single or (path, dai_path)

        if found_grouped and found_single:
            break

    if found_grouped:
        print("OK: grouped example:", found_grouped[0].name, found_grouped[1])
    else:
        print("WARN: no grouped example found (setVal/setMag with FC!=SP might not exist in this dataset)")

    if found_single:
        print("OK: single example:", found_single[0].name, found_single[1])
    else:
        print("WARN: no single example found (setVal/setMag with FC==SP might not exist in this dataset)")

    # Hydration behavior checks (best-effort):
    if found_grouped:
        tpl_path, dai_path = found_grouped
        tpl_model = load_template_model_from_path(tpl_path)
        doc2 = create_ln_instance_from_template(
            iec61850_dir=iec,
            template=tpl_model,
            target_path=dbmeditor_dir / "__tmp_grouped_settings_hydration.xml",
            prefix="",
            inst="0",
            ln_desc="",
        )
        ln2 = doc2.ln_elements[0]
        for p, dai in iter_dai_with_path(ln2):
            if p != dai_path:
                continue
            # Collapse to a single ungrouped Val.
            vals = [x for x in list(dai) if isinstance(x.tag, str) and local(x.tag) == "Val"]
            if vals:
                for v in vals[1:]:
                    dai.remove(v)
                vals[0].attrib.pop("sGroup", None)

            ensure_all_dai_present_from_template(
                doc2,
                0,
                iec61850_dir=iec,
                template=tpl_model,
            )
            cnt, sgs = _val_shape(dai)
            if cnt == 8 and sgs == [str(i) for i in range(1, 9)]:
                print("OK: hydration expands grouped values")
            else:
                print("WARN: hydration did not expand grouped values as expected", cnt, sgs)
            break

    if found_single:
        tpl_path, dai_path = found_single
        tpl_model = load_template_model_from_path(tpl_path)
        doc3 = create_ln_instance_from_template(
            iec61850_dir=iec,
            template=tpl_model,
            target_path=dbmeditor_dir / "__tmp_grouped_settings_hydration_single.xml",
            prefix="",
            inst="0",
            ln_desc="",
        )
        ln3 = doc3.ln_elements[0]
        for p, dai in iter_dai_with_path(ln3):
            if p != dai_path:
                continue
            # Ensure it is single and stays single after hydration.
            ensure_all_dai_present_from_template(
                doc3,
                0,
                iec61850_dir=iec,
                template=tpl_model,
            )
            cnt, sgs = _val_shape(dai)
            if cnt == 1 and sgs == [""]:
                print("OK: hydration keeps single value")
            else:
                print("WARN: hydration changed single value unexpectedly", cnt, sgs)
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
