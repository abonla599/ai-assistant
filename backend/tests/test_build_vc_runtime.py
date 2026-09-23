# backend/tests/test_build_vc_runtime.py
"""冻结版顶层 VC 运行库必须是系统现役版本，不许夹带商店版 Python 的 14.29 老拷贝。

2026-09-23 的"手机聊天 HTTP 502"就是这条链：PyInstaller 把 Store-Python 同级的
msvcp140.dll 14.29 收进 _internal 顶层 → exe 应用目录优先遮蔽系统 14.51 →
chromadb_rust_bindings 的 HNSW query 在 MSVCP140 里对空指针做写 → 整进程
0xc0000005。复现与栈证据见 run_backend.spec 那段注释。

两类锁：
1. 源锁：spec 必须"先滤顶层裸名、再从 System32 补回"，缺任何一半都红；
2. 产物锁：本机若已有 dist/run_backend 构建产物，直接验那几个 DLL 的文件版本。
   CI 没有构建产物 → 跳过，不造假。
"""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "run_backend.spec"
DIST_INTERNAL = ROOT / "dist" / "run_backend" / "_internal"

VC_NAMES = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")


def test_spec_replaces_top_level_vc_runtime_with_system32():
    src = SPEC.read_text(encoding="utf-8")
    # 半 1：把收集来的顶层裸名滤掉（按 basename 小写比较，才杀得掉大小写各异的入口）
    assert re.search(r"basename\(\s*str\([^)]*\)\s*\)\.lower\(\)\s+not\s+in\s+_VC_NAMES", src), \
        "spec 不再过滤顶层 VC 运行库——Store-Python 的 14.29 老拷贝会重新混进 exe"
    # 半 2：从 System32 补回现役版本（msvcp140* 与 vcruntime140* 两类都要）
    assert r"C:\Windows\System32\msvcp140*.dll" in src and \
           r"C:\Windows\System32\vcruntime140*.dll" in src, \
        "spec 只滤不补：chromadb_rust_bindings 会找不到 MSVCP140.dll，exe 启动即 ImportError"
    # 过滤必须发生在 Analysis 之后、COLLECT 之前（对 a.binaries 动手）
    assert re.search(r"a\.binaries\s*=\s*\[[^\]]*a\.binaries", src), \
        "VC 换血必须作用在 a.binaries 上，位置错了等于没做"


@pytest.mark.skipif(not DIST_INTERNAL.is_dir(), reason="本机没有构建产物，产物锁跳过")
@pytest.mark.parametrize("dll", VC_NAMES)
def test_bundled_vc_runtime_is_not_the_stale_store_python_copy(dll):
    target = DIST_INTERNAL / dll
    if not target.is_file():
        pytest.skip(f"产物里没有 {dll}（运行库收集形状变了，产物锁不猜）")
    import ctypes
    from ctypes import wintypes

    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(target), None)
    assert size, f"{dll} 读不到文件版本"
    buf = ctypes.create_string_buffer(size)
    assert ctypes.windll.version.GetFileVersionInfoW(str(target), 0, size, buf)
    p = wintypes.LPVOID()
    buflen = wintypes.UINT()
    # VsFixedFileInfoSignature = '\\VS_VERSION_INFO'
    assert ctypes.windll.version.VerQueryValueW(
        buf, "\\VS_VERSION_INFO", ctypes.byref(p), ctypes.byref(buflen))
    fixed = p.value  # VS_FIXEDFILEINFO*, dword-aligned
    # ms = [0]dwSignature,[1]dwStrucVersion,[2]dwFileVersionMS,[3]dwFileVersionLS
    ms, ls = ctypes.cast(fixed, ctypes.POINTER(ctypes.c_uint32))[2], \
             ctypes.cast(fixed, ctypes.POINTER(ctypes.c_uint32))[3]
    major, minor = ms >> 16, ms & 0xFFFF
    build = ls >> 16
    print(f"{dll}: {major}.{minor}.{build}")
    assert (major, minor) >= (14, 30), (
        f"{dll} 打包版本 {major}.{minor}.{build} 还是商店版 Python 的同级老拷贝——"
        "chroma 的 HNSW query 会在 MSVCP140 里空指针写崩（2026-09-23 聊天 502 根因）"
    )
