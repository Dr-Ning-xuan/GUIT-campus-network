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
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


def _resource(name: str) -> Path:
    """打包后资源在 _MEIPASS 下，开发时在仓库根目录。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / name

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
                    _install_cv2_stub()
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
# ddddocr 常把数字认成形近的字母。算式里除了运算符只会有数字，所以可以放心还原。
# 注意 x/X 已在 _OP_SYMBOLS 里映射成乘号，这里不再处理它们。
# 实测收益很明显：'I5-I' 原本会被整串剥成 '-I' 直接丢弃，还原后是 '15-1'。
# 只收大写字母：小写的 l / i 经常是「加」「减」这类汉字被认歪的偏旁，
# 把它们也改成 1 会把运算符整条吃掉（实测样本图 55加9 的 "55l9" 出现 7 次，
# 归一化后变成 "5519"，运算符没了只能丢弃）。
_LETTER_TO_DIGIT = {
    "I": "1", "|": "1", "丨": "1",
    "O": "0", "D": "0",
    "S": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
    "T": "7",
}
# 解析前统一剥掉的非算式字符（= ? 这些不用我们管）
_STRIP_RE = re.compile(r"[^0-9+\-*/]")
# A op B，两边最多 2 位。实测门户所有算式都是 1~2 位数：三位数几乎只可能是
# 把右侧「= ?」里的问号（形似 2）或干扰笔画当成了第三位，直接判非法比接受它划算。
_EXPR_RE = re.compile(r"(\d{1,2})([+\-*/])(\d{1,2})")


@dataclass
class MathCaptcha:
    """一次算式识别的结果。answer 为空表示这张图没认出来。"""

    raw: str = ""
    expr: str = ""
    answer: str = ""
    votes: int = 0
    # 按票数从高到低排的候选 (答案, 票数)。采集训练数据时一次提交多个候选，
    # 既提命中率，又能顺便验证门户的验证码是不是「提交一次就作废」。
    candidates: list[tuple[str, int]] = field(default_factory=list)

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
    for ch, rep in _LETTER_TO_DIGIT.items():
        text = text.replace(ch, rep)
    text = _STRIP_RE.sub("", text)
    # 真正的题目只有一个运算符；出现两个以上基本就是把干扰认成符号了，宁可不猜
    if sum(1 for ch in text if ch in "+-*/") != 1:
        return ""
    m = _EXPR_RE.search(text)
    if not m:
        return ""
    a, op, b = m.group(1), m.group(2), m.group(3)
    # 前导零（"02+3"）基本只会出现在「多认了一位」的时候，判非法
    if (len(a) > 1 and a[0] == "0") or (len(b) > 1 and b[0] == "0"):
        return ""
    return f"{a}{op}{b}"


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


# --------------------------------------------------------------------------- #
# cv2 空壳：ddddocr 的 slide_engine 在模块顶层无条件 import cv2，失败就抛 ImportError。
# 我们只用它的分类功能，打包时把 cv2 整个排除（省 112MB），这里补个空壳让它 import 得过。
# --------------------------------------------------------------------------- #
def _install_cv2_stub() -> None:
    import sys
    import types

    if sys.modules.get("cv2") is not None:
        return
    try:
        import cv2  # noqa: F401
        return
    except Exception:
        pass

    stub = types.ModuleType("cv2")

    def _missing(name):
        def _fail(*args, **kwargs):
            raise RuntimeError(f"cv2 空壳：{name} 不可用（本程序不需要 OpenCV）")

        return _fail

    def _getattr(name):
        return _missing(name)

    stub.__getattr__ = _getattr  # type: ignore[attr-defined]
    sys.modules["cv2"] = stub


# --------------------------------------------------------------------------- #
# 2x2 开运算（替代 cv2.morphologyEx，省掉 112MB 的 OpenCV）
# --------------------------------------------------------------------------- #
# cv2 对 2x2 核的默认 anchor 是 (1,1)：erode/dilate 都取「左上方」的 2x2 邻域。
# 边界处理与 cv2 一致：erode 补 255（背景），dilate 补 0（前景）。
def _erode2(m):
    import numpy as np

    p = np.pad(m, ((1, 0), (1, 0)), mode="constant", constant_values=255)
    return np.minimum(
        np.minimum(p[:-1, :-1], p[:-1, 1:]), np.minimum(p[1:, :-1], p[1:, 1:])
    )


def _dilate2(m):
    import numpy as np

    p = np.pad(m, ((1, 0), (1, 0)), mode="constant", constant_values=0)
    return np.maximum(
        np.maximum(p[:-1, :-1], p[:-1, 1:]), np.maximum(p[1:, :-1], p[1:, 1:])
    )


class MathCaptchaSolver:
    """算式验证码识别器：多变体 x 多模型投票。

    ddddocr 的模型（默认=common_old / beta=common）各自会错在不同的图上，
    同一张图的多种二值化变体也是各有各的错法，投票能把它们的大部分
    错误互相抵消掉。
    """

    #: 灰度阈值。用感知亮度而不是最大通道：字符是深色的蓝/紫/绿，干扰曲线是亮彩色，
    #: 亮度能把两者分开（最大通道不行，紫色的 B 通道照样很亮）。
    THRESHOLDS: tuple[int, ...] = (125, 140, 155, 170)
    #: 饱和度上限，滤掉一部分亮彩色曲线。
    SAT_MAX: int = 220
    #: 放大倍数。小图放大后模型准确率明显更高。
    #: 120 张真图实测：只跑 3x 时认出 67%；加上 2x 两档后 79%（≥5 票的图 33→48 张）。
    #: 2x 排在后，配合 AGE 早停：3x 这批票数已经够就直接返回，不为难图白烧时间。
    SCALES: tuple[int, ...] = (3, 2)

    #: 早停门槛：最先跑的缩放档凑够这么多票就直接返回，不再跑后面的档。
    EARLY_STOP_VOTES: int = 5
    #: 右侧裁切比例。最右边永远跟着「= ?」，而问号老被认成数字 2，
    #: 于是 «8乘6=?» 会变成 8*62。几个比例一起投，让票数决定。
    # 裁剪窗口右边界（占图宽比例）。这一刀是「保数字」与「别把问号框进来」之间的拉锯：
    #   · 切太左 → 最后一位数字被切掉半截，整行识别直接崩
    #   · 切太右（>0.70）→ 「=?」被框进来，问号被当成数字 2，八乘六变 8*62
    # 实测数字右边界在 130~150px 之间浮动，没有单一窗口能同时满足两边。
    # 试过整体右移到 (0.60,0.66,0.72) 与混合档 (0.54,0.64,0.72)：认出率能涨到
    # 77~83%，但服务端通过率反而 53%→44~48% —— 多认出来的基本是多认一位。
    # 所以维持原值，宁可认不出（换图不要钱）也不要认错（提交一次就废一张图）。
    RIGHT_CUTS: tuple[float, ...] = (0.54, 0.59, 0.64)

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
                    _install_cv2_stub()
                    import ddddocr

                    # 实测 ddddocr 的模型选择：old=True 与默认都加载 common_old.onnx，
                    # 两个引擎是同一个模型，纯属重复 —— 只留默认 + beta 两个。
                    # beta 模型（common.onnx）原本 51.6 MB，绝大多数体积压在输出层
                    # (8210,1024) 上 —— 那是 8210 个汉字/符号类别，我们永远用不到。
                    # 仓库里放的是裁到 72 类（字母数字 + 加减乘除等 + blank）的版本，
                    # 19.8 MB，识别结果与原模型逐位一致（tools/slim_onnx.py 可复现）。
                    slim_model = _resource("models/common_slim.onnx")
                    slim_charset = _resource("models/common_slim_charset.json")
                    slim_kwargs: dict = {"show_ad": False}
                    if slim_model.exists() and slim_charset.exists():
                        slim_kwargs["import_onnx_path"] = str(slim_model)
                        slim_kwargs["charsets_path"] = str(slim_charset)
                    else:  # pragma: no cover - 资源缺失时退回原模型
                        slim_kwargs["beta"] = True

                    built = []
                    for kwargs in (
                        {"show_ad": False},                    # common_old.onnx
                        slim_kwargs,                           # 裁剪版 common
                    ):
                        try:
                            built.append(ddddocr.DdddOcr(**kwargs))
                        except Exception:
                            # 老版本 ddddocr 没有 beta 参数，跳过就好
                            continue
                    if not built:  # pragma: no cover - 极端环境
                        raise RuntimeError("ddddocr 一个模型都没加载成功")
                    self._engines = built
        return self._engines

    # -- 预处理 -------------------------------------------------------------- #
    @staticmethod
    def _variants(png: bytes, scales: tuple[int, ...] | None = None) -> list[bytes]:
        """把原图切成若干「白底黑字」的识别候选。

        三步：按感知亮度和饱和度二值化 → （可选）开运算去细线 → 右侧裁掉「=?」、
        再裁到笔画包围盒并放大。裁包围盒很关键，等价于把字符放大到占满画面。
        """
        from PIL import Image
        import numpy as np

        try:
            src = Image.open(io.BytesIO(png)).convert("RGB")
        except Exception:
            return []
        rgb = np.array(src)
        # PIL 的 convert("L") 与 cv2.COLOR_RGB2GRAY 逐像素一致（ITU-R 601-2 整数版）
        gray = np.array(src.convert("L"))
        # cv2 的 HSV 饱和度：S = round(255*(max-min)/max)，max>0
        mx = rgb.max(axis=2).astype(np.int32)
        mn = rgb.min(axis=2).astype(np.int32)
        sat = np.where(
            mx > 0, np.rint((mx - mn) * 255.0 / np.maximum(mx, 1)), 0.0
        ).astype(np.uint8)
        width = rgb.shape[1]
        cuts = [0] + [int(width * f) for f in MathCaptchaSolver.RIGHT_CUTS]
        out: list[bytes] = []

        # 缩放放最外层：调用方可以让「先跑 3x、不够再跑 2x」，天然按批次切片
        use_scales = tuple(scales) if scales else MathCaptchaSolver.SCALES
        for scale in use_scales:
            for thresh in MathCaptchaSolver.THRESHOLDS:
                base = (
                    (gray < thresh) & (sat < MathCaptchaSolver.SAT_MAX)
                ).astype(np.uint8) * 255
                for use_open in (False, True):
                    mask = base
                    if use_open:
                        # 2x2 开运算剔掉圆点和 1px 细线，保留字符笔画
                        mask = _dilate2(_erode2(mask))
                    for cut in cuts:
                        sub = mask[:, :cut] if cut else mask
                        ys, xs = np.nonzero(sub)
                        if len(xs) == 0:
                            continue
                        pad = 3
                        sub = sub[
                            max(0, int(ys.min()) - pad) : int(ys.max()) + pad,
                            max(0, int(xs.min()) - pad) : int(xs.max()) + pad,
                        ]
                        im = Image.fromarray(255 - sub).convert("RGB")
                        big = im.resize(
                            (im.width * scale, im.height * scale), Image.LANCZOS
                        )
                        buf = io.BytesIO()
                        big.save(buf, "PNG")
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

    def _classify(self, variants: list[bytes], engines: list) -> list[str]:
        """每个变体都过所有模型，返回原始识别字符串。"""

        # 早先的做法是按 i % len(engines) 把变体「分」给各模型，省一半算力；
        # 但实测发现结果会随变体顺序漂移（同一批图上认出率 71% ~ 79% 浮动），
        # 因为两个模型对一个变体的判断本来就不一样，凭什么给它看是随机的。
        # 现在每个变体都让两个模型各看一遍：票数翻倍、结果与顺序无关，
        # 120 张实测认出率 71% -> 80%，代价是每张多 0.2 秒。
        def run_all(engine):
            out: list[str] = []
            for item in variants:
                try:
                    out.append(engine.classification(item))
                except Exception:
                    continue
            return out

        raws: list[str] = []
        if len(engines) > 1:
            with ThreadPoolExecutor(max_workers=len(engines)) as pool:
                for part in pool.map(run_all, engines):
                    raws.extend(part)
        else:
            raws.extend(run_all(engines[0]))
        return raws

    def _solve_locked(self, png: bytes) -> MathCaptcha:
        engines = self._ensure()
        votes: dict[str, list[tuple[str, str]]] = {}
        stats = {"recognized": 0, "parsed": 0}

        # 按缩放档分批：先跑 3x（也就是原先全部的那批），票数已经够就直接返回；
        # 不够再补 2x —— 难图多花时间，容易的图不被拖慢。
        for scale in (MathCaptchaSolver.SCALES or (3,)):
            variants = self._variants(png, (scale,))
            if not variants:
                continue
            for raw in self._classify(variants, engines):
                stats["recognized"] += 1
                expr = normalize_expr(raw)
                if not expr:
                    continue
                answer = compute(expr)
                if not answer:
                    continue
                stats["parsed"] += 1
                votes.setdefault(answer, []).append((expr, raw))
            if votes and max(len(v) for v in votes.values()) >= self.EARLY_STOP_VOTES:
                break

        self.last_stats = stats
        if not votes:
            return MathCaptcha()

        answer, hits = max(votes.items(), key=lambda kv: len(kv[1]))
        expr, raw = hits[0]
        ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))
        return MathCaptcha(
            raw=raw,
            expr=expr,
            answer=answer,
            votes=len(hits),
            candidates=[(ans, len(h)) for ans, h in ranked],
        )


# --------------------------------------------------------------------------- #
# 默认实例
# --------------------------------------------------------------------------- #
_default: Optional[Any] = None
_default_lock = threading.Lock()
_default_math: Optional[Any] = None
_default_math_lock = threading.Lock()


def get_solver():
    """4 位字母数字验证码：自训 CRNN（int8 4 MB，150 张人工真值 96.0%）。

    已彻底脱离 ddddocr：原来的 common.onnx（51 MB raw / 6.46 MB 打包）在
    同一批人工真值上只有 71.3%，体积还大 60%。
    """
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                from .crnn4_ocr import Crnn4Solver

                _default = Crnn4Solver()
    return _default


def get_math_solver():
    """算式题识别器：CRNN（ONNX，单图 ~4 ms）。

    ddddocr 那套 72 类字符集里没有 "÷" 字形，除法题在真图上 30 张全错；
    CRNN 直接在 21 类（含 ×÷加减乘除）上分类，除法能到 80%。
    加载失败直接抛错 —— ddddocr 已经从打包里移除了，没有回退可退。
    """
    global _default_math
    if _default_math is None:
        with _default_math_lock:
            if _default_math is None:
                from .crnn_ocr import CrnnSolver

                crnn = CrnnSolver()
                if not crnn.warmup():
                    raise RuntimeError(f"算式 CRNN 加载失败：{crnn.last_error}")
                _default_math = crnn
    return _default_math
