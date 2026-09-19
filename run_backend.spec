# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# chromadb 用配置项按字符串路径动态导入 chromadb.api.rust，原生实现又在独立包
# chromadb_rust_bindings 里，静态分析两者都发现不了，必须显式整体收集。
chroma_datas, chroma_binaries, chroma_hiddenimports = collect_all("chromadb")
binding_datas, binding_binaries, binding_hiddenimports = collect_all("chromadb_rust_bindings")
# PDF 解析用 PyMuPDF（导入名 fitz）：带原生二进制，且只在上传时函数内延迟导入，
# 静态分析抓不到，漏收会让冻结版一上传 PDF 就 ImportError。
fitz_datas, fitz_binaries, fitz_hiddenimports = collect_all("fitz")


a = Analysis(
    ['run_backend.py'],
    # 入口以顶层包名 app 导入（见 run_backend.py），分析阶段需要能找到 backend/app
    pathex=['backend'],
    binaries=chroma_binaries + binding_binaries + fitz_binaries,
    # 三个前端目录都要显式列出：漏一个的后果是"源码版全对、EXE 版 404"，
    # 而这条不会让任何测试变红（上次 static 缺失导致 EXE 启动即崩就是同一形状）。
    # site 是官网：漏了它，冻结版的 GET / 会因为找不到 index.html 而 500。
    datas=[('backend/app/web/static', 'app/web/static'),
           ('backend/app/web/admin', 'app/web/admin'),
           ('backend/app/web/site', 'app/web/site')] + chroma_datas + binding_datas + fitz_datas,
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        # FastAPI 在函数内条件导入，静态分析抓不到；缺失会让附件上传直接崩
        'multipart',
    ] + chroma_hiddenimports + binding_hiddenimports + fitz_hiddenimports,
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
