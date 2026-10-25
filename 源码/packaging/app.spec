# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置。由 build.ps1 调用：
#   GUIT_ONEDIR=1 出文件夹版，否则出单文件版。
#
# ROOT 由 SPEC 推导，换机器 / 换克隆目录都不用改。
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC))).replace("\\", "/")
ONEDIR = os.environ.get("GUIT_ONEDIR") == "1"
ICON = ROOT + "/assets/icon.ico"
NAME = "GUIT校园网一键联网"

# 只带两个自训 CRNN 模型：
#   mathcaptcha.onnx  算式验证码（96x320，int8 6.26MB）
#   captcha4.onnx     四位字母数字（32x128，int8 4.18MB）
# ddddocr 已经从产物里整体移除：它的 common.onnx(51MB raw) 在 150 张人工真值
# 上只有 71.3%，common_slim(19.8MB) 80.0%，而 captcha4 是 96.0% 且只要 4MB。
# 这里逐文件列出而不是整个 models/ 目录，避免把备份/实验模型也打进去。
datas = [(ROOT + "/assets", "assets")]
MODELS = ROOT + "/models"
datas += [
    (MODELS + "/mathcaptcha.onnx", "models"),
    (MODELS + "/captcha4.onnx", "models"),
]
binaries = []
hiddenimports = []

# —— onnxruntime ——
# 只取它的 datas/binaries，**丢掉 collect_all 给的 hiddenimports**。
# 那份 hiddenimports 是全量子模块（quantization / transformers / tools），
# 其中 onnxruntime/tools/symbolic_shape_infer.py 顶部就 import sympy，
# 于是 sympy(6.2MB)+mpmath(0.7MB) 整条符号计算链被塞进 PYZ，而运行时
# 只会用到 InferenceSession —— 这条链永远走不到（实测剔除后识别结果不变）。
_ort = collect_all("onnxruntime")
datas += _ort[0]
binaries += _ort[1]
hiddenimports += [
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi._pybind_state",
    "onnxruntime.capi.onnxruntime_inference_collection",
    "onnxruntime.capi.onnxruntime_validation",
    "onnxruntime.capi.version_info",
]
# 附带的两个副作用（已逐个核对，属实且无害）：
#   · PIL/_imagingft.pyd（2.1MB，TrueType 字体渲染）与 ml_dtypes（1.0MB，bfloat16）
#     原先只是被 quantization/transformers 子模块顺带 import 才进包的；src/ 与
#     tests/ 都没有 ImageFont / ImageDraw / ml_dtypes 的引用，ddddocr 只做
#     classification（不渲染文字），运行时不走这两条路；
#   · _uuid.pyd（20KB）同理 —— 分析期已经没有任何模块 import uuid，
#     uuid 若真被动态 import 会走纯 Python 实现。
# 若以后要在界面上用 QPainter 画自定义字体、或用 bfloat16，需要把对应模块
# 加回 hiddenimports。

