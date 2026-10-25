"""采集训练数据：抓图 → 识别 → 依次提交多个候选 → 只留服务端确认的样本。

副产品（很重要）：顺手回答「验证码是不是提交一次就作废」。
对同一张图不重新抓图、直接提交第二个候选，如果它能通过，就说明不作废，
那么「多候选提交」可以直接把单次命中率抬上去。

用法：
    python tools/collect_train.py --count 300 --max-submit 1200 --top 3

输出（默认 _train/，已 gitignore）：
    images/00000.png      —— 只保存服务端确认过的图
    labels.jsonl          —— {file, expr, answer, rank, votes}
    submits.jsonl         —— 每一次提交的完整记录，用来分析作废/风控
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
from src.portal import PortalClient  # noqa: E402

# 假账号：只用来验验证码，不存在、也不会动到任何真实账号
PROBE_GUI_ID = "2024012345"
PROBE_ACCOUNT = "13800138000"
PROBE_PASSWORD = "abcdefg1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=300, help="目标真值条数")
    ap.add_argument("--max-submit", type=int, default=1200, help="提交次数上限")
    ap.add_argument("--top", type=int, default=3, help="每张图最多提交几个候选")
    ap.add_argument("--sleep", type=float, default=0.5, help="每次提交后歇多久")
    ap.add_argument("--base", default="http://10.6.6.6")
    ap.add_argument("--out", default="_train")
    ap.add_argument("--dry-run", action="store_true", help="只识别不提交，看候选分布")
    args = ap.parse_args()

    out = ROOT / args.out
    (out / "images").mkdir(parents=True, exist_ok=True)
    f_labels = (out / "labels.jsonl").open("a", encoding="utf-8")
    f_submits = (out / "submits.jsonl").open("a", encoding="utf-8")

    solver = get_math_solver()
    if not solver.warmup():
        print(f"[FAIL] 识别引擎加载失败：{solver.last_error}")
        return 1

    cli = PortalClient(args.base)
    cli._get("/login")

    # 接着已有的样本往下数，避免覆盖前面采到的图
    got = 0
    lab_path = out / "labels.jsonl"
    if lab_path.exists():
        with lab_path.open("r", encoding="utf-8") as f:
            got = sum(1 for line in f if line.strip())
        print(f"[INFO] 已有 {got} 条，继续往上加", flush=True)
    n_submit = blanks = 0
    multi_ok = 0
    rank_hist: dict[int, int] = {}
    t0 = time.time()

    while got < args.count and n_submit < args.max_submit:
        try:
            form = cli.prepare_login()
        except Exception as exc:
            print(f"[WARN] 取图失败：{exc}", flush=True)
            time.sleep(2.0)
            continue
        png = form.captcha_png
        cap = solver.solve(png)
        if not cap.ok:
            blanks += 1
            if args.dry_run and blanks % 10 == 0:
                print(f"  (空图 {blanks})", flush=True)
            continue

        if args.dry_run:
            print(f"  {cap.expr} = {cap.answer}  候选={cap.candidates[:args.top]}", flush=True)
            got += 1
            time.sleep(0.3)
            continue

        cands = cap.candidates[: args.top] or [(cap.answer, cap.votes)]
        seq = got
        rank_hit = 0
        for rank, (ans, votes) in enumerate(cands, 1):
            if n_submit >= args.max_submit:
                break
            try:
                outcome = cli.submit_login(
                    form, PROBE_GUI_ID, PROBE_ACCOUNT, PROBE_PASSWORD, ans, "自动选择"
                )
            except Exception as exc:
                print(f"[WARN] 提交失败：{exc}", flush=True)
                break
            msg = outcome.message
            n_submit += 1
            ok = "验证码" not in msg
            f_submits.write(json.dumps({
                "ts": round(time.time(), 3),
                "seq": seq,
                "rank": rank,
                "answer": ans,
                "votes": votes,
                "expr": cap.expr,
                "msg": msg[:200],
                "ok": ok,
            }, ensure_ascii=False) + "\n")
            f_submits.flush()
            if ok:
                name = f"{seq:05d}.png"
                (out / "images" / name).write_bytes(png)
                f_labels.write(json.dumps({
                    "file": name,
                    "expr": cap.expr,
                    "answer": ans,
                    "rank": rank,
                    "votes": votes,
                }, ensure_ascii=False) + "\n")
                f_labels.flush()
                got += 1
                rank_hit = rank
                rank_hist[rank] = rank_hist.get(rank, 0) + 1
                if rank > 1:
                    multi_ok += 1
                break
            time.sleep(args.sleep)

        if rank_hit:
            print(f"  +1  #{got:03d}  {cap.expr} = {ans}  (第 {rank_hit} 候选)", flush=True)
        if n_submit and n_submit % 25 < len(cands):
            print(f"[进度] 真值 {got}/{args.count}  提交 {n_submit}/{args.max_submit}  空图 {blanks}", flush=True)
        time.sleep(args.sleep)

    f_labels.close()
    f_submits.close()
    dt = time.time() - t0
    print()
    print(f"[RESULT] 真值 {got} 条 / 提交 {n_submit} 次 / 空图 {blanks} / 用时 {dt:.0f}s")
    print(f"[RESULT] 提交成功率 {got / max(n_submit, 1) * 100:.0f}%")
    print(f"[RESULT] 命中排位分布 {rank_hist}")
    if multi_ok:
        print(f"[结论] 第 2 及以后候选通过 {multi_ok} 次 -> 验证码【不是】一次性的，多候选提交可行")
    elif n_submit:
        print("[结论] 暂无第 2 候选通过的证据，验证码可能是一次性的")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
