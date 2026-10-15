"""开机自启：写 HKCU\...\Run（不需要管理员权限）。"""
from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "CampusAutoLogin"
MIN_FLAG = "--minimized"


def target_command(start_minimized: bool = True) -> str:
    if getattr(sys, "frozen", False):
        cmd = f'"{Path(sys.executable).resolve()}"'
    else:
        script = Path(__file__).resolve().parent / "main.py"
        cmd = f'"{Path(sys.executable).resolve()}" "{script}"'
    if start_minimized:
        cmd += f" {MIN_FLAG}"
    return cmd


def _winreg():
    import winreg  # noqa: WPS433 - 仅 Windows

    return winreg


def is_enabled() -> bool:
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def current_value() -> str:
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return value
    except OSError:
        return ""


def enable(start_minimized: bool = True) -> None:
    winreg = _winreg()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, target_command(start_minimized))


def disable() -> None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except FileNotFoundError:
        pass


def apply(enabled: bool, start_minimized: bool = True) -> None:
    if enabled:
        enable(start_minimized)
    else:
        disable()