# —— 运行时不存在的二进制 ——
# ① opengl32sw.dll（19.7MB，Qt 软件 OpenGL 兜底）/ _avif.pyd（7.5MB，Pillow 的
#    AVIF 解码器，验证码是 PNG）—— 由 hook 在 Analysis() 内部塞进来；
# ② libcrypto-3-x64.dll / libssl-3-x64.dll / ucrtbase.dll —— bindepend 顺着 PATH
#    在另一套 Anaconda（D:\AAA bisai\AN）里找到的**重复** OpenSSL，而 _ssl.pyd
#    真正链接的是 Python 自带的 libcrypto-3.dll / libssl-3.dll（这两份都还在）。
#    实测删掉后 requests/ssl 正常，识别结果不变。
_SKIP_SUBSTR = ("opengl32sw", "_avif")
_SKIP_SRC = ("aaa bisai",)          # 来源路径不在 venv、也不在 Python 安装目录
# PySide6 / shiboken6 里运行时用不到的整类文件（dest 相对 _internal 的路径）。
_PYSIDE_DROP_PREFIX = (
    "pyside6/plugins/tls/",              # TLS 后端属于 QtNetwork，走不到
    "pyside6/plugins/networkinformation/",
    "pyside6/plugins/generic/",
)
_PYSIDE_DROP_EXACT = {
    "pyside6/qt6network.dll",            # 网络全走 requests（自带 OpenSSL），没有 QNetworkAccessManager
    "pyside6/qtnetwork.pyd",
    "pyside6/qt6svg.dll",                # 图标是 .ico + .gif，没有 svg
    "pyside6/plugins/iconengines/qsvgicon.dll",
    "pyside6/plugins/platforms/qdirect2d.dll",   # 实验性 Direct2D 后端，默认平台是 qwindows
    "pyside6/plugins/imageformats/qtiff.dll",    # 只用到 ico/gif/png
    "pyside6/plugins/imageformats/qwebp.dll",
    "pyside6/plugins/imageformats/qjpeg.dll",
    "pyside6/plugins/imageformats/qicns.dll",
    "pyside6/plugins/imageformats/qtga.dll",
    "pyside6/plugins/imageformats/qwbmp.dll",
    "pyside6/plugins/imageformats/qsvg.dll",
    # MSVC 运行库：_internal 根目录已有一份（Python 自己带的那份），
    # 这两处副本是 Qt wheel 自带的，删掉后 Qt/.pyd 仍能从根目录解析到。
    "pyside6/msvcp140.dll", "pyside6/msvcp140_1.dll", "pyside6/msvcp140_2.dll",
    "pyside6/vcruntime140.dll", "pyside6/vcruntime140_1.dll",
    "shiboken6/msvcp140.dll", "shiboken6/msvcp140_1.dll",
    "shiboken6/vcruntime140.dll", "shiboken6/vcruntime140_1.dll",
}
# 只留 Qt 自己的简体中文翻译（QMessageBox 等标准按钮的本地化），其余 30 多个
# 语言的 .qm 全部去掉（约 1.9MB 未压缩）。
_PYSIDE_KEEP_TRANSLATIONS = {"qtbase_zh_cn.qm", "qt_zh_cn.qm"}


def _skip(dest, src=""):
    d = dest.replace("\\", "/").lower()
    s = src.replace("\\", "/").lower()
    if any(k in d for k in _SKIP_SUBSTR):
        return True
    if any(k in s for k in _SKIP_SRC):
        return True
    if d.startswith("pyside6/translations/"):
        return os.path.basename(d) not in _PYSIDE_KEEP_TRANSLATIONS
    if d.startswith(_PYSIDE_DROP_PREFIX) or d in _PYSIDE_DROP_EXACT:
        return True
    return False


def _keep(entries):
    return [e for e in entries if not _skip(e[0], e[1] if len(e) > 1 else "")]


a = Analysis(
    [ROOT + "/run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 原先只为 cv2.cvtColor / morphologyEx 三个函数拖进 112MB 的 OpenCV，
        # 现已用 PIL + numpy 手写等价实现（tools/verify_nocv.py 逐变体字节比对 77/80 一致）。
        # 注意：ddddocr 顶层的 slide_engine 会无条件 import cv2，所以运行时由
        # src/ocr.py 的 _install_cv2_stub() 注入空壳。
        # onnx 只是开发期用来读模型结构的工具（裁剪输出层那一步），
        # 运行时完全不用 —— 它带一个上百 MB 的 C++ 扩展，误打包会让 exe 直接翻倍。
        "onnx",
        # torch 只用来训 CRNN（tools/train_crnn.py），推理走 numpy/onnxruntime。
        # 它的 DLL 有 2000+ 个，被 hook 顺手拖进来能撑大几十 MB。
        "torch",
        "torchvision",
        "cv2",
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.Qt3DCore",
        "matplotlib",
        # —— 开发期/测试期的依赖链，运行时零引用 ——
        # sympy/mpmath 是 onnxruntime.tools.symbolic_shape_infer 的依赖；
        # pytest/pygments/pluggy 是测试框架；setuptools/pkg_resources/distutils
        # 只有 numpy.f2py、pkg_resources 入口这些代码路径会碰。
        # 实测全排除后 pytest 全绿、exe [SELFTEST OK]、两条样例识别结果不变。
        "sympy",
        "mpmath",
        "pygments",
        "pytest",
        "_pytest",
        "pluggy",
        "iniconfig",
        "setuptools",
        "pkg_resources",
        "distutils",
        "pydoc",
        "pydoc_data",
        "doctest",
    ],
    noarchive=False,
    optimize=0,
)
# hook 追加完才能过滤（见上面 _skip 的注释）。
a.binaries = _keep(a.binaries)
a.datas = _keep(a.datas)

pyz = PYZ(a.pure)

if ONEDIR:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=[ICON],
        version=ROOT + "/build/version_info.txt",
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=NAME,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=[ICON],
        version=ROOT + "/build/version_info.txt",
    )
