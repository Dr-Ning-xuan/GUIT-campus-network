"""打包后产物级验证。

两个坑都得防：
  1. --windowed 的 exe 在 Windows 上没有 stdout，自检结果要读它落的 _selftest.log；
  2. onefile 的 exe 启动时把自身解压到 TEMP。实测本机 DSH 会话下，
     只有 workspace(D:\\DSH) 内的目录能成功创建带安全描述符的临时目录，
     否则 bootloader 会**静默**退 -1（Python 一行都没跑）。所以验证时
     显式把 TEMP 指到项目内的短路径。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHORT_TMP = ROOT / "build" / "vtemp"      # workspace 内，实测可解压
NAME = "GUIT校园网一键联网"


def find_exe() -> Path | None:
    """只认本次构建的产物。

    以前这里优先找 dist-onedir，而那个目录是历史遗留的旧构建（768 MB，
    早就不更新了），结果每次验证其实都在校验一个几周前的 exe，全绿也是假的。
    """
    onedir = os.environ.get("GUIT_ONEDIR") == "1"
    ordered = (
        (ROOT / "dist" / NAME / f"{NAME}.exe", ROOT / "dist" / f"{NAME}.exe")
        if onedir
        else (ROOT / "dist" / f"{NAME}.exe", ROOT / "dist" / NAME / f"{NAME}.exe")
    )
    for cand in ordered:
        if cand.exists():
            return cand
    base = ROOT / "dist"
    if base.exists():
        found = sorted(base.rglob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if found:
            return found[0]
    return None


def main() -> int:
    exe = find_exe()
    if not exe:
        print("[FAIL] dist/dist-onedir 下没找到 exe")
        return 1

    files = [p for p in exe.parent.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"[INFO] exe   = {exe}")
    print(f"[INFO] mode  = {'onedir' if exe.parent.name == NAME else 'onefile'}")
    print(f"[INFO] size  = {exe.stat().st_size / 1024 / 1024:.1f} MB"
          f"{f'  (+{len(files) - 1} 个依赖文件, 合计 {total / 1024 / 1024:.1f} MB)' if len(files) > 1 else ''}")

    SHORT_TMP.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["TEMP"] = str(SHORT_TMP)
    env["TMP"] = str(SHORT_TMP)
    print(f"[INFO] TEMP -> {SHORT_TMP} (len={len(str(SHORT_TMP))})")

    log = exe.parent / "_selftest.log"
    if log.exists():
        log.unlink()

    print("[INFO] running --selftest ...")
    t0 = time.time()
    proc = subprocess.run([str(exe), "--selftest"], capture_output=True, text=True, timeout=600, env=env)
    dt = time.time() - t0
    print(f"[INFO] 用时 {dt:.1f}s，退出码 {proc.returncode}")

    text = ""
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
    else:
        text = (proc.stdout or "") + (proc.stderr or "")

    print("---- _selftest.log ----")
    print(text.strip()[:4000])
    print("-----------------------")

    if proc.returncode != 0:
        print(f"[FAIL] 退出码 {proc.returncode}")
        print("       提示：onefile 模式下若 TEMP 不可写（无 WRITE_DAC）会静默退 -1")
        return 1
    if "[SELFTEST OK]" not in text:
        print("[FAIL] 没看到 [SELFTEST OK]")
        return 1
    print("[OK] 产物级验证通过（exe 真的能 import 依赖并识别验证码）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
