from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

from iec61850_scanner import LNodeTypeModel, SCL_NS, load_lnode_type


# Domain/UI rule: these DAI must be persisted even if no value is defined.
# On save we intentionally omit empty <Val/> placeholders for them to reduce noise,
# resulting in a self-closing <DAI .../> in the output XML.
ALWAYS_PERSIST_EMPTY_DAI_NAMES = {
    "units",
    "multiplier",
    "SIUnit",
    "SIUnits",  # compatibility alias (some templates/spellings use plural)
    "setSrcRef",
    "purpose",
}


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _ns_uri(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[0][1:]
    return ""


@dataclass(frozen=True)
class LNElementRef:
    index: int
    tag: str
    attrib: dict[str, str]

    @property
    def label(self) -> str:
        ln_class = (self.attrib.get("lnClass") or "").strip()
        inst = (self.attrib.get("inst") or "").strip()
        prefix = (self.attrib.get("prefix") or "").strip()
        ln_type = (self.attrib.get("lnType") or "").strip()
        parts = []
        if ln_class:
            parts.append(ln_class)
        if prefix:
            parts.append(prefix)
        if inst:
            parts.append(inst)
        if ln_type:
            parts.append(f"[{ln_type}]")
        base = " ".join(parts).strip() or f"LN#{self.index}"
        return f"{self.index}: {base}"


@dataclass
class ValueRef:
    path: str
    val_elements: list[ET.Element]
    dai_element: ET.Element

    def get_value_text(self) -> str:
        vals: list[str] = []
        for ve in self.val_elements:
            vals.append((ve.text or "").strip())
        return "\n".join(vals)

    def set_value_text(self, text: str) -> None:
        lines = [ln.rstrip("\r") for ln in (text or "").split("\n")]
        # Trim trailing blank lines
        while lines and not lines[-1].strip():
            lines.pop()

        if not self.val_elements:
            return

        if len(self.val_elements) == 1:
            self.val_elements[0].text = ("\n".join(lines)).strip()
            return

        # Multi-Val: map line-per-Val; keep extra Vals blank if fewer lines.
        for i, ve in enumerate(self.val_elements):
            ve.text = (lines[i].strip() if i < len(lines) else "")


@dataclass
class LangRefRef:
    path: str
    dai_element: ET.Element
    private_type: str = "SchneiderElectric-PowerLogic-LangRef"
    private_element: ET.Element | None = None
    val_element: ET.Element | None = None

    def get_private_text(self) -> str:
        if self.private_element is None:
            return ""
        return (self.private_element.text or "").strip()

    def get_group_label(self) -> tuple[str, str]:
        raw = self.get_private_text()
        if not raw:
            return ("", "")
        if "." not in raw:
            return (raw.strip(), "")
        g, l = raw.split(".", 1)
        return (g.strip(), l.strip())

    def get_label_text(self) -> str:
        if self.val_element is None:
            return ""
        return (self.val_element.text or "").strip()

    def set_group_label(self, group_id: str, label_id: str) -> None:
        g = (group_id or "").strip()
        l = (label_id or "").strip()

        if not g and not l:
            # Clear, but keep the element if it exists (non-destructive round-trip).
            if self.private_element is not None:
                self.private_element.text = ""
            return

        # Ensure we have a <Private ...> node.
        if self.private_element is None:
            ns = _ns_uri(self.dai_element.tag)
            tag = f"{{{ns}}}Private" if ns else "Private"
            pe = ET.Element(tag)
            pe.attrib["type"] = (self.private_type or "").strip() or "SchneiderElectric-PowerLogic-LangRef"

            # Insert before the first <Val> if present, otherwise append.
            insert_at = None
            for i, ch in enumerate(list(self.dai_element)):
                if isinstance(ch.tag, str) and _local_name(ch.tag) == "Val":
                    insert_at = i
                    break
            if insert_at is None:
                self.dai_element.append(pe)
            else:
                self.dai_element.insert(insert_at, pe)
            self.private_element = pe

        # Keep type stable.
        if self.private_type:
            self.private_element.attrib["type"] = self.private_type

        if g and l:
            self.private_element.text = f"{g}.{l}"
        else:
            # If one side is missing, store the raw value as-is.
            self.private_element.text = g or l


@dataclass
class LNInstanceDocument:
    file_path: Path
    tree: ET.ElementTree
    ns: str
    ln_elements: list[ET.Element]

    def q(self, name: str) -> str:
        return f"{{{self.ns}}}{name}" if self.ns else name


def load_ln_instance_document(path: Path) -> LNInstanceDocument:
    tree = ET.parse(path)
    root = tree.getroot()
    ns = _ns_uri(root.tag)

    def q(name: str) -> str:
        return f"{{{ns}}}{name}" if ns else name

    ln_elements: list[ET.Element] = []

    root_ln = _local_name(root.tag)
    if root_ln in {"LN", "LN0", "LNode"}:
        ln_elements.append(root)
    else:
        # Prefer LN over LN0 if present.
        ln_elements.extend(list(root.iter(q("LN"))))
        if not ln_elements:
            ln_elements.extend(list(root.iter(q("LN0"))))
        if not ln_elements:
            ln_elements.extend(list(root.iter(q("LNode"))))

    if not ln_elements:
        raise ValueError(f"No LN/LN0/LNode element found in: {path}")

    return LNInstanceDocument(file_path=path, tree=tree, ns=ns, ln_elements=ln_elements)


def list_ln_refs(doc: LNInstanceDocument) -> list[LNElementRef]:
    out: list[LNElementRef] = []
    for idx, el in enumerate(doc.ln_elements):
        out.append(LNElementRef(index=idx, tag=_local_name(el.tag), attrib={k: str(v) for k, v in el.attrib.items()}))
    return out


def _iter_child_elems(el: ET.Element) -> Iterable[ET.Element]:
    # ElementTree iteration includes nested; we only want direct children.
    for ch in list(el):
        if isinstance(ch.tag, str):
            yield ch


def extract_value_refs(doc: LNInstanceDocument, ln_index: int, *, sort: bool = True) -> list[ValueRef]:
    if ln_index < 0 or ln_index >= len(doc.ln_elements):
        raise IndexError("ln_index out of range")

    ln = doc.ln_elements[ln_index]
    q = doc.q

    refs: list[ValueRef] = []

    def walk(parent: ET.Element, prefix: str) -> None:
        # DOI / SDI may contain DAI and SDI
        for ch in _iter_child_elems(parent):
            ln_tag = _local_name(ch.tag)
            if ln_tag in {"DOI", "SDI"}:
                name = (ch.attrib.get("name") or "").strip()
                p2 = f"{prefix}/{ln_tag}:{name}" if name else f"{prefix}/{ln_tag}"
                walk(ch, p2)
            elif ln_tag == "DAI":
                name = (ch.attrib.get("name") or "").strip()
                p2 = f"{prefix}/DAI:{name}" if name else f"{prefix}/DAI"
                val_elems = [x for x in _iter_child_elems(ch) if _local_name(x.tag) == "Val"]
                if val_elems:
                    if len(val_elems) == 1:
                        refs.append(ValueRef(path=p2, val_elements=val_elems, dai_element=ch))
                    else:
                        # Multi-Val (typically grouped settings via sGroup): expose each <Val> separately.
                        # This enables inline table editing without collapsing all groups into the first element.
                        for i, ve in enumerate(val_elems):
                            sg = (ve.attrib.get("sGroup") or "").strip()
                            suffix = f"/Val:sGroup={sg}" if sg else f"/Val:{i+1}"
                            refs.append(ValueRef(path=p2 + suffix, val_elements=[ve], dai_element=ch))
                # Some configs nest SDI under DAI
                walk(ch, p2)
            else:
                # Keep walking, but only through known containers to avoid huge tree
                pass

    for ch in _iter_child_elems(ln):
        if _local_name(ch.tag) == "DOI":
            do_name = (ch.attrib.get("name") or "").strip()
            prefix = f"DOI:{do_name}" if do_name else "DOI"
            walk(ch, prefix)

    if sort:
        refs.sort(key=lambda r: r.path)
    return refs


def extract_langref_refs(
    doc: LNInstanceDocument,
    ln_index: int,
    *,
    private_type: str = "SchneiderElectric-PowerLogic-LangRef",
    sort: bool = True,
) -> list[LangRefRef]:
    if ln_index < 0 or ln_index >= len(doc.ln_elements):
        raise IndexError("ln_index out of range")

    ln = doc.ln_elements[ln_index]
    refs: list[LangRefRef] = []

    def walk(parent: ET.Element, prefix: str) -> None:
        for ch in _iter_child_elems(parent):
            ln_tag = _local_name(ch.tag)
            if ln_tag in {"DOI", "SDI"}:
                name = (ch.attrib.get("name") or "").strip()
                p2 = f"{prefix}/{ln_tag}:{name}" if name else f"{prefix}/{ln_tag}"
                walk(ch, p2)
            elif ln_tag == "DAI":
                name = (ch.attrib.get("name") or "").strip()
                p2 = f"{prefix}/DAI:{name}" if name else f"{prefix}/DAI"

                if name == "d":
                    private_el: ET.Element | None = None
                    val_el: ET.Element | None = None

                    for x in _iter_child_elems(ch):
                        xln = _local_name(x.tag)
                        if xln == "Private":
                            t = (x.attrib.get("type") or "").strip()
                            if t == private_type:
                                private_el = x
                        elif xln == "Val" and val_el is None:
                            val_el = x

                    refs.append(
                        LangRefRef(
                            path=p2,
                            dai_element=ch,
                            private_type=private_type,
                            private_element=private_el,
                            val_element=val_el,
                        )
                    )

                walk(ch, p2)

    for ch in _iter_child_elems(ln):
        if _local_name(ch.tag) == "DOI":
            do_name = (ch.attrib.get("name") or "").strip()
            prefix = f"DOI:{do_name}" if do_name else "DOI"
            walk(ch, prefix)

    if sort:
        refs.sort(key=lambda r: r.path)
    return refs


def update_ln_header(doc: LNInstanceDocument, ln_index: int, *, lnClass: str, inst: str, prefix: str, lnType: str) -> None:
    if ln_index < 0 or ln_index >= len(doc.ln_elements):
        raise IndexError("ln_index out of range")
    ln = doc.ln_elements[ln_index]

    def set_attr(key: str, value: str) -> None:
        v = (value or "").strip()
        if v:
            ln.attrib[key] = v
        else:
            if key in ln.attrib:
                del ln.attrib[key]

    set_attr("lnClass", lnClass)
    set_attr("inst", inst)
    set_attr("prefix", prefix)
    set_attr("lnType", lnType)


def _ensure_backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        return
    bak.write_bytes(path.read_bytes())


def save_ln_instance_document(doc: LNInstanceDocument, *, target_path: Path | None = None, make_backup: bool = True) -> Path:
    out_path = target_path or doc.file_path

    if make_backup and out_path == doc.file_path and out_path.exists():
        _ensure_backup(out_path)

    # Stable output format requirements:
    # 1) First line:  <?xml version='1.0' encoding='utf-8'?>
    # 2) Second line: <SCL xmlns="..." xmlns:xsi="..." xsi:schemaLocation="...">
    # 3) Last line:   </SCL>
    scl_ns = "http://www.iec.ch/61850/2003/SCL"
    xsd_ns = "http://www.w3.org/2001/XMLSchema"
    xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"
    etr_ns = "http://www.iec.ch/61850-90-11/2019/SCL"

    # Register namespaces to prevent ElementTree from inventing ns0/ns1 prefixes.
    # - SCL is default namespace
    # - Others are declared on <SCL ...> and may be used by attributes/elements.
    try:
        ET.register_namespace("", scl_ns)
    except Exception:
        pass
    try:
        ET.register_namespace("xsd", xsd_ns)
    except Exception:
        pass
    try:
        ET.register_namespace("xsi", xsi_ns)
    except Exception:
        pass
    try:
        ET.register_namespace("eTr-IEC61850-90-11", etr_ns)
    except Exception:
        pass

    def _deep_copy(el: ET.Element) -> ET.Element:
        # ElementTree elements are mutable; copy so indent/text/tail changes don't pollute the in-memory doc.
        return ET.fromstring(ET.tostring(el, encoding="utf-8"))

    def _strip_text(s: str | None) -> str:
        return (s or "").strip()

    def _dai_is_meaningful(dai: ET.Element) -> bool:
        # Keep DAI if it contains any non-empty <Val> or non-empty <Private> text.
        # Also keep if it contains any non-standard child element (safety), or any remaining SDI/DAI after pruning.
        try:
            dai_name = (dai.attrib.get("name") or "").strip()
        except Exception:
            dai_name = ""

        # Domain/UI rule: certain DAI must be persisted when present even if empty.
        # Note: we may intentionally drop empty <Val/> placeholders for these on save,
        # resulting in a self-closing <DAI .../>.
        if dai_name in ALWAYS_PERSIST_EMPTY_DAI_NAMES:
            return True
        for ch in list(dai):
            if not isinstance(ch.tag, str):
                continue
            ln = _local_name(ch.tag)
            if ln in {"SDI", "DAI"}:
                return True
            if ln == "Val" and _strip_text(ch.text):
                return True
            if ln == "Private" and _strip_text(ch.text):
                return True
            if ln not in {"Val", "Private"}:
                return True
        return False

    def _prune_empty_containers(parent: ET.Element) -> bool:
        # Returns True if parent has any meaningful content after pruning.
        meaningful = False
        for ch in list(parent):
            if not isinstance(ch.tag, str):
                continue
            ln = _local_name(ch.tag)
            if ln in {"DOI", "SDI"}:
                child_meaningful = _prune_empty_containers(ch)
                if not child_meaningful:
                    parent.remove(ch)
                else:
                    # If container became empty, clear whitespace-only text so it serializes as <... />.
                    if (len(list(ch)) == 0) and not _strip_text(ch.text):
                        ch.text = None
                    meaningful = True
            elif ln == "DAI":
                # Prune nested SDI/DAI first (some configs nest SDI under DAI)
                _prune_empty_containers(ch)

                # Compact-save: for some placeholder DAI, omit empty <Val/> children to reduce noise.
                try:
                    dai_name = (ch.attrib.get("name") or "").strip()
                except Exception:
                    dai_name = ""
                if dai_name in ALWAYS_PERSIST_EMPTY_DAI_NAMES:
                    vals = [
                        x
                        for x in list(ch)
                        if isinstance(x.tag, str) and _local_name(x.tag) == "Val"
                    ]
                    if vals:
                        any_text = any(_strip_text(v.text) for v in vals)
                        any_attrs = any(bool(v.attrib) for v in vals)
                        if (not any_text) and (not any_attrs):
                            for v in vals:
                                try:
                                    ch.remove(v)
                                except Exception:
                                    pass
                            # If we removed the last children, clear leftover whitespace text.
                            if (len(list(ch)) == 0) and not _strip_text(ch.text):
                                ch.text = None

                # Remove empty Schneider LangRef placeholders (avoid writing <Private ... /> when undefined).
                try:
                    if dai_name == "d":
                        for x in list(ch):
                            if not isinstance(x.tag, str) or _local_name(x.tag) != "Private":
                                continue
                            t = (x.attrib.get("type") or "").strip()
                            if t == "SchneiderElectric-PowerLogic-LangRef" and not _strip_text(x.text):
                                ch.remove(x)
                except Exception:
                    pass

                # If DAI ended up with no children, clear whitespace-only text so it serializes as <DAI ... />.
                if (len(list(ch)) == 0) and not _strip_text(ch.text):
                    ch.text = None

                if not _dai_is_meaningful(ch):
                    parent.remove(ch)
                else:
                    meaningful = True
            else:
                # Unknown tags: keep and consider meaningful (avoid accidental data loss)
                meaningful = True

        # If any remaining child element exists, treat as meaningful.
        if not meaningful:
            for x in list(parent):
                if isinstance(x.tag, str):
                    meaningful = True
                    break
        return meaningful

    def _prune_empty_dai_in_doc_root(root_el: ET.Element) -> None:
        # Apply pruning inside each LN/LN0/LNode element.
        for ln_el in list(root_el.iter()):
            if not isinstance(ln_el.tag, str):
                continue
            if _local_name(ln_el.tag) not in {"LN", "LN0", "LNode"}:
                continue
            for doi in list(ln_el):
                if not isinstance(doi.tag, str) or _local_name(doi.tag) != "DOI":
                    continue
                # Never remove DOI: even if it becomes empty, it should be written as <DOI name="X" />.
                _prune_empty_containers(doi)

    def _reorder_ln_attributes_for_stable_output(root_el: ET.Element) -> None:
        # XML attribute order is not semantically important, but some downstream tools
        # and diffs expect a stable order. Requirement: prefix before lnClass.
        for ln_el in list(root_el.iter()):
            if not isinstance(ln_el.tag, str):
                continue
            if _local_name(ln_el.tag) not in {"LN", "LN0", "LNode"}:
                continue

            attrib = {k: str(v) for k, v in (ln_el.attrib or {}).items()}
            ordered: dict[str, str] = {}
            for k in ("prefix", "lnClass", "inst", "lnType", "desc"):
                v = (attrib.get(k) or "").strip()
                if v:
                    ordered[k] = v
            for k in sorted(attrib.keys()):
                if k in ordered:
                    continue
                v = (attrib.get(k) or "").strip()
                if v:
                    ordered[k] = v
            ln_el.attrib.clear()
            ln_el.attrib.update(ordered)

    root = doc.tree.getroot()

    # Wrap content in a temporary SCL root for pretty indenting with inherited default namespace.
    wrap_root = ET.Element(f"{{{scl_ns}}}SCL")
    if _local_name(root.tag) == "SCL":
        for ch in list(root):
            if isinstance(ch.tag, str):
                wrap_root.append(_deep_copy(ch))
    else:
        wrap_root.append(_deep_copy(root))

    # Compact-on-save requirement: do not write empty placeholder DAI nodes.
    # Note: we only prune in this temporary copy, not in the in-memory doc.
    try:
        _prune_empty_dai_in_doc_root(wrap_root)
    except Exception:
        # Never block save; worst case is a larger output.
        pass

    try:
        _reorder_ln_attributes_for_stable_output(wrap_root)
    except Exception:
        pass

    wrap_tree = ET.ElementTree(wrap_root)
    try:
        ET.indent(wrap_tree, space="    ", level=0)  # py311+
    except Exception:
        pass

    wrap_text = ET.tostring(wrap_root, encoding="unicode")
    wrap_lines = wrap_text.splitlines()

    inner_lines: list[str] = []
    if wrap_lines:
        # If self-closing (<SCL ... />) there is no inner content.
        if not (len(wrap_lines) == 1 and wrap_lines[0].rstrip().endswith("/>")):
            if len(wrap_lines) >= 2:
                # Drop wrapper open/close lines.
                inner_lines = wrap_lines[1:-1]

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="utf-8" ?>')
    lines.append(
        '<SCL xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns="http://www.iec.ch/61850/2003/SCL" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.iec.ch/61850/2003/SCL SCL.xsd" '
        'xmlns:eTr-IEC61850-90-11="http://www.iec.ch/61850-90-11/2019/SCL">'
    )
    lines.extend(inner_lines)
    lines.append("</SCL>")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def create_application_file_for_ln_instance(
    doc: LNInstanceDocument,
    *,
    target_path: Path,
    funblock_name: str | None = None,
    funblock_class: str | None = None,
    seq_nb: str = "50",
    ln_ref: str | None = None,
    desc: str | None = None,
    group_name: str = "CAG_1",
) -> Path:
    """Create a minimal Schneider ExecutionScheme application file for a given LN instance.

    This is intentionally a small skeleton (group + funBlock only). Later steps can add
    input/output/setting wiring from InRef and settings.
    """

    target_path = Path(target_path)
    if funblock_name is None:
        funblock_name = target_path.stem
    if funblock_class is None:
        funblock_class = funblock_name

    ln_el = doc.ln_elements[0] if doc.ln_elements else None
    ln_class = ((ln_el.attrib.get("lnClass") if ln_el is not None else "") or "").strip()
    prefix = ((ln_el.attrib.get("prefix") if ln_el is not None else "") or "").strip()
    ln_desc = ((ln_el.attrib.get("desc") if ln_el is not None else "") or "").strip()
    auto_ln_ref = f"{prefix}{ln_class}#" if (prefix or ln_class) else "#"
    effective_ln_ref = (ln_ref or "").strip() or auto_ln_ref
    effective_desc = (desc or "").strip() if (desc is not None) else ln_desc

    exe_ns = "http://www.schneider-electric.com/PowerLogic/ExecutionScheme"
    xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"
    schema_loc = "http://www.schneider-electric.com/PowerLogic/ExecutionScheme SE_PowerLogic_ExecutionScheme.xsd"

    try:
        ET.register_namespace("", exe_ns)
    except Exception:
        pass
    try:
        ET.register_namespace("xsi", xsi_ns)
    except Exception:
        pass

    root = ET.Element(f"{{{exe_ns}}}EasergyPExecutionScheme")
    root.attrib[f"{{{xsi_ns}}}schemaLocation"] = schema_loc

    group = ET.SubElement(root, f"{{{exe_ns}}}group")
    group.attrib["name"] = (group_name or "CAG_1").strip() or "CAG_1"

    fb = ET.SubElement(group, f"{{{exe_ns}}}funBlock")
    fb.attrib["name"] = (funblock_name or "").strip()
    fb.attrib["class"] = (funblock_class or "").strip()
    fb.attrib["seqNb"] = (seq_nb or "").strip() or "50"
    fb.attrib["LnRef"] = effective_ln_ref
    fb.attrib["desc"] = effective_desc

    return save_execution_scheme_root(root, target_path=target_path)


def save_execution_scheme_root(root: ET.Element, *, target_path: Path) -> Path:
    """Write an ExecutionScheme (application) XML with a stable root open tag line.

    Requirement: root opening tag must be serialized as:
    <EasergyPExecutionScheme xmlns:xsi="..." xsi:schemaLocation="..." xmlns="...">
    """

    target_path = Path(target_path)

    exe_ns = "http://www.schneider-electric.com/PowerLogic/ExecutionScheme"
    xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"
    schema_loc = "http://www.schneider-electric.com/PowerLogic/ExecutionScheme SE_PowerLogic_ExecutionScheme.xsd"

    try:
        ET.register_namespace("", exe_ns)
    except Exception:
        pass
    try:
        ET.register_namespace("xsi", xsi_ns)
    except Exception:
        pass

    # Ensure schemaLocation exists (common requirement for these files).
    root.attrib[f"{{{xsi_ns}}}schemaLocation"] = schema_loc

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="    ", level=0)  # py311+
    except Exception:
        pass

    xml = ET.tostring(root, encoding="unicode")

    stable_open = (
        f'<EasergyPExecutionScheme xmlns:xsi="{xsi_ns}" '
        f'xsi:schemaLocation="{schema_loc}" '
        f'xmlns="{exe_ns}">' 
    )

    lines = [ln for ln in xml.splitlines() if ln is not None]
    if lines:
        for i, ln in enumerate(lines):
            s = ln.lstrip()
            if s.startswith("<EasergyPExecutionScheme") or s.startswith(f"<{{{exe_ns}}}EasergyPExecutionScheme"):
                indent = ln[: len(ln) - len(s)]
                lines[i] = indent + stable_open
                break

    text = "<?xml version=\"1.0\" encoding=\"utf-8\" ?>\n" + "\n".join(lines) + "\n"
    target_path.write_text(text, encoding="utf-8")
    return target_path


