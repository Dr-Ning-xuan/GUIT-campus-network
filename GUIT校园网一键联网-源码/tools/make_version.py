"""从 src/version.py 生成 PyInstaller 的版本资源文件（build/version_info.txt）。

版本号只留一个来源：改 src/version.py，exe 属性和窗口标题一起更新。
用法: python tools/make_version.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "version.py"
DST = ROOT / "build" / "version_info.txt"

PRODUCT = "GUIT校园网一键联网"
OWNER = "宁轩"
DESC = "校园网一键联网 - 自动识别验证码并登录校园网"
COMMENT = "迷迭香给宁轩做的喵~"

TEMPLATE = """# 本文件由 tools/make_version.py 生成，别手改（改 src/version.py）
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={filevers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'{owner}'),
        StringStruct(u'FileDescription', u'{desc}'),
        StringStruct(u'FileVersion', u'{ver4}'),
        StringStruct(u'InternalName', u'CampusAutoLogin'),
        StringStruct(u'LegalCopyright', u'{owner}'),
        StringStruct(u'OriginalFilename', u'{product}.exe'),
        StringStruct(u'ProductName', u'{product}'),
        StringStruct(u'ProductVersion', u'{ver4}'),
        StringStruct(u'Comments', u'{comment}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def read_version() -> str:
    for line in SRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip(chr(34)).strip(chr(39))
    raise SystemExit("在 %s 里没找到 __version__" % SRC)


def main() -> int:
    ver = read_version()
    parts = [int(x) for x in ver.split(".")]
    parts = (parts + [0, 0, 0, 0])[:4]
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(
        TEMPLATE.format(
            filevers=tuple(parts),
            ver4=".".join(str(p) for p in parts),
            product=PRODUCT,
            owner=OWNER,
            desc=DESC,
            comment=COMMENT,
        ),
        encoding="utf-8",
    )
    print("[ok] version %s -> %s" % (ver, DST))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
