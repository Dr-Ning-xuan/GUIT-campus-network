"""跨进程「把已经在跑的那份窗口叫出来」。

Windows 命名事件：常驻的老实例一直监听着，新实例 `SetEvent` 敲一下门就完事。

为什么不用 `FindWindow` + `ShowWindow`？实测过：那样恢复的是 **Win32 层面的窗口**，
Qt 内部仍然认为自己处于 hidden 状态，于是会拉出一个**全白的空壳**，
得再点一次任务栏才肯重画。让老实例自己调 `showNormal()` 才是干净的。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

EVENT_NAME = "Local\\GUITCampusAutoLoginShow"
_WAIT_OBJECT_0 = 0
_EVENT_MODIFY_STATE = 0x0002


class WakeupServer:
    """常驻实例这一侧：建事件、轮询、被敲门时回调。"""

    def __init__(self, name: str = EVENT_NAME) -> None:
        self._name = name
        self._handle = None

    def create(self) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateEventW.restype = wintypes.HANDLE
            # 自动复位事件：WaitForSingleObject 拿到信号就自动清掉，不用手动 Reset
            self._handle = kernel32.CreateEventW(None, False, False, self._name)
            return bool(self._handle)
        except Exception:
            self._handle = None
            return False

    def poll(self) -> bool:
        """被敲过门就返回 True（并且这个信号已经自动清掉）。"""
        if not self._handle:
            return False
        try:
            kernel32 = ctypes.windll.kernel32
            return kernel32.WaitForSingleObject(self._handle, 0) == _WAIT_OBJECT_0
        except Exception:
            return False

    def close(self) -> None:
        try:
            if self._handle:
                ctypes.windll.kernel32.CloseHandle(self._handle)
        except Exception:
            pass
        self._handle = None


def fire(name: str = EVENT_NAME) -> bool:
    """新实例这一侧：敲一下门。返回 False 说明对面没在监听（可能是老版本）。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenEventW.restype = wintypes.HANDLE
        handle = kernel32.OpenEventW(_EVENT_MODIFY_STATE, False, name)
        if not handle:
            return False
        try:
            kernel32.SetEvent(handle)
        finally:
            kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False
