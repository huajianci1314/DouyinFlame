# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：抖音续火助手 onedir + 无控台窗口。

static 资源进 _internal（运行期由 sys._MEIPASS 解析）；
playwright 连同 Node 驱动整体收集（collect_all）；
chromium 内核不走 PyInstaller，由 build_exe.py 在打包后复制进
dist/DouyinFlame/runtime/browsers，运行期用环境变量指向它。
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ("static", "static"),
]
binaries = []
hiddenimports = [
    # uvicorn 用 importlib 动态导入这些子模块，静态分析抓不到
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
]
for pkg in ("playwright", "greenlet", "pyee"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "pytest",
        "IPython", "jedi", "setuptools", "pip", "wheel",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DouyinFlame",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DouyinFlame",
)
