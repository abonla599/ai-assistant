# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_backend.py'],
    pathex=[],
    binaries=[],
    # PWA 静态资源不是 .py，PyInstaller 不会自动收集；目标路径须与
    # app/web/web_router.py 中 frozen 分支拼接的 app/web/static 保持一致。
    datas=[('backend/app/web/static', 'app/web/static')],
    hiddenimports=['uvicorn.logging', 'uvicorn.loops', 'uvicorn.protocols', 'chromadb'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='run_backend',
)
