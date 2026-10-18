"""验证码识别。

门户上其实有**两套**验证码，接口和形态都不同：

  * 登录页 /ACaptcha.ashx（200x60）—— 算术题，比如 "61加5=?"、"63÷7=?"，
    运算符会混用 + - × ÷ 和中文 加 减 乘 除，要提交的是**计算结果**。
    页面 title 原话是「请输入此验证码计算结果」。
  * 注销页 / 查询页 /CheckCode（80x24）—— 4 位字母数字，没变。

所以这里有两个识别器：

  * CaptchaSolver      —— 老的四位字母数字，注销和查询还要用。
  * MathCaptchaSolver  —— 新的算式题。

算式题干扰很重（彩色曲线、虚影背景字、满屏圆点），单个模型认不准。
这里的做法是**多变体 x 多模型投票**：同一张图用不同阈值/缩放切出若干版本，
分别喂给 ddddocr 的三个模型，把能解析成合法算式的答案收集起来投票，
票数最高者胜出。认不出就返回空结果，让上层换一张图，绝不去猜。
"""
from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# 旧版：4 位字母数字（注销 / 查询页仍在用）
# --------------------------------------------------------------------------- #
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_clean_re = re.compile(r"[^0-9A-Za-z]")


class CaptchaSolver:
    """线程安全的 ddddocr 包装（4 位字母数字）。"""

    def __init__(self, length: int = 4) -> None:
        self.length = length
        self._engine = None
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()   # 识别调用串行化：界面线程和重连线程会抢同一个引擎
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
        with self._io_lock:
            raw = engine.classification(png)
        return self.normalize(raw)

    def normalize(self, raw: str) -> str:
        if not raw:
            return ""
        text = _clean_re.sub("", raw).upper()
        if self.length and len(text) != self.length:
            return ""
        return text


# --------------------------------------------------------------------------- #
# 新版：算式验证码
# --------------------------------------------------------------------------- #
# 中文运算符 -> 符号，逐个 replace 即可。
_OP_WORDS = {
    "加": "+",
    "减": "-",
    "乘": "*",
    "除": "/",
}
# 各种被认歪的符号。ddddocr 常把 + 认成 十、把 - 认成 一、把 x 认成 X。
_OP_SYMBOLS = {
    "×": "*",
    "✕": "*",
    "✖": "*",
    "ｘ": "*",
    "Ｘ": "*",
    "x": "*",
    "X": "*",
    "乖": "*",   # “乘”字被认成“乖”是常事
    "÷": "/",
    "／": "/",
    "−": "-",
    "–": "-",
    "—": "-",
    "一": "-",   # 半截的减号
    "ー": "-",
    "＋": "+",
    "十": "+",
    "士": "+",
}
# 解析前统一剥掉的非算式字符（= ? 这些不用我们管）
_STRIP_RE = re.compile(r"[^0-9+\-*/]")
# A op B，两边最多 3 位（门户输入框 maxlength=4，结果不可能超过 4 位）
_EXPR_RE = re.compile(r"(\d{1,3})([+\-*/])(\d{1,3})")


@dataclass
class MathCaptcha:
    """一次算式识别的结果。answer 为空表示这张图没认出来。"""

    raw: str = ""
    expr: str = ""
    answer: str = ""
    votes: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.answer)

    def describe(self) -> str:
        if not self.ok:
            return "未识别"
        tail = f"（{self.votes} 票）" if self.votes else ""
        return f"{self.expr} = {self.answer}{tail}"


def normalize_expr(raw: str) -> str:
    """把模型吐出来的字符串尽量还原成 A op B 形式，失败返回空串。"""
    if not raw:
        return ""
    text = raw
    for word, sym in _OP_WORDS.items():
        text = text.replace(word, sym)
    for sym, rep in _OP_SYMBOLS.items():
        text = text.replace(sym, rep)
    text = _STRIP_RE.sub("", text)
    # 真正的题目只有一个运算符；出现两个以上基本就是把干扰认成符号了，宁可不猜
    if sum(1 for ch in text if ch in "+-*/") != 1:
        return ""
    m = _EXPR_RE.search(text)
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else ""


def compute(expr: str) -> Optional[str]:
    """算出结果。负数、除不尽、超过 4 位都视为非法（门户不会出这种题）。"""
    m = _EXPR_RE.fullmatch(expr or "")
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    if op == "+":
        result = a + b
    elif op == "-":
        result = a - b
    elif op == "*":
        result = a * b
    else:  # "/"
        if b == 0 or a % b != 0:
            return None
        result = a // b
    if result < 0 or result > 9999:
        return None
    return str(result)


