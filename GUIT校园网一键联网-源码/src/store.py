"""配置读写：exe 同目录的 config.json（明文保存，方便直接查看和修改）。"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CONFIG_NAME = "config.json"


def hide_file(path: Path | str) -> None:
    """Windows 上给文件加隐藏属性。

    config.json / _boot.log 这些是程序自己的东西，躺在 exe 旁边（经常就是桌面）
    太碍眼了，写完之后直接设成隐藏属性。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def app_dir() -> Path:
    """exe（打包后）或项目根（源码运行）所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_dir() / CONFIG_NAME


@dataclass
class Config:
    base_url: str = "http://10.6.6.6"
    gui_id: str = ""          # 学(工)号
    account: str = ""         # 上网账号
    password: str = "123456"  # 明文保存（默认 123456）
    save_credentials: bool = True   # 关掉后 config.json 里不留账号密码
    isp: str = "自动选择"
    auto_reconnect: bool = False
    auto_connect_on_start: bool = True   # 打开程序就检查，没在线就自动连上
    poll_seconds: int = 15
    autostart: bool = False
    start_minimized: bool = False
    manual_captcha: bool = True    # 识别失败时允许手动输入（新算式验证码容易认歪，默认开着）
    max_captcha_retry: int = 10    # 最多提交几次；算式验证码认错概率不低，多给几次机会

    # -- 读写 ---------------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = path or config_path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        cfg = cls()
        for k, v in data.items():
            setattr(cfg, k, v)
        cfg.sanitize()
        return cfg

    def save(self, path: Path | None = None) -> Path:
        self.sanitize()
        p = path or config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        hide_file(p)
        return p

    _CRED_KEYS = ("gui_id", "account", "password")

    def to_dict(self) -> dict:
        d = asdict(self)
        if not self.save_credentials:
            # 不保存时把凭据整个 key 拿掉（而不是写空串），
            # 这样下次 load 会回落到默认值（密码 123456），而不是空密码。
            for k in self._CRED_KEYS:
                d.pop(k, None)
        return d

    def sanitize(self) -> "Config":
        self.base_url = (self.base_url or "http://10.6.6.6").strip().rstrip("/")
        self.gui_id = (self.gui_id or "").strip()
        self.account = (self.account or "").strip()
        self.isp = (self.isp or "自动选择").strip()
        try:
            self.poll_seconds = max(3, min(600, int(self.poll_seconds)))
        except Exception:
            self.poll_seconds = 15
        try:
            self.max_captcha_retry = max(1, min(20, int(self.max_captcha_retry)))
        except Exception:
            self.max_captcha_retry = 6
        return self
