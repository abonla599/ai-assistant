# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_backend.py'],
    # 入口以顶层包名 app 导入（见 run_backend.py），分析阶段需要能找到 backend/app
    pathex=['backend'],
    binaries=[],
    # PWA 静态资源不是 .py，PyInstaller 不会自动收集；目标路径须与
    # app/web/web_router.py 中 frozen 分支拼接的 app/web/static 保持一致。
    datas=[('backend/app/web/static', 'app/web/static')],
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        'chromadb',
        # FastAPI 在函数内条件导入，静态分析抓不到；缺失会让附件上传直接崩
        'multipart',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 本应用嵌入走向云端（apiyi），不加载任何本地模型；以下均是被可选依赖链
        # 拖进来的死重，合计约 600MB。onnxruntime 保留：chromadb 默认 embedding
        # function 可能引用它。
        'torch', 'torchaudio', 'torchvision',
        'transformers', 'sentence_transformers', 'huggingface_hub',
        'cv2', 'imageio_ffmpeg', 'moviepy',
        'scipy', 'sklearn', 'pandas',
    ],
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
