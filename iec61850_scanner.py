from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import textwrap
import xml.etree.ElementTree as ET


SCL_NS = "http://www.iec.ch/61850/2003/SCL"


def _q(tag: str) -> str:
    return f"{{{SCL_NS}}}{tag}"


@dataclass(frozen=True)
class LNodeTypeInfo:
    id: str
    ln_class: str
    desc: str
    file_path: Path


@dataclass
class TypeCatalog:
    do_types: list[str]
    da_types: list[str]
    enum_types: list[str]
    lnode_types: list[LNodeTypeInfo]


@dataclass
class DOItem:
    name: str
    do_type: str
    # DO-level <Private> blocks (we call these "rules" in the editor)
    privates: list[PrivateItem] = field(default_factory=list)


@dataclass
class PrivateItem:
    attrib: dict[str, str]
    inner_xml: str = ""


@dataclass
class LNodeTypeModel:
    info: LNodeTypeInfo
    lnode_attrib: dict[str, str]
    dos: list[DOItem]
    privates: list[PrivateItem]


def _safe_parse(path: Path) -> ET.ElementTree | None:
    try:
        return ET.parse(path)
    except Exception:
        return None


def _iter_lnode_type_elements(root: ET.Element) -> list[ET.Element]:
    out: list[ET.Element] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag == _q("LNodeType") or el.tag.endswith("}LNodeType") or el.tag == "LNodeType":
            out.append(el)
    return out


