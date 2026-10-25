# -*- coding: utf-8 -*-
"""四位字母数字验证码 ONNX 识别器（自训 CRNN，单图一次前向）。

替代 ddddocr 的默认 common.onnx（51 MB raw / 6.46 MB 打包）与 slim（18.36 MB）。
自训模型 int8 只有 4 MB，在 150 张人工真值上 94.7%，ddddocr 分别是 71.3% / 80.0%。

接口与 ocr.CaptchaSolver.solve() 完全一致：返回长度不足时为空串，让调用方重取图。
"""
from __future__ import annotations

import io
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = ROOT / "models" / "captcha4.onnx"

# 必须与训练时 model_4.CHARSET 一致
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BLANK = len(CHARSET)
NUM_CLASS = BLANK + 1
IN_H, IN_W = 32, 128

_lock = threading.Lock()


def _decode(logits) -> str:
    """CTC 贪心解码：合并重复、去掉 blank。"""
    idx = logits.argmax(axis=2)
    out = []
    for seq in idx:
        prev, chars = -1, []
        for i in seq:
            i = int(i)
            if i != prev and i != BLANK and 0 <= i < BLANK:
                chars.append(CHARSET[i])
            prev = i
        out.append("".join(chars))
    return out[0] if out else ""


class Crnn4Solver:
    """ONNX CRNN 四位验证码识别。懒加载，CPU 单图约 4~8 ms。"""

    single_shot = True

    def __init__(self, model_path: str | Path | None = None, length: int = 4) -> None:
        self._model_path = Path(model_path) if model_path else DEFAULT_MODEL
        self.length = length
        self._sess = None
        self._inp = None
        self._in_h = IN_H
        self._in_w = IN_W
        self._io_lock = threading.Lock()
        self.last_error: str | None = None

    def warmup(self) -> bool:
        try:
            self._ensure()
            return True
        except Exception as exc:  # pragma: no cover - 环境相关
            self.last_error = str(exc)
            return False

    def _ensure(self):
        if self._sess is not None:
            return self._sess
        with _lock:
            if self._sess is not None:
                return self._sess
            try:
                import onnxruntime as ort

                sess = ort.InferenceSession(str(self._model_path),
                                            providers=["CPUExecutionProvider"])
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                raise
            shp = sess.get_inputs()[0].shape
            if len(shp) == 4:
                # NCHW -> H, W（warmup 时用实际尺寸，避免和训练尺寸对不上）
                self._in_h = int(shp[2]) if isinstance(shp[2], int) else IN_H
                self._in_w = int(shp[3]) if isinstance(shp[3], int) else IN_W
            self._inp = sess.get_inputs()[0].name
            dummy = __import__("numpy").zeros((1, 3, self._in_h, self._in_w), dtype="float32")
            sess.run(None, {self._inp: dummy})
            self._sess = sess
        return self._sess

    def _infer(self, png: bytes) -> str:
        import numpy as np
        from PIL import Image

        im = Image.open(io.BytesIO(png)).convert("RGB")
        im = im.resize((self._in_w, self._in_h), Image.BILINEAR)
        a = np.asarray(im).astype(np.float32) / 255.0
        a = (a - 0.5) / 0.5
        x = a.transpose(2, 0, 1)[None]
        logits = self._sess.run(None, {self._inp: x})[0]
        return _decode(logits)

    def solve(self, png: bytes) -> str:
        """返回清洗后的验证码（长度不足 length 时返回空串，让调用方重取图）。"""
        self._ensure()
        with self._io_lock:
            try:
                raw = self._infer(png)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                return ""
        return self.normalize(raw)

    def normalize(self, raw: str) -> str:
        if not raw:
            return ""
        text = "".join(c for c in raw if c.isalnum()).upper()
        if self.length and len(text) != self.length:
            return ""
        return text


_default: Crnn4Solver | None = None
_default_lock = threading.Lock()


def get_crnn4_solver() -> Crnn4Solver:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = Crnn4Solver()
    return _default
