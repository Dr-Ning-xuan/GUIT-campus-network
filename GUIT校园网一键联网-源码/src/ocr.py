"""验证码识别：ddddocr 懒加载封装 + 结果清洗。

ddddocr 首次加载会读 onnx 模型（约 1-2 秒），故做成懒加载并在后台线程预热。
"""
from __future__ import annotations

import re
import threading
import time
from typing import Optional

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_clean_re = re.compile(r"[^0-9A-Za-z]")


class CaptchaSolver:
    """线程安全的 ddddocr 包装。"""

    def __init__(self, length: int = 4) -> None:
        self.length = length
        self._engine = None
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None

    # -- 引擎 ---------------------------------------------------------------- #
    def warmup(self) -> bool:
        try:
            self._ensure()
            return True
        except Exception as exc:  # pragma: no cover - 环境相关
            self.last_error = str(exc)
            return False

    def _ensure(self):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    import ddddocr

                    kw = {"show_ad": False}
                    try:
                        self._engine = ddddocr.DdddOcr(**kw)
                    except TypeError:
                        self._engine = ddddocr.DdddOcr()
        return self._engine

    # -- 识别 ---------------------------------------------------------------- #
    def solve(self, png: bytes) -> str:
        """返回清洗后的验证码（长度不足 length 时返回空串，让调用方重取图）。"""
        engine = self._ensure()
        raw = engine.classification(png)
        return self.normalize(raw)

    def normalize(self, raw: str) -> str:
        if not raw:
            return ""
        text = _clean_re.sub("", raw).upper()
        if self.length and len(text) != self.length:
            return ""
        return text


_default: Optional[CaptchaSolver] = None
_default_lock = threading.Lock()


def get_solver() -> CaptchaSolver:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = CaptchaSolver()
    return _default