def _collect_scl_ids_from_files(folder: Path, tag: str) -> set[str]:
    out: set[str] = set()
    if not folder.exists():
        return out

    # Templates are organized in subfolders (e.g., P7/, P3Plus/). Scan recursively.
    for path in folder.rglob("*.xml"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        root = tree.getroot()
        for el in root.findall(f".//{_q(tag)}"):
            id_ = el.attrib.get("id")
            if id_:
                out.add(id_)
    return out


def _collect_do_types_from_list(do_type_list_path: Path) -> set[str]:
    out: set[str] = set()
    if not do_type_list_path.is_file():
        return out

    tree = _safe_parse(do_type_list_path)
    if tree is None:
        return out

    root = tree.getroot()
    # Format is <LIST><Type id=".." ref="SE_xxx"/></LIST>
    for el in root.findall(".//Type"):
        ref = (el.attrib.get("ref") or "").strip()
        if ref:
            out.add(ref)
    return out


def scan_type_catalog(iec61850_dir: str | Path) -> TypeCatalog:
    """Scan %REPO_ROOT%/ep7_datamodel/datamodel/iec61850 for DAType/DOType/EnumType and LNodeType templates."""
    iec61850_dir = Path(iec61850_dir)

    do_type_dir = iec61850_dir / "DOType"
    da_type_dir = iec61850_dir / "DAType"
    enum_type_dir = iec61850_dir / "EnumType"
    lnode_type_dir = iec61850_dir / "LNodeType"

    do_types = set()
    do_types |= _collect_do_types_from_list(do_type_dir / "DoTypeList.xml")
    # Also collect from actual DOType xml files if present
    do_types |= _collect_scl_ids_from_files(do_type_dir, "DOType")

    da_types = _collect_scl_ids_from_files(da_type_dir, "DAType")

    enum_types: set[str] = set()
    enum_types |= _collect_scl_ids_from_files(enum_type_dir, "EnumType")

    lnode_types: list[LNodeTypeInfo] = []
    if lnode_type_dir.exists():
        for path in lnode_type_dir.rglob("*.xml"):
            tree = _safe_parse(path)
            if tree is None:
                continue
            root = tree.getroot()
            for ln in _iter_lnode_type_elements(root):
                id_ = (ln.attrib.get("id") or "").strip()
                ln_class = (ln.attrib.get("lnClass") or "").strip()
                desc = (ln.attrib.get("desc") or "").strip()
                if id_:
                    lnode_types.append(LNodeTypeInfo(id=id_, ln_class=ln_class, desc=desc, file_path=path))

    lnode_types.sort(key=lambda x: (x.ln_class, x.id))

    return TypeCatalog(
        do_types=sorted(do_types),
        da_types=sorted(da_types),
        enum_types=sorted(enum_types),
        lnode_types=lnode_types,
    )


def load_lnode_type(info: LNodeTypeInfo) -> LNodeTypeModel:
    tree = ET.parse(info.file_path)
    root = tree.getroot()
    ln: ET.Element | None = None
    target_id = (getattr(info, "id", "") or "").strip()
    if target_id:
        for el in _iter_lnode_type_elements(root):
            if (el.attrib.get("id") or "").strip() == target_id:
                ln = el
                break
    if ln is None:
        all_lnodes = _iter_lnode_type_elements(root)
        ln = all_lnodes[0] if all_lnodes else None
    if ln is None:
        raise ValueError(f"No LNodeType found in {info.file_path}")

    def _normalize_rule_text(value: str) -> str:
        # Vendor templates often indent CDATA to match XML indentation.
        # That indentation becomes part of the rule text, causing huge leading
        # spaces on every line when re-opened. Normalize by dedenting.
        value = (value or "").replace("\r\n", "\n").replace("\r", "\n")
        # Templates sometimes mix tabs and spaces. Expand tabs first so dedent can
        # reliably strip the common indentation baseline and avoid "indent growth"
        # after round-trips.
        value = value.expandtabs(4)
        value = value.strip("\n")
        value = textwrap.dedent(value)
        return value.strip()

    def _inner_xml_from_private(p: ET.Element, *, typ: str) -> str:
        # Keep a best-effort inner XML representation so it can be edited.
        # This is not a byte-for-byte roundtrip for complex XML, but it prevents
        # losing the Private blocks on save.
        parts: list[str] = []
        txt = (p.text or "")
        if txt.strip():
            if (typ or "").startswith("SchneiderElectric-PowerLogic-Rules"):
                parts.append(_normalize_rule_text(txt))
            else:
                parts.append(txt.strip())
        for child in list(p):
            parts.append(ET.tostring(child, encoding="unicode"))
        return "".join(parts).strip()

    def _private_items_from_parent(parent: ET.Element) -> list[PrivateItem]:
        out: list[PrivateItem] = []
        for p in parent.findall(_q("Private")):
            attrib = {k: str(v) for k, v in p.attrib.items()}
            inner_xml = _inner_xml_from_private(p, typ=(attrib.get("type") or ""))
            out.append(PrivateItem(attrib=attrib, inner_xml=inner_xml))
        return out

    dos: list[DOItem] = []
    for do in ln.findall(_q("DO")):
        name = (do.attrib.get("name") or "").strip()
        do_type = (do.attrib.get("type") or "").strip()
        if not name:
            continue
        do_privs = _private_items_from_parent(do)
        dos.append(DOItem(name=name, do_type=do_type, privates=do_privs))

    # LN-level <Private> blocks (apply to the whole LN template)
    privates = _private_items_from_parent(ln)

    lnode_attrib = {k: str(v) for k, v in ln.attrib.items()}
    resolved_info = LNodeTypeInfo(
        id=(lnode_attrib.get("id") or info.id or "").strip(),
        ln_class=(lnode_attrib.get("lnClass") or info.ln_class or "").strip(),
        desc=(lnode_attrib.get("desc") or info.desc or "").strip(),
        file_path=info.file_path,
    )
    return LNodeTypeModel(info=resolved_info, lnode_attrib=lnode_attrib, dos=dos, privates=privates)


def save_lnode_type(
    model: LNodeTypeModel,
    *,
    make_backup: bool = True,
    target_path: Path | None = None,
) -> None:
    """Save the LNodeType file while keeping a stable, expected header/footer.

    Requirement:
    - First line: <?xml version="1.0" encoding="utf-8" ?>
    - Second line: <SCL ...> (single-line, exact attribute set)
    - Last line: </SCL>
    """
    path = target_path or model.info.file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if make_backup and path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            bak.write_bytes(path.read_bytes())

    xml_decl = '<?xml version="1.0" encoding="utf-8" ?>'
    scl_open = (
        '<SCL xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns="http://www.iec.ch/61850/2003/SCL" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.iec.ch/61850/2003/SCL SCL.xsd">'
    )

    def esc(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    attrib = dict(model.lnode_attrib or {})
    # Ensure required attributes exist (keep any extra attributes)
    attrib.setdefault("lnClass", model.info.ln_class)
    attrib.setdefault("id", model.info.id)

    # Stable attribute order: lnClass, id, desc, then rest sorted
    ordered_keys: list[str] = []
    for k in ("lnClass", "id", "desc"):
        if k in attrib and str(attrib.get(k) or "") != "":
            ordered_keys.append(k)
    for k in sorted(attrib.keys()):
        if k not in ordered_keys and str(attrib.get(k) or "") != "":
            ordered_keys.append(k)

    lnode_attr_text = " ".join(f'{k}="{esc(str(attrib[k]))}"' for k in ordered_keys)

    def _write_private_block(lines: list[str], p: PrivateItem, *, indent: str) -> None:
        p_attrib = dict(p.attrib or {})

        ordered_p_keys: list[str] = []
        if "type" in p_attrib and str(p_attrib.get("type") or "") != "":
            ordered_p_keys.append("type")
        for k in sorted(p_attrib.keys()):
            if k not in ordered_p_keys and str(p_attrib.get(k) or "") != "":
                ordered_p_keys.append(k)

        p_attr_text = " ".join(f'{k}="{esc(str(p_attrib[k]))}"' for k in ordered_p_keys)
        p_open = f"{indent}<Private {p_attr_text}>" if p_attr_text else f"{indent}<Private>"

        inner_raw = p.inner_xml or ""
        inner = inner_raw.strip("\n")
        if not inner:
            # Keep the explicit open/close form (matches many vendor files)
            if p_attr_text:
                lines.append(f"{indent}<Private {p_attr_text}></Private>")
            else:
                lines.append(f"{indent}<Private></Private>")
            return

        # For PowerLogic rule blocks, writing the rule body in CDATA is the safest and
        # matches the vendor examples. If the user already provided explicit CDATA/markup,
        # keep it as-is.
        typ = (p_attrib.get("type") or "").strip()
        should_wrap_cdata = (
            typ.startswith("SchneiderElectric-PowerLogic-Rules")
            and "<![CDATA[" not in inner
            and "<" not in inner
        )

        lines.append(p_open)
        if should_wrap_cdata:
            lines.append(f"{indent}    <![CDATA[")
            # Indent rule lines 4 spaces more than the <![CDATA[ line for readable XML.
            # To avoid indentation growing on round-trips, normalize first.
            normalized = textwrap.dedent(inner.expandtabs(4)).strip("\n")
            for ln_inner in normalized.splitlines():
                # Keep blank lines, but still align them visually.
                if ln_inner.strip() == "":
                    lines.append(f"{indent}        ")
                else:
                    # Keep relative indentation inside the rule body (e.g. THEN blocks).
                    lines.append(f"{indent}        {ln_inner.rstrip()}")
            lines.append(f"{indent}    ]]>")
        else:
            for ln_inner in inner.splitlines():
                lines.append(f"{indent}    {ln_inner}")
        lines.append(f"{indent}</Private>")

    lines: list[str] = [xml_decl, scl_open]
    lines.append(f"    <LNodeType {lnode_attr_text}>")

    # Requirement: LN-level privates live above all DOs and apply to the whole LN.
    for p in getattr(model, "privates", []) or []:
        _write_private_block(lines, p, indent="        ")

    for item in model.dos:
        rules = getattr(item, "privates", []) or []
        if not rules:
            lines.append(f"        <DO name=\"{esc(item.name)}\" type=\"{esc(item.do_type)}\" />")
            continue

        lines.append(f"        <DO name=\"{esc(item.name)}\" type=\"{esc(item.do_type)}\">")
        for rp in rules:
            _write_private_block(lines, rp, indent="            ")
        lines.append("        </DO>")

    lines.append("    </LNodeType>")
    lines.append("</SCL>")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
