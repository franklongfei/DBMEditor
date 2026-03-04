from __future__ import annotations

from pathlib import Path

try:
    from ln_instance_editor_ui import LNInstanceEditorFrame
except ModuleNotFoundError as e:
    if getattr(e, "name", None) not in {"ln_instance_editor_ui"}:
        raise
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ln_instance_editor_ui import LNInstanceEditorFrame


__all__ = ["LNInstanceEditorFrame"]
