from __future__ import annotations

import os
from pathlib import Path
import xml.etree.ElementTree as ET


SCL_NS = "http://www.iec.ch/61850/2003/SCL"


def local_name(tag: str) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def qname(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def deepcopy_et_element(el: ET.Element) -> ET.Element:
    # ElementTree has no built-in deepcopy; round-trip via string is good enough here.
    return ET.fromstring(ET.tostring(el, encoding="unicode"))


def scan_xml_relpaths(base_dir: Path, *, limit: int = 8000) -> list[str]:
    base_dir = Path(base_dir)
    if not base_dir.exists():
        return []

    rels: list[str] = []
    try:
        for p in base_dir.rglob("*.xml"):
            try:
                rels.append(os.fspath(p.relative_to(base_dir)))
            except Exception:
                rels.append(os.fspath(p))
            if len(rels) >= limit:
                break
    except Exception:
        rels = [os.fspath(p.name) for p in base_dir.glob("*.xml")]

    rels.sort(key=lambda s: s.lower())
    return rels


def find_type_file(*, kind_dir: Path, type_id: str, cache: dict[tuple[str, str], Path | None] | None = None) -> Path | None:
    type_id = (type_id or "").strip()
    if not type_id:
        return None

    if cache is None:
        cache = {}

    key = (os.fspath(kind_dir), type_id)
    if key in cache:
        return cache[key]

    direct = Path(kind_dir) / f"{type_id}.xml"
    if direct.is_file():
        cache[key] = direct
        return direct

    found: Path | None = None
    try:
        for p in Path(kind_dir).rglob(f"{type_id}.xml"):
            if p.is_file():
                found = p
                break
    except Exception:
        found = None

    cache[key] = found
    return found
