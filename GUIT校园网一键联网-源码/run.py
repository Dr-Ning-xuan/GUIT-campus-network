"""入口：python run.py（开发期） / PyInstaller 打包入口。

最外层故意写得啰嗦：frozen 环境里一旦崩在 DLL 加载这种进程级错误上，
Python 的 traceback 根本来不及打印，所以这里每走一步都实时落盘取证。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _hide(path) -> None:
    """给文件加隐藏属性（Windows），别让日志躺在桌面上。"""
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def early_log(msg: str) -> None:
    try:
        p = _base_dir() / "_boot.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        _hide(p)
    except Exception:
        pass
    try:
        sys.__stdout__.write(msg + "\n")
        sys.__stdout__.flush()
    except Exception:
        pass


if __name__ == "__main__":
    _base_dir().joinpath("_boot.log").unlink(missing_ok=True)

early_log(f"[boot] argv={sys.argv!r}")
early_log(f"[boot] frozen={getattr(sys, 'frozen', False)} exe={sys.executable}")
early_log(f"[boot] cwd={Path.cwd()}")

try:
    from src.main import main  # noqa: E402
except BaseException:
    early_log("[boot] IMPORT FAILED:\n" + traceback.format_exc())
    raise

if __name__ == "__main__":
    code = 0
    try:
        early_log("[boot] entering main()")
        code = main()
        early_log(f"[boot] main() returned {code}")
    except SystemExit as exc:
        early_log(f"[boot] SystemExit code={exc.code}")
        raise
    except BaseException:
        early_log("[boot] CRASH:\n" + traceback.format_exc())
        try:
            traceback.print_exc()
        except Exception:
            pass
        raise
    raise SystemExit(code)
