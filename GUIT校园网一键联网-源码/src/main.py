"""入口：校园网一键联网（桂林信息科技学院）。"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path  # noqa: F401  (类型注解用)


APP_MUTEX = "Global\\GUITCampusAutoLogin"
#: 窗口标题里一定有这一串（后面跟版本号），用来找「已经在跑的那一份」
APP_TITLE_KEY = "GUIT校园网一键联网"


def _single_instance() -> bool:
    """用命名互斥体防止开两份。"""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, APP_MUTEX)
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _activate_existing() -> bool:
    """把已经在跑的那一份窗口叫出来。

    程序点「关闭」默认是收进托盘常驻的，进程没退、互斥体还在。
    以前这时候再双击 exe 会静默退出，用户看到的就是「打不开」——
    所以这里必须把已有窗口恢复并置顶。
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):  # noqa: ANN001
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if APP_TITLE_KEY in buf.value:
                    found.append(hwnd)
            return True

        user32.EnumWindows(_enum, 0)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE：托盘的隐藏窗口也能拉出来
        # 关键：光 ShowWindow 只会拉出 Win32 的外框，Qt 还认为自己隐藏着，
        # 于是画出来是一片空白，得再点任务栏才重绘。这里强制重画一遍。
        SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040
        user32.SetWindowPos(
            hwnd, -1, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
        )  # -1 = HWND_TOPMOST 的占位，随后马上取消置顶
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
        user32.InvalidateRect(hwnd, None, True)
        user32.UpdateWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _notify_running() -> None:
    """实在找不到窗口，也得让用户看见一句话，不能静默退出。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "程序已经在运行了。\n\n"
            "窗口可能在屏幕右下角的托盘里（点一下 ^ 展开就能看到），"
            "双击那里的图标就能打开。",
            "校园网一键联网",
            0x40,  # MB_ICONINFORMATION
        )
    except Exception:
        pass


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
        from src.ocr import get_math_solver, get_solver

        # 老的四位字母数字（注销/查询页那套）
        solver = get_solver()
        sample = _resource("sample_captcha.png").read_bytes()
        code = solver.solve(sample)
        print(f"[selftest] ddddocr 4位 OK, sample[{len(sample)}B] -> {code!r}")
        if len(code) != 4:
            ok = False
            print("[selftest] 识别结果不是 4 位，可能模型没打进去")

        # 新的算式题（登录页那套）
        math_solver = get_math_solver()
        msample = _resource("sample_captcha_math.png").read_bytes()
        cap = math_solver.solve(msample)
        print(f"[selftest] 算式识别 -> {cap.describe()}")
        if not cap.ok:
            ok = False
            print("[selftest] 算式样本没认出来，可能模型或预处理没打进去")
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
        # 已经有一份在跑：敲门让它自己把窗口亮出来，而不是像以前那样默默退出。
        # 优先用命名事件 —— 老实例收到后自己调 showNormal()，窗口内容才是完整的；
        # 拿不到事件（对面是老版本）才退回 Win32 硬拉窗口。
        from src.wakeup import fire as _fire_wake

        if not _fire_wake():
            if not _activate_existing():
                _notify_running()
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.ocr import get_math_solver, get_solver
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
    solver = get_solver()            # 四位字母数字（注销 / 查询）
    math_solver = get_math_solver()  # 算式题（登录）

    # 引擎预热放到后台线程，避免首屏卡顿
    threading.Thread(target=math_solver.warmup, daemon=True).start()
    threading.Thread(target=solver.warmup, daemon=True).start()

    win = MainWindow(cfg, solver, math_solver, start_hidden=args.minimized and True)
    if cfg.auto_reconnect:
        win.chk_auto.setChecked(True)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
