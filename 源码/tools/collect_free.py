"""免费采集：只抓图 + 识别，票数够就存，一次都不提交。

门户每 GET 一次 /ACCaptcha.ashx 就给一张全新图，既没有成本也没有风控风险。
用识别器自己的一致性当可信度：459 次真实提交的统计里，
票 8~15 的答案 84% 正确，票 4~7 的 57%，票 1 的只有 19%。

所以这里只收高票样本，标签是用「多个变体 × 两个模型」投票投出来的，
噪声比提交确认的样本大，但量能拉开两个数量级，对 CTC 训练仍然是好事。

输出与 collect_train.py 同格式，直接追加到 _train/：
    images/NNNNN.png
    labels.jsonl   {file, expr, answer, rank, votes, src}

用法：
    python tools/collect_free.py --count 5120 --min-votes 8
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import get_math_solver  # noqa: E402
from src.portal import CAPTCHA_LOGIN_ACTION, PortalClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5120, help="目标条数")
    ap.add_argument("--min-votes", type=int, default=8, help="票数门槛")
    ap.add_argument("--max-fetch", type=int, default=60000, help="最多抓多少张图")
    ap.add_argument("--base", default="http://10.6.6.6")
    ap.add_argument("--out", default="_train")
    ap.add_argument("--sleep", type=float, default=0.0, help="抓图间隔（秒）")
    args = ap.parse_args()

    out = ROOT / args.out
    (out / "images").mkdir(parents=True, exist_ok=True)
    f_labels = (out / "labels.jsonl").open("a", encoding="utf-8")

    # 接着已有编号往下写，不和 collect_train.py 的样本撞名
    got = 0
    lab = out / "labels.jsonl"
    if lab.exists():
        with lab.open("r", encoding="utf-8") as f:
            got = sum(1 for line in f if line.strip())
        print(f"[INFO] 已有 {got} 条，继续往上加", flush=True)

    solver = get_math_solver()
    if not solver.warmup():
        print(f"[FAIL] 识别引擎加载失败：{solver.last_error}")
        return 1

    cli = PortalClient(args.base)
    cli._get("/login")

    start_got = got
    fetches = 0
    t0 = time.time()
    last_report = t0
    while got < args.count and fetches < args.max_fetch:
        try:
            png = cli.fetch_captcha(CAPTCHA_LOGIN_ACTION)
        except Exception as exc:
            print(f"[WARN] 抓图失败：{exc}", flush=True)
            time.sleep(2.0)
            continue
        fetches += 1
        cap = solver.solve(png)
        if cap.ok and cap.votes >= args.min_votes:
            name = f"{got:05d}.png"
            (out / "images" / name).write_bytes(png)
            f_labels.write(json.dumps({
                "file": name,
                "expr": cap.expr,
                "answer": cap.answer,
                "rank": 1,
                "votes": cap.votes,
                "src": "free",
            }, ensure_ascii=False) + "\n")
            f_labels.flush()
            got += 1
            if (got - start_got) and (got - start_got) % 50 == 0:
                now = time.time()
                span = (got - start_got)
                per = (now - t0) / span
                remain = (args.count - got) * per / 60
                print(
                    f"[进度] {got}/{args.count}  本次新增 {span} 条 / 抓图 {fetches}  "
                    f"命中率 {span / max(fetches, 1) * 100:.1f}%  "
                    f"每条 {per:.2f}s  预计还要 {remain:.0f} 分钟",
                    flush=True,
                )
        if args.sleep:
            time.sleep(args.sleep)

    f_labels.close()
    dt = time.time() - t0
    print()
    added = got - start_got
    print(f"[RESULT] 新增 {added} 条，抓图 {fetches} 张，用时 {dt / 60:.1f} 分钟")
    print(f"[RESULT] 命中率 {added / max(fetches, 1) * 100:.1f}%（门槛 ≥{args.min_votes} 票）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
