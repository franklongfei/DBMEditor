# -*- mode: python ; coding: utf-8 -*-
import os
import sys

try:
    project_root = os.path.abspath(os.path.dirname(__file__))
except NameError:
    project_root = os.getcwd()

entry_script = os.path.join(project_root, 'main.py')

icon_candidates = [
    os.path.join(project_root, 'assets', 'app.ico'),
    os.path.join(project_root, 'app.ico'),
]
icon_path = next((p for p in icon_candidates if os.path.isfile(p)), None)

py_major = sys.version_info.major
py_minor = sys.version_info.minor
python_dll_name = f"python{py_major}{py_minor}.dll"
possible_dll_paths = [
    os.path.join(sys.base_prefix, python_dll_name),
    os.path.join(sys.prefix, python_dll_name),
    os.path.join(project_root, '.venv', python_dll_name),
]
extra_binaries = []
for p in possible_dll_paths:
    if os.path.isfile(p):
        extra_binaries.append((p, '.'))
        break

block_cipher = None

a = Analysis(
    [entry_script],
    pathex=[project_root],
    binaries=extra_binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DBMEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_path,
)