class MathCaptchaSolver:
    """算式验证码识别器：多变体 x 多模型投票。

    ddddocr 的三个模型（默认 / old / beta）各自会错在不同的图上，
    同一张图的多种二值化变体也是各有各的错法，投票能把它们的大部分
    错误互相抵消掉。
    """

    #: 二值化阈值（像素最大通道 < 阈值 视为前景）。字符深浅不一，多扫几档。
    THRESHOLDS: tuple[int, ...] = (150, 160, 165, 170, 175, 180)
    #: 放大倍数。小图放大后模型准确率明显更高。
    SCALES: tuple[int, ...] = (3, 4)

    def __init__(self) -> None:
        self._engines: list = []
        self._lock = threading.Lock()
        self._solve_lock = threading.Lock()   # 界面线程与自动重连线程可能同时来问
        self.last_error: Optional[str] = None
        self.last_stats: dict[str, int] = {}

    # -- 引擎 ---------------------------------------------------------------- #
    def warmup(self) -> bool:
        try:
            self._ensure()
            return True
        except Exception as exc:  # pragma: no cover - 环境相关
            self.last_error = str(exc)
            return False

    def _ensure(self) -> list:
        if not self._engines:
            with self._lock:
                if not self._engines:
                    import ddddocr

                    built = []
                    for kwargs in (
                        {"show_ad": False},
                        {"show_ad": False, "old": True},
                        {"show_ad": False, "beta": True},
                    ):
                        try:
                            built.append(ddddocr.DdddOcr(**kwargs))
                        except Exception:
                            # 老版本 ddddocr 没有 old / beta 参数，跳过就好
                            continue
                    if not built:  # pragma: no cover - 极端环境
                        raise RuntimeError("ddddocr 一个模型都没加载成功")
                    self._engines = built
        return self._engines

    # -- 预处理 -------------------------------------------------------------- #
    @staticmethod
    def _variants(png: bytes) -> list[bytes]:
        """把原图切成若干「白底黑字」的识别候选。"""
        from PIL import Image
        import cv2
        import numpy as np

        try:
            src = Image.open(io.BytesIO(png)).convert("RGB")
        except Exception:
            return []
        rgb = np.array(src)
        vmax = rgb.max(axis=2)
        out: list[bytes] = []

        for thresh in MathCaptchaSolver.THRESHOLDS:
            mask = ((vmax < thresh).astype(np.uint8)) * 255
            # 2x2 开运算剔掉圆点和 1px 细线，保留字符笔画
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
            base = Image.fromarray(255 - mask).convert("RGB")
            for scale in MathCaptchaSolver.SCALES:
                im = base.resize(
                    (base.width * scale, base.height * scale), Image.LANCZOS
                )
                buf = io.BytesIO()
                im.save(buf, "PNG")
                out.append(buf.getvalue())
        return out

    # -- 识别 ---------------------------------------------------------------- #
    def solve(self, png: bytes) -> MathCaptcha:
        """识别并计算。认不出来就返回 answer 为空的对象。

        加锁是因为界面线程和自动重连线程可能同时拿这个识别器问，
        ddddocr 未必扛得住并发（一次 solve 要跑三十来次分类）。
        """
        with self._solve_lock:
            return self._solve_locked(png)

    def _solve_locked(self, png: bytes) -> MathCaptcha:
        engines = self._ensure()
        votes: dict[str, list[tuple[str, str]]] = {}
        stats = {"recognized": 0, "parsed": 0}

        for variant in self._variants(png):
            for engine in engines:
                try:
                    raw = engine.classification(variant)
                except Exception:
                    continue
                stats["recognized"] += 1
                expr = normalize_expr(raw)
                if not expr:
                    continue
                answer = compute(expr)
                if not answer:
                    continue
                stats["parsed"] += 1
                votes.setdefault(answer, []).append((expr, raw))

        self.last_stats = stats
        if not votes:
            return MathCaptcha()

        answer, hits = max(votes.items(), key=lambda kv: len(kv[1]))
        expr, raw = hits[0]
        return MathCaptcha(raw=raw, expr=expr, answer=answer, votes=len(hits))


# --------------------------------------------------------------------------- #
# 默认实例
# --------------------------------------------------------------------------- #
_default: Optional[CaptchaSolver] = None
_default_lock = threading.Lock()
_default_math: Optional[MathCaptchaSolver] = None
_default_math_lock = threading.Lock()


def get_solver() -> CaptchaSolver:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = CaptchaSolver()
    return _default


def get_math_solver() -> MathCaptchaSolver:
    global _default_math
    if _default_math is None:
        with _default_math_lock:
            if _default_math is None:
                _default_math = MathCaptchaSolver()
    return _default_math
