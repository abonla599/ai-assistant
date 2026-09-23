# -*- mode: python ; coding: utf-8 -*-
import os

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
    # 最后那一项是构建戳：打包前由 `git describe --tags --abbrev=0 > version.txt` 生成。
    # 它不进版本库——版本号的唯一来源是 git tag，抄一份进 Python 或 JS 就是第二个事实
    # 来源。文件不在就不列：漏了它「设置 → 关于」只是显示不出服务端版本，不该让构建失败。
    datas=[('backend/app/web/static', 'app/web/static'),
           ('backend/app/web/admin', 'app/web/admin'),
           ('backend/app/web/site', 'app/web/site')] + chroma_datas + binding_datas + fitz_datas \
          + ([('version.txt', '.')] if os.path.isfile('version.txt') else []),
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
        # 本应用嵌入走的是 .env 里配的云端地址，不加载任何本地模型；以下均是被可选依赖链
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

# ── VC 运行库换血：顶层那份 msvcp/vcruntime 必须来自系统，不能是构建 Python 的同级旧拷贝 ──
# 本机跑的是商店版 Python，它的安装目录里躺着一份 msvcp140.dll 14.29（"built by:
# cloudtest"）。PyInstaller 顺 python312.dll 的依赖把它收进 _internal 顶层；exe 的
# DLL 搜索顺序是"应用目录优先"，于是 chromadb_rust_bindings.pyd（C++/MSVC，导入
# MSVCP140/VCRUNTIME140/VCRUNTIME140_1）被塞了一份比它构建时预期更老的运行库。
# 表现：手机第一次发 /v1/chat/stream，记忆检索走 HNSW query，在 MSVCP140+0x13080
# 对空指针做写 → 0xc0000005 整进程阵亡 → cloudflared 回 502 → watchdog 拉起、下条
# 聊天再炸。2026-09-23 那次"HTTP 502"的全部根因，dump 里栈是
# chromadb_rust_bindings+0x22ec309 → MSVCP140+0x1300c。
# 最小复现（源码环境即可，不进 exe 也能炸）：
#   ctypes.WinDLL(r"…\_internal\MSVCP140.dll")   # 预载老运行库
#   chromadb.PersistentClient(...).get_or_create_collection("user_memories")
#       .query(query_embeddings=[[0.1]*1536], n_results=5)                      # 段错误
# numpy 自己那份哈希名并排的 msvcp140-<hash>.dll 不在射程内（SxS 名字不同，互不遮蔽），
# 所以只洗顶层裸名，并整体换成系统 System32 的现役版本。
_VC_NAMES = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
             "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
a.binaries = [b for b in a.binaries
              if os.path.basename(str(b[0])).lower() not in _VC_NAMES]
import glob as _glob
for _dll in (sorted(_glob.glob(r"C:\Windows\System32\msvcp140*.dll"))
             + sorted(_glob.glob(r"C:\Windows\System32\vcruntime140*.dll"))):
    a.binaries.append((os.path.basename(_dll), _dll, "BINARY"))

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
