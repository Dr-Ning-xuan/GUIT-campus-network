# -*- coding: utf-8 -*-
"""算式验证码 ONNX 识别器（CRNN，单图一次前向）。

和 ocr.MathCaptchaSolver（ddddocr 多引擎多变体投票）是两条独立路线：
ddddocr 那套在真实分流图上答案正确率只有 36%，而它的根因是字符集里压根没有
"÷" 这个字形，除法题 30 张全错。CRNN 直接在 21 类字符集上分类，除数题能到 80%。

接口与 MathCaptchaSolver.solve() 保持一致，返回 ocr.MathCaptcha，方便直接替换。
"""
from __future__ import annotations

import io
import threading
from pathlib import Path

from .ocr import MathCaptcha, compute, normalize_expr

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = ROOT / "models" / "mathcaptcha.onnx"

CHARSET = "0123456789+-*=?\u00d7\u00f7\u52a0\u51cf\u4e58\u9664"
BLANK = len(CHARSET)
IN_H, IN_W = 96, 320

_lock = threading.Lock()


def _decode(logits) -> str:
    """CTC 贪心解码：合并重复、去掉 blank(索引 21)。"""
    idx = logits.argmax(axis=2)
    out = []
    for seq in idx:
        prev, chars = -1, []
        for i in seq:
            i = int(i)
            if i != prev and i != BLANK and 0 <= i < len(CHARSET):
                chars.append(CHARSET[i])
            prev = i
        out.append("".join(chars))
    return out[0] if out else ""


class CrnnSolver:
    """ONNX CRNN 算式识别。懒加载，CPU 单图约 3~5 ms。"""

    def __init__(self, model_path: str | Path | None = None):
        self._model_path = Path(model_path) if model_path else DEFAULT_MODEL
        self._sess = None
        self._inp = None
        self._in_h = IN_H
        self._in_w = IN_W
        self.last_error = ""

    def _load(self) -> bool:
        if self._sess is not None:
            return True
        with _lock:
            if self._sess is not None:
                return True
            try:
                import onnxruntime as ort

                so = ort.SessionOptions()
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                so.intra_op_num_threads = 2
                self._sess = ort.InferenceSession(
                    str(self._model_path), sess_options=so,
                    providers=["CPUExecutionProvider"],
                )
                inp = self._sess.get_inputs()[0]
                self._inp = inp.name
                # 输入尺寸跟着模型走，兼容旧的 60x200 与新的 96x320
                shp = list(inp.shape or [])
                if len(shp) == 4 and isinstance(shp[2], int) and isinstance(shp[3], int):
                    self._in_h, self._in_w = int(shp[2]), int(shp[3])
                self.last_error = ""
                return True
            except Exception as exc:  # 模型缺失/损坏时不能让登录整体崩掉
                self.last_error = f"{type(exc).__name__}: {exc}"
                return False

    def warmup(self) -> bool:
        """首帧预热，把 onnxruntime 的初始化开销挪到启动阶段。"""
        if not self._load():
            return False
        try:
            import numpy as np

            dummy = np.zeros((1, 3, self._in_h, self._in_w), dtype=np.float32)
            self._sess.run(None, {self._inp: dummy})
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    #: 单次前向、没有多引擎投票 → 调用方不要用票数门槛筛它。
    single_shot = True

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

    def solve(self, png: bytes) -> MathCaptcha:
        """识别一张算式验证码。answer 为空表示没认出来（调用方换图重取）。"""
        if not self._load():
            return MathCaptcha(raw="", expr="", answer="", votes=0)
        try:
            raw = self._infer(png)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return MathCaptcha(raw="", expr="", answer="", votes=0)

        expr = normalize_expr(raw)
        if not expr:
            return MathCaptcha(raw=raw, expr="", answer="", votes=0)
        ans = compute(expr)
        if ans is None:
            return MathCaptcha(raw=raw, expr=expr, answer="", votes=0)
        # CRNN 是单模型单次前向，没有投票概念，所以 votes=0（describe 里就不显示票数）。
        # 调用方的票数门槛必须被 single_shot 跳过，否则老配置里存的 min_votes=3/5
        # 会让每张图都"票太少不提交"→ 30 张全换成图、只提交最后一张被迫提交的错图。
        return MathCaptcha(raw=raw, expr=expr, answer=ans, votes=0,
                           candidates=[(ans, 0)])


_default_crnn: CrnnSolver | None = None
_default_crnn_lock = threading.Lock()


def get_crnn_solver() -> CrnnSolver:
    global _default_crnn
    if _default_crnn is None:
        with _default_crnn_lock:
            if _default_crnn is None:
                _default_crnn = CrnnSolver()
    return _default_crnn
