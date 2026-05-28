# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ["darkcodex/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "requests",
        "json",
        "base64",
        "hashlib",
        "sqlite3",
        "urllib.request",
        "urllib.parse",
        "darkcodex.agent_fichiers",
        "darkcodex.langues",
        "darkcodex.licence",
        "darkcodex.memory",
        "darkcodex.security",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "test",
        "html",
        "http.server",
        "xmlrpc",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
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
    name="darkcodex",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