def compute_signature(doc: LNInstanceDocument) -> str:
    # Deterministic signature for dirty tracking: for each LN, collect sorted attrib + values.
    parts: list[str] = []
    for idx, ln in enumerate(doc.ln_elements):
        attrib_items = sorted((k, str(v)) for k, v in ln.attrib.items())
        parts.append(f"LN[{idx}]:{_local_name(ln.tag)}")
        for k, v in attrib_items:
            parts.append(f"A:{k}={v}")
        try:
            refs = extract_value_refs(doc, idx)
        except Exception:
            refs = []
        for r in refs:
            parts.append(f"V:{r.path}={r.get_value_text()}")

        try:
            lrefs = extract_langref_refs(doc, idx)
        except Exception:
            lrefs = []
        for lr in lrefs:
            g, l = lr.get_group_label()
            parts.append(f"L:{lr.path}={g}.{l}")
        parts.append("--")
    return "\n".join(parts)


def create_ln_instance_from_template(
    *,
    iec61850_dir: Path,
    template: LNodeTypeModel | Path,
    target_path: Path,
    prefix: str = "",
    inst: str = "0",
    ln_desc: str | None = None,
    include_type_langref_ids: bool = False,
    copy_d_val_from_type: bool = False,
    create_empty_val_for_edit: bool = True,
) -> LNInstanceDocument:
    """Create a brand-new LN instance XML file skeleton from an LNodeType template.

    The skeleton is generated by expanding the template's DO list via DOType/DAType definitions.
    We always emit DOI/SDI/DAI with at least one <Val/> for writable attributes so the UI can edit.
    """

    iec61850_dir = Path(iec61850_dir)
    do_type_dir = iec61850_dir / "DOType"
    da_type_dir = iec61850_dir / "DAType"

    if isinstance(template, Path):
        # Best-effort: parse as LNodeType file via existing loader expectations.
        # We need an LNodeTypeInfo; load_lnode_type takes LNodeTypeInfo, but we only have a path.
        # Create a minimal info by reading the LNodeType element.
        tree = ET.parse(template)
        root = tree.getroot()

        def _find_lnode(el0: ET.Element) -> ET.Element | None:
            for el in el0.iter():
                if isinstance(el.tag, str) and _local_name(el.tag) == "LNodeType":
                    return el
            return None

        ln_el = _find_lnode(root)
        if ln_el is None:
            raise ValueError(f"No <LNodeType> found in: {template}")
        ln_id = (ln_el.attrib.get("id") or "").strip() or template.stem
        ln_class = (ln_el.attrib.get("lnClass") or "").strip()
        desc = (ln_el.attrib.get("desc") or "").strip()

        from iec61850_scanner import LNodeTypeInfo

        info = LNodeTypeInfo(id=ln_id, ln_class=ln_class, desc=desc, file_path=Path(template))
        template_model = load_lnode_type(info)
    else:
        template_model = template

    ns = SCL_NS

    def q(tag: str) -> str:
        return f"{{{ns}}}{tag}"

    def _is_setting_name(name: str) -> bool:
        return (name or "").strip() in {"setVal", "setMag"}

    def _fc_is_grouped(fc: str) -> bool:
        # Domain rule:
        # - Determine grouped-vs-single by FC of SetVal/SetMag in the DO template.
        # - If FC == SP => not grouped
        # - Else => grouped (SE, etc.)
        return (fc or "").strip().upper() != "SP"

    def _apply_val_meta(
        dst_dai: ET.Element,
        *,
        src_val_kind: str,
        src_val_import: str,
        effective_fc: str,
    ) -> None:
        """Apply valKind/valImport to a DAI per rules.

        Rules:
        1) Template overrides per attribute: if template defines valKind and/or valImport, keep those.
        2) If either attribute is missing, fill it from FC:
           - FC in {SE, SP}: valKind="Set" valImport="true"
           - Else: valKind="RO" valImport="false"
        """
        vk = (src_val_kind or "").strip()
        vi = (src_val_import or "").strip()
        fc0 = (effective_fc or "").strip().upper()

        if fc0 in {"SE", "SP"}:
            default_vk = "Set"
            default_vi = "true"
        else:
            default_vk = "RO"
            default_vi = "false"

        dst_dai.attrib["valKind"] = vk or default_vk
        dst_dai.attrib["valImport"] = vi or default_vi

    def _create_empty_val(dst_parent: ET.Element, *, grouped: bool) -> None:
        if not grouped:
            ET.SubElement(dst_parent, q("Val"))
            return
        # Default setting groups count used in existing instances.
        for i in range(1, 9):
            ve = ET.SubElement(dst_parent, q("Val"))
            ve.attrib["sGroup"] = str(i)

    do_type_cache: dict[str, ET.Element] = {}
    da_type_cache: dict[str, ET.Element] = {}
    do_type_path_index: dict[str, Path] | None = None
    da_type_path_index: dict[str, Path] | None = None

    def _build_type_path_index(type_dir: Path, *, type_tag: str) -> dict[str, Path]:
        idx: dict[str, Path] = {}
        if not type_dir.exists():
            return idx
        for cand in type_dir.glob("*.xml"):
            try:
                t = ET.parse(cand)
            except Exception:
                continue
            r = t.getroot()
            for el in r.iter():
                if not isinstance(el.tag, str):
                    continue
                if _local_name(el.tag) != type_tag:
                    continue
                id_ = (el.attrib.get("id") or "").strip()
                if id_ and id_ not in idx:
                    idx[id_] = cand
        return idx

    def _load_do_type(do_type_id: str) -> ET.Element:
        if do_type_id in do_type_cache:
            return do_type_cache[do_type_id]

        p = do_type_dir / f"{do_type_id}.xml"
        if not p.is_file():
            nonlocal do_type_path_index
            if do_type_path_index is None:
                do_type_path_index = _build_type_path_index(do_type_dir, type_tag="DOType")
            p = do_type_path_index.get(do_type_id)
            if p is None:
                raise FileNotFoundError(f"DOType not found: {do_type_id}")
        t = ET.parse(p)
        r = t.getroot()
        el = r.find(f".//{{{ns}}}DOType")
        if el is None:
            # Some files may have no namespace prefix in ElementTree if missing xmlns; fall back.
            for x in r.iter():
                if isinstance(x.tag, str) and _local_name(x.tag) == "DOType":
                    el = x
                    break
        if el is None or (el.attrib.get("id") or "").strip() != do_type_id:
            # Search for matching id.
            for x in r.iter():
                if isinstance(x.tag, str) and _local_name(x.tag) == "DOType" and (x.attrib.get("id") or "").strip() == do_type_id:
                    el = x
                    break
        if el is None:
            raise ValueError(f"No <DOType id='{do_type_id}'> in: {p}")
        do_type_cache[do_type_id] = el
        return el

    def _load_da_type(da_type_id: str) -> ET.Element:
        if da_type_id in da_type_cache:
            return da_type_cache[da_type_id]

        p = da_type_dir / f"{da_type_id}.xml"
        if not p.is_file():
            nonlocal da_type_path_index
            if da_type_path_index is None:
                da_type_path_index = _build_type_path_index(da_type_dir, type_tag="DAType")
            p = da_type_path_index.get(da_type_id)
            if p is None:
                raise FileNotFoundError(f"DAType not found: {da_type_id}")
        t = ET.parse(p)
        r = t.getroot()
        el = r.find(f".//{{{ns}}}DAType")
        if el is None:
            for x in r.iter():
                if isinstance(x.tag, str) and _local_name(x.tag) == "DAType":
                    el = x
                    break
        if el is None or (el.attrib.get("id") or "").strip() != da_type_id:
            for x in r.iter():
                if isinstance(x.tag, str) and _local_name(x.tag) == "DAType" and (x.attrib.get("id") or "").strip() == da_type_id:
                    el = x
                    break
        if el is None:
            raise ValueError(f"No <DAType id='{da_type_id}'> in: {p}")
        da_type_cache[da_type_id] = el
        return el

    def _copy_private_and_vals(src: ET.Element, dst_parent: ET.Element, *, create_empty_val: bool, grouped: bool) -> None:
        # Copy <Private> and <Val> children from type definitions into instance.
        # Vendor convention: if LangRef IDs are defined in the type definition, we don't repeat them in instances.
        # (But for UI 'template defaults' display we do want to include them.)
        any_val = False
        copied_vals: list[ET.Element] = []
        for ch in list(src):
            if not isinstance(ch.tag, str):
                continue
            ln = _local_name(ch.tag)
            if ln == "Private":
                p_type = (ch.attrib.get("type") or "").strip()
                if (
                    (not include_type_langref_ids)
                    and p_type == "SchneiderElectric-PowerLogic-LangRef"
                    and (ch.text or "").strip()
                ):
                    continue
                pe = ET.SubElement(dst_parent, q("Private"))
                for k, v in ch.attrib.items():
                    pe.attrib[k] = str(v)
                pe.text = (ch.text or "").strip() if (ch.text or "").strip() else None
            elif ln == "Val":
                ve = ET.SubElement(dst_parent, q("Val"))
                for k, v in ch.attrib.items():
                    ve.attrib[k] = str(v)
                if ch.text is not None:
                    ve.text = ch.text
                any_val = True
                copied_vals.append(ve)

        if grouped and copied_vals:
            # Normalize grouped settings to 8 <Val sGroup="1..8"> entries.
            # - If the type had a single ungrouped <Val>, treat it as group 1.
            # - If the type defines a single default value, replicate it to all 8 groups.
            sgs = [(v.attrib.get("sGroup") or "").strip() for v in copied_vals]
            has_any_sg = any(bool(s) for s in sgs)

            default_text: str | None = None
            if len(copied_vals) == 1:
                t0 = (copied_vals[0].text or "").strip()
                if t0:
                    default_text = copied_vals[0].text

            if not has_any_sg:
                # Assign sequential sGroups.
                for i, v in enumerate(copied_vals, start=1):
                    v.attrib["sGroup"] = str(i)

            existing = {str((v.attrib.get("sGroup") or "").strip()): v for v in copied_vals if (v.attrib.get("sGroup") or "").strip()}
            # Fill missing groups with empty placeholders.
            for i in range(1, 9):
                sg = str(i)
                if sg in existing:
                    continue
                ve3 = ET.SubElement(dst_parent, q("Val"))
                ve3.attrib["sGroup"] = sg
                if default_text is not None:
                    ve3.text = default_text

            # If the type provided grouped values but only one group has a non-empty default,
            # replicate that default into the other empty groups.
            try:
                vals_now = [x for x in list(dst_parent) if isinstance(x.tag, str) and _local_name(x.tag) == "Val"]
                non_empty = []
                for v in vals_now:
                    txt = (v.text or "")
                    if txt.strip():
                        non_empty.append(txt)
                uniq = {t.strip() for t in non_empty if t.strip()}
                if len(uniq) == 1:
                    dtext = next(iter(uniq))
                    for v in vals_now:
                        if not (v.text or "").strip():
                            v.text = dtext
            except Exception:
                pass

            # Reorder Val children by sGroup for stability.
            try:
                children = list(dst_parent)
                non_val = [c for c in children if not (isinstance(c.tag, str) and _local_name(c.tag) == "Val")]
                vals2 = [c for c in children if isinstance(c.tag, str) and _local_name(c.tag) == "Val"]

                def _key(v: ET.Element) -> tuple[int, str]:
                    sg = (v.attrib.get("sGroup") or "").strip()
                    try:
                        return (int(sg), sg)
                    except Exception:
                        return (10**9, sg)

                vals2.sort(key=_key)
                dst_parent[:] = []
                for c in non_val:
                    dst_parent.append(c)
                for c in vals2:
                    dst_parent.append(c)
            except Exception:
                pass

        if create_empty_val and not any_val:
            _create_empty_val(dst_parent, grouped=grouped)

    def _expand_da_type(
        da_type_id: str,
        container: ET.Element,
        visited: set[str],
        *,
        setting_grouped: bool,
        fc_ctx: str,
        parent_name: str | None = None,
    ) -> None:
        # Treat visited as a recursion stack (cycle detection), not a global cache.
        # The same DAType may need to be expanded multiple times under different parents.
        if da_type_id in visited:
            return
        visited.add(da_type_id)

        try:
            da_type_el = _load_da_type(da_type_id)
            for bda in list(da_type_el):
                if not isinstance(bda.tag, str):
                    continue
                if _local_name(bda.tag) != "BDA":
                    continue

                name = (bda.attrib.get("name") or "").strip()
                if not name:
                    continue
                # LNDM instance rule: do not generate dataNs.
                if name == "dataNs":
                    continue

                fc = (bda.attrib.get("fc") or "").strip().upper()
                eff_fc = fc or (fc_ctx or "").strip().upper()
                if fc in {"ST", "MX"}:
                    continue

                btype = (bda.attrib.get("bType") or "").strip()
                val_kind = (bda.attrib.get("valKind") or "").strip()
                val_import = (bda.attrib.get("valImport") or "").strip()

                next_setting_grouped = setting_grouped
                if _is_setting_name(name):
                    next_setting_grouped = _fc_is_grouped(eff_fc)

                if btype.lower() == "struct":
                    sdi = ET.SubElement(container, q("SDI"))
                    sdi.attrib["name"] = name
                    t_id = (bda.attrib.get("type") or "").strip()
                    if t_id:
                        _expand_da_type(
                            t_id,
                            sdi,
                            visited,
                            setting_grouped=next_setting_grouped,
                            fc_ctx=eff_fc,
                            parent_name=name,
                        )
                    continue

                dai = ET.SubElement(container, q("DAI"))
                dai.attrib["name"] = name

                _apply_val_meta(dai, src_val_kind=val_kind, src_val_import=val_import, effective_fc=eff_fc)

                grouped = next_setting_grouped
                vk = (dai.attrib.get("valKind") or "").strip().upper()
                # For leaves under setVal/setMag, valKind is often omitted; still create editable placeholders.
                always_show = name in (ALWAYS_PERSIST_EMPTY_DAI_NAMES | {"minVal", "maxVal", "stepSize"})
                force_struct_leaf_val = (parent_name or "") in {"minVal", "maxVal", "stepSize"}
                create_empty_val = always_show or force_struct_leaf_val or (
                    (vk != "RO") and (next_setting_grouped or _is_setting_name(name) or (vk != ""))
                )
                _copy_private_and_vals(
                    bda,
                    dai,
                    create_empty_val=(create_empty_val_for_edit and create_empty_val),
                    grouped=grouped,
                )
        finally:
            visited.discard(da_type_id)

    def _expand_do_type(do_type_id: str, container: ET.Element, visited_do: set[str], visited_da: set[str]) -> None:
        # Treat visited_do/visited_da as recursion stacks (cycle detection), not caches.
        # Multiple sibling SDOs (e.g., phsA/phsB/phsC) may reference the same DOType and
        # must each be expanded fully.
        if do_type_id in visited_do:
            return
        visited_do.add(do_type_id)

        try:
            do_type_el = _load_do_type(do_type_id)
            # Children: <DA>, <SDO>
            for ch in list(do_type_el):
                if not isinstance(ch.tag, str):
                    continue
                ln = _local_name(ch.tag)
                if ln == "DA":
                    name = (ch.attrib.get("name") or "").strip()
                    if not name:
                        continue
                    # LNDM instance rule: do not generate dataNs.
                    if name == "dataNs":
                        continue

                    fc = (ch.attrib.get("fc") or "").strip().upper()
                    if fc in {"ST", "MX"}:
                        continue

                    setting_grouped = _is_setting_name(name) and _fc_is_grouped(fc)

                    btype = (ch.attrib.get("bType") or "").strip()
                    val_kind = (ch.attrib.get("valKind") or "").strip()
                    val_import = (ch.attrib.get("valImport") or "").strip()

                    # Special-case Schneider: English label stored at DAI name='d'
                    if name == "d" and btype.lower() != "struct":
                        dai = ET.SubElement(container, q("DAI"))
                        dai.attrib["name"] = "d"

                        _apply_val_meta(dai, src_val_kind=val_kind, src_val_import=val_import, effective_fc=fc)

                        if copy_d_val_from_type:
                            # For template-default display: copy any <Private>/<Val> from type defs.
                            _copy_private_and_vals(ch, dai, create_empty_val=False, grouped=False)
                        else:
                            # Instance generation: do not duplicate type-defined LangRef IDs; emit editable placeholder if missing.
                            has_langref_id = False
                            for t in list(ch):
                                if isinstance(t.tag, str) and _local_name(t.tag) == "Private":
                                    if (t.attrib.get("type") or "").strip() == "SchneiderElectric-PowerLogic-LangRef" and (
                                        (t.text or "").strip()
                                    ):
                                        has_langref_id = True
                                        break

                            # If no LangRef ID is defined, do not create an empty <Private .../> placeholder.
                            # The Language tab can create it on first edit (non-destructive).

                            if create_empty_val_for_edit:
                                ET.SubElement(dai, q("Val"))
                        continue

                    if btype.lower() == "struct":
                        sdi = ET.SubElement(container, q("SDI"))
                        sdi.attrib["name"] = name
                        t_id = (ch.attrib.get("type") or "").strip()
                        if t_id:
                            _expand_da_type(
                                t_id,
                                sdi,
                                visited_da,
                                setting_grouped=setting_grouped,
                                fc_ctx=fc,
                                parent_name=name,
                            )
                        continue

                    dai = ET.SubElement(container, q("DAI"))
                    dai.attrib["name"] = name

                    _apply_val_meta(dai, src_val_kind=val_kind, src_val_import=val_import, effective_fc=fc)

                    # If type definition provides <Val>, copy it; otherwise create empty <Val/> for non-RO.
                    grouped = setting_grouped
                    vk = (dai.attrib.get("valKind") or "").strip().upper()
                    always_show = name in (ALWAYS_PERSIST_EMPTY_DAI_NAMES | {"minVal", "maxVal", "stepSize"})
                    create_empty_val = always_show or (
                        (vk != "RO") and (setting_grouped or _is_setting_name(name) or (vk != ""))
                    )
                    _copy_private_and_vals(
                        ch,
                        dai,
                        create_empty_val=(create_empty_val_for_edit and create_empty_val),
                        grouped=grouped,
                    )
                elif ln == "SDO":
                    name = (ch.attrib.get("name") or "").strip()
                    t_id = (ch.attrib.get("type") or "").strip()
                    if not name or not t_id:
                        continue
                    sdi = ET.SubElement(container, q("SDI"))
                    sdi.attrib["name"] = name
                    _expand_do_type(t_id, sdi, visited_do, visited_da)
        finally:
            visited_do.discard(do_type_id)

    # Root + LN
    scl = ET.Element(q("SCL"))
    ln = ET.SubElement(scl, q("LN"))

    ln_id = template_model.info.id
    ln_class = template_model.info.ln_class
    desc0 = (ln_desc or "").strip() or (template_model.info.desc or "").strip()
    ln.attrib["lnClass"] = ln_class
    ln.attrib["inst"] = (inst or "0").strip() or "0"
    if (prefix or "").strip():
        ln.attrib["prefix"] = (prefix or "").strip()
    ln.attrib["lnType"] = ln_id
    if desc0:
        ln.attrib["desc"] = desc0

    visited_do: set[str] = set()
    visited_da: set[str] = set()

    for do in list(template_model.dos or []):
        doi = ET.SubElement(ln, q("DOI"))
        doi.attrib["name"] = do.name
        if do.do_type:
            _expand_do_type(do.do_type, doi, visited_do=set(), visited_da=set())

        # Domain rule: all InRef DOI are used to define LN inputs.
        # Ensure these placeholders always exist in generated LNDM instances.
        # - purpose: visible/editable in UI; should be written even if empty
        # - setSrcRef: visible/editable in UI; should be written even if empty
        try:
            doi_name = (do.name or "").strip()
        except Exception:
            doi_name = ""

        if doi_name.startswith("InRef"):
            def _ensure_dai(doi_el: ET.Element, dai_name: str, *, default_vk: str, default_vi: str) -> None:
                ns0 = _ns_uri(doi_el.tag)
                tag_dai = f"{{{ns0}}}DAI" if ns0 else "DAI"
                tag_val = f"{{{ns0}}}Val" if ns0 else "Val"

                dai_el: ET.Element | None = None
                for ch in list(doi_el):
                    if not isinstance(ch.tag, str) or _local_name(ch.tag) != "DAI":
                        continue
                    if (ch.attrib.get("name") or "").strip() != dai_name:
                        continue
                    dai_el = ch
                    break
                if dai_el is None:
                    dai_el = ET.SubElement(doi_el, tag_dai)
                    dai_el.attrib["name"] = dai_name

                if not (dai_el.attrib.get("valKind") or "").strip():
                    dai_el.attrib["valKind"] = default_vk
                if not (dai_el.attrib.get("valImport") or "").strip():
                    dai_el.attrib["valImport"] = default_vi

                has_val = False
                for x in list(dai_el):
                    if isinstance(x.tag, str) and _local_name(x.tag) == "Val":
                        has_val = True
                        break
                if not has_val:
                    dai_el.append(ET.Element(tag_val))

            _ensure_dai(doi, "setSrcRef", default_vk="Set", default_vi="true")
            _ensure_dai(doi, "purpose", default_vk="RO", default_vi="false")

    tree = ET.ElementTree(scl)
    doc = LNInstanceDocument(file_path=Path(target_path), tree=tree, ns=ns, ln_elements=[ln])
    return doc


