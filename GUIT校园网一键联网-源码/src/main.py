"""入口：校园网一键联网（桂林信息科技学院）。"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path  # noqa: F401  (类型注解用)


def _single_instance() -> bool:
    """用命名互斥体防止开两份。"""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Global\\GUITCampusAutoLogin")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _resource(name: str) -> "Path":
    from pathlib import Path

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for cand in (base / "assets" / name, Path(__file__).resolve().parent.parent / "assets" / name):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"内置资源缺失：{name}")


def _selftest() -> int:
    """打包后自检：能 import、能加载识别模型、能识别内置样图。

    --windowed 打包出来的 exe 在 Windows 上没有 stdout，
    所以结果必须同时写进 exe 同目录的 _selftest.log。
    """
    from src.store import app_dir

    log_path = app_dir() / "_selftest.log"
    try:
        log_path.unlink(missing_ok=True)
    except Exception:
        pass

    from src.store import hide_file as _hide

    def print(*a, **_k):  # noqa: A001 - 故意遮蔽，统一收口
        text = " ".join(str(x) for x in a)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
            _hide(log_path)
        except Exception:
            pass
        try:
            sys.__stdout__.write(text + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass

    ok = True
    print(f"[selftest] python {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}")

    try:
        import requests  # noqa: F401

        print("[selftest] requests OK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[selftest] requests FAIL: {exc}")

    try:
        from PySide6 import QtCore

        print(f"[selftest] PySide6 OK ({QtCore.__version__})")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[selftest] PySide6 FAIL: {exc}")

    try:
        from src.ocr import get_solver

        solver = get_solver()
        sample = _resource("sample_captcha.png").read_bytes()
        code = solver.solve(sample)
        print(f"[selftest] ddddocr OK, sample[{len(sample)}B] -> {code!r}")
        if len(code) != 4:
            ok = False
            print("[selftest] 识别结果不是 4 位，可能模型没打进去")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[selftest] ddddocr FAIL: {exc!r}")

    try:
        from src.store import Config, config_path

        print(f"[selftest] config path = {config_path()}")
        _ = Config.load()
        print("[selftest] Config OK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[selftest] Config FAIL: {exc}")

    try:
        from src.gui import MainWindow  # noqa: F401

        print("[selftest] gui import OK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[selftest] gui FAIL: {exc}")

    print("[SELFTEST OK]" if ok else "[SELFTEST FAILED]")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校园网一键联网")
    parser.add_argument("--minimized", action="store_true", help="启动后最小化到托盘")
    parser.add_argument("--no-single-instance", action="store_true", help="允许多开")
    parser.add_argument("--selftest", action="store_true", help="打包后自检：依赖/识别引擎/内置样图")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.selftest:
        return _selftest()

    if not args.no_single_instance and not _single_instance():
        print("程序已经在运行中")
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.ocr import get_solver
    from src.store import Config
    from src.gui import MainWindow, app_icon

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("CampusAutoLogin")
    app.setApplicationDisplayName("校园网一键联网")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GUIT.CampusAutoLogin")
    except Exception:
        pass

    cfg = Config.load()
    solver = get_solver()

    # 引擎预热放到后台线程，避免首屏卡顿
    threading.Thread(target=solver.warmup, daemon=True).start()

    win = MainWindow(cfg, solver, start_hidden=args.minimized and True)
    if cfg.auto_reconnect:
        win.chk_auto.setChecked(True)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
