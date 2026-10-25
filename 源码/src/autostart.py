"""开机自启：写 HKCU\...\Run（不需要管理员权限）。"""
from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "CampusAutoLogin"
MIN_FLAG = "--minimized"


def _prefer_onedir(onefile_exe: Path) -> Path | None:
    """单文件版旁边如果放着同名文件夹版，返回文件夹版的可执行文件。

    单文件版（onefile）每次启动都要把上百 MB 解压到 TEMP。开机时磁盘正忙，
    可能要几十秒托盘才出现（容易被误认为"开机自启没生效"）；而且 TEMP 目录
    权限受限时会直接静默退 -1。文件夹版没有解压步骤，秒开，所以自启优先用它。
    """
    cand = onefile_exe.parent / onefile_exe.stem / onefile_exe.name
    try:
        if cand.is_file():
            return cand.resolve()
    except OSError:
        pass
    return None


def autostart_target() -> Path | None:
    """打包后：返回应当写进自启项的可执行文件。"""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    return _prefer_onedir(exe) or exe


def target_command(start_minimized: bool = True) -> str:
    if getattr(sys, "frozen", False):
        exe = autostart_target() or Path(sys.executable).resolve()
        cmd = f'"{exe}"'
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


def is_stale() -> bool:
    """已启用的自启项指向的命令，和当前应该用的命令不一致（例如换了打包位置）。"""
    cur = current_value()
    return bool(cur) and cur != target_command(True)


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