def ensure_all_dai_present_from_template(
    doc: LNInstanceDocument,
    ln_index: int,
    *,
    iec61850_dir: Path,
    template: LNodeTypeModel,
) -> None:
    """Augment an existing LN instance document with missing DOI/SDI/DAI placeholders.

    This is intended for UI display only: we want the UI to show the full template-defined
    DAI set even if the on-disk file only stores DAI that have values.

    The merge is additive and non-destructive:
    - existing elements are kept as-is
    - missing DOI/SDI/DAI from the template skeleton are deep-copied in
    - if an existing DAI has no <Val>, a placeholder <Val/> is added (so it can be edited)
    """

    if ln_index < 0 or ln_index >= len(doc.ln_elements):
        raise IndexError("ln_index out of range")

    ln = doc.ln_elements[ln_index]
    prefix = (ln.attrib.get("prefix") or "").strip()
    inst = (ln.attrib.get("inst") or "0").strip() or "0"
    desc = (ln.attrib.get("desc") or "").strip()

    sk = create_ln_instance_from_template(
        iec61850_dir=Path(iec61850_dir),
        template=template,
        target_path=doc.file_path,
        prefix=prefix,
        inst=inst,
        ln_desc=desc,
    )
    sk_ln = sk.ln_elements[0]

    def _deep_copy(el: ET.Element) -> ET.Element:
        return ET.fromstring(ET.tostring(el, encoding="utf-8"))

    def _child_key(el: ET.Element) -> tuple[str, str]:
        return (_local_name(el.tag), (el.attrib.get("name") or "").strip())

    def _find_child(parent: ET.Element, tag_local: str, name: str) -> ET.Element | None:
        for ch in list(parent):
            if not isinstance(ch.tag, str):
                continue
            if _local_name(ch.tag) != tag_local:
                continue
            if (ch.attrib.get("name") or "").strip() != name:
                continue
            return ch
        return None

    def _ensure_dai_has_val(dst_dai: ET.Element, src_dai: ET.Element) -> None:
        def _vals(parent: ET.Element) -> list[ET.Element]:
            return [x for x in list(parent) if isinstance(x.tag, str) and _local_name(x.tag) == "Val"]

        src_vals = _vals(src_dai)
        dst_vals = _vals(dst_dai)

        # If the template skeleton has no Val, keep dst as-is.
        if not src_vals:
            if not dst_vals:
                ns = _ns_uri(dst_dai.tag)
                tag = f"{{{ns}}}Val" if ns else "Val"
                dst_dai.append(ET.Element(tag))
            return

        # If template expects grouped values, ensure we have the same sGroup set.
        src_sgs = [(v.attrib.get("sGroup") or "").strip() for v in src_vals]
        expects_grouped = any(src_sgs) or (len(src_vals) > 1)

        if not expects_grouped:
            if not dst_vals:
                for v in src_vals:
                    dst_dai.append(_deep_copy(v))
            return

        # Grouped expected.
        # Best-effort conversion: if dst has a single non-sGroup Val, treat it as sGroup=first.
        if len(dst_vals) == 1 and not (dst_vals[0].attrib.get("sGroup") or "").strip():
            first_sg = (src_sgs[0] or "1")
            dst_vals[0].attrib["sGroup"] = first_sg

        # Ensure all template sGroups exist.
        existing_by_sg: dict[str, ET.Element] = {}
        ungrouped: list[ET.Element] = []
        for v in dst_vals:
            sg = (v.attrib.get("sGroup") or "").strip()
            if sg:
                existing_by_sg[sg] = v
            else:
                ungrouped.append(v)

        # If we have multiple ungrouped vals but no sGroup, assign sequentially.
        if ungrouped and not existing_by_sg:
            for i, v in enumerate(ungrouped):
                sg = (src_sgs[i] if i < len(src_sgs) else str(i + 1)) or str(i + 1)
                v.attrib["sGroup"] = sg
                existing_by_sg[sg] = v

        # Create missing sGroup vals.
        ns = _ns_uri(dst_dai.tag)
        tag_val = f"{{{ns}}}Val" if ns else "Val"
        for sg in src_sgs:
            sg0 = (sg or "").strip() or "1"
            if sg0 in existing_by_sg:
                continue
            ve = ET.Element(tag_val)
            ve.attrib["sGroup"] = sg0
            dst_dai.append(ve)

        # Reorder Val children to match template order; keep non-Val children in place.
        try:
            children = list(dst_dai)
            non_val = [c for c in children if not (isinstance(c.tag, str) and _local_name(c.tag) == "Val")]
            vals2 = [c for c in children if isinstance(c.tag, str) and _local_name(c.tag) == "Val"]

            def _key(v: ET.Element) -> tuple[int, str]:
                sg = (v.attrib.get("sGroup") or "").strip()
                try:
                    return (int(sg), sg)
                except Exception:
                    return (10**9, sg)

            vals2.sort(key=_key)
            dst_dai[:] = []
            for c in non_val:
                dst_dai.append(c)
            for c in vals2:
                dst_dai.append(c)
        except Exception:
            pass

    def _merge(dst_parent: ET.Element, src_parent: ET.Element) -> None:
        # Only merge known containers; keep dst as source-of-truth.
        for src in list(src_parent):
            if not isinstance(src.tag, str):
                continue
            t = _local_name(src.tag)
            if t not in {"DOI", "SDI", "DAI"}:
                continue
            name = (src.attrib.get("name") or "").strip()
            if not name:
                continue

            dst = _find_child(dst_parent, t, name)
            if dst is None:
                dst_parent.append(_deep_copy(src))
                continue

            # Recurse first so nested placeholders are created.
            _merge(dst, src)

            if t == "DAI":
                _ensure_dai_has_val(dst, src)

                # Preserve template DAI metadata attributes.
                # Some instances omit these; UI generation should keep them consistent.
                for k in ("valKind", "valImport"):
                    sv = (src.attrib.get(k) or "").strip()
                    if not sv:
                        continue
                    dv = (dst.attrib.get(k) or "").strip()
                    if not dv:
                        dst.attrib[k] = sv

    # Merge DOI tree from skeleton into loaded LN.
    _merge(ln, sk_ln)
