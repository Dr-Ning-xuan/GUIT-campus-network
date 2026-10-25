"""训练纯卷积 CTC 模型来识别算式验证码。

设计上刻意只用 Conv2d / ReLU / MaxPool2d / Linear 四种层 —— 没有任何
需要特殊算子才能复刻的东西，训练完导出成 npz，运行时用纯 numpy 做前向，
不需要 onnxruntime（这一块就省掉 37 MB）。

为什么用 CTC 而不是「先切字再分类」：
    连通域分割实测切不齐（块数分布 2~8 个都有），而 CTC 根本不需要分割，
    滑窗 + 合并重复就能直接吐出 "55+9"。

用法：
    python tools/train_crnn.py --data _train --epochs 120
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHARSET = "0123456789+-*/"
BLANK = len(CHARSET)
NUM_CLASS = BLANK + 1

# 尺寸和预处理必须与 src/crnn.py 完全一致，否则训练/推理对不上
from src.crnn import H, W, prepare  # noqa: E402

# 验证码干扰线的深浅粗细是随机的，同一张图换个阈值就相当于一张新样本，
# 训练时随机抖动阈值 = 免费扩了 4 倍数据。
THRESH_CHOICES = (125, 140, 155, 170)


def _block(cin: int, cout: int) -> list[nn.Module]:
    """Conv + BN + ReLU。BN 只为了让训练收敛快，导出时会融进卷积里。"""
    return [
        nn.Conv2d(cin, cout, 3, padding=1),
        nn.BatchNorm2d(cout),
        nn.ReLU(),
    ]


class CRNN(nn.Module):
    """(N,1,48,160) -> (N,20,15) 的 logits。

    通道数取 32/64/128/128，约 25 万参数（FP32 约 1 MB）。
    之前 16/32/64/64 只有 6.6 万参数，字符准确率卡在 35% 上不去——
    小模型在这么重的干扰下容量不够，加宽后才有余地。
    """

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            *_block(1, 32), nn.MaxPool2d(2, 2),      # (32,24,80)
            *_block(32, 64), nn.MaxPool2d(2, 2),     # (64,12,40)
            *_block(64, 128), nn.MaxPool2d(2, 2),    # (128,6,20)
            *_block(128, 128),                       # (128,6,20)
        )
        self.fc = nn.Linear(128 * (H // 8), NUM_CLASS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        f = self.features(x)                       # (N, 128, 6, 20)
        n, c, h, w = f.shape
        f = f.permute(0, 3, 1, 2).reshape(n, w, c * h)   # (N, W, C*H)
        return self.fc(f)                          # (N, W, NUM_CLASS)


def open_image(path: Path) -> Image.Image:
    """读成已解码的 RGB 并常驻内存，避免训练循环里反复读盘。"""
    return Image.open(path).convert("RGB")


def load_image(path: Path) -> np.ndarray:
    """走与推理端完全相同的预处理（二值化 + 开运算 + 缩放 + 反色）。"""
    return prepare(Image.open(path))                          # (H, W) 0~1


def augment(a: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """轻度增强：亮度/对比度抖动 + 水平平移。干扰本来就很重，别加太狠。"""
    out = a.copy()
    out = out * float(rng.uniform(0.85, 1.15)) + float(rng.uniform(-0.06, 0.06))
    out = np.clip(out, 0.0, 1.0)
    shift = int(rng.integers(-4, 5))
    if shift:
        out = np.roll(out, shift, axis=1)
    return out


def encode(label: str) -> list[int]:
    return [CHARSET.index(ch) for ch in label if ch in CHARSET]


def decode(ids) -> str:
    out: list[str] = []
    prev = None
    for i in ids:
        i = int(i)
        if i != prev and i != BLANK:
            out.append(CHARSET[i])
        prev = i
    return "".join(out)


def load_dataset(data_dir: Path):
    labels_path = data_dir / "labels.jsonl"
    if not labels_path.exists():
        raise SystemExit(f"没有找到 {labels_path}")
    items = []
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        img_path = data_dir / "images" / rec["file"]
        if not img_path.exists():
            continue
        target = encode(rec["expr"])
        if not target:
            continue
        items.append((open_image(img_path), target, rec["expr"]))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="_train")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--out", default="models/crnn.npz")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)

    data_dir = ROOT / args.data
    items = load_dataset(data_dir)
    if len(items) < 40:
        print(f"[FAIL] 样本太少（{len(items)} 条），至少 40 条才能训")
        return 1
    print(f"[INFO] 样本 {len(items)} 条")

    # 存已解码的 PIL 图，训练时再按随机阈值渲染
    cache = [(im, t) for im, t, _ in items]
    random.shuffle(cache)

    n_val = max(8, len(cache) // 5)
    val_set, train_set = cache[:n_val], cache[n_val:]
    print(f"[INFO] 训练 {len(train_set)} / 验证 {len(val_set)}")

    # 有显卡就走 CUDA：之前在 CPU 上跑 200 轮要几十分钟，纯属浪费。
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        print(f"[INFO] 设备 {device} / {torch.cuda.get_device_name(0)}")
    else:
        print(f"[INFO] 设备 {device}（没检测到可用的 CUDA）")

    model = CRNN().to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"[INFO] 参数量 {n_params:,}（FP32 约 {n_params * 4 / 1024:.0f} KB）")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    ctc = nn.CTCLoss(blank=BLANK, zero_infinity=True)

    def evaluate() -> tuple[float, float]:
        model.eval()
        exact = 0
        char_ok = char_tot = 0
        with torch.no_grad():
            for i in range(0, len(val_set), args.batch):
                chunk = val_set[i:i + args.batch]
                x = torch.from_numpy(
                    np.stack([prepare(c[0]) for c in chunk])
                )[:, None].to(device)
                logits = model(x)                       # (N,T,C)
                for row, (_, target) in zip(logits.argmax(2).cpu(), chunk):
                    got = decode(row.tolist())
                    want = "".join(CHARSET[t] for t in target)
                    exact += int(got == want)
                    char_tot += max(len(want), len(got))
                    char_ok += sum(1 for a, b in zip(got, want) if a == b)
        model.train()
        return exact / max(len(val_set), 1), char_ok / max(char_tot, 1)

    best = 0.0
    best_state = None
    for epoch in range(1, args.epochs + 1):
        order = list(range(len(train_set)))
        random.shuffle(order)
        total_loss = 0.0
        for start in range(0, len(order), args.batch):
            batch = [train_set[j] for j in order[start:start + args.batch]]
            imgs = np.stack([
                augment(prepare(c[0], int(rng.choice(THRESH_CHOICES))), rng)
                for c in batch
            ])[:, None]
            x = torch.from_numpy(imgs.astype(np.float32)).to(device)
            logits = model(x)                           # (N,T,C)
            logp = logits.log_softmax(2).permute(1, 0, 2)   # (T,N,C)
            input_lengths = torch.full(
                (len(batch),), logits.shape[1], dtype=torch.long, device=device
            )
            # CTC 要求把不同长度的标签首尾相接拼成一维，长度另给一份
            targets = torch.tensor(
                [t for c in batch for t in c[1]], dtype=torch.long, device=device
            )
            target_lengths = torch.tensor(
                [len(c[1]) for c in batch], dtype=torch.long, device=device
            )
            loss = ctc(logp, targets, input_lengths, target_lengths)
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            total_loss += float(loss.item())
        sched.step()

        if epoch % 10 == 0 or epoch == args.epochs:
            exact, char_acc = evaluate()
            print(f"[{epoch:3d}] loss={total_loss / max(len(order), 1):.4f}  "
                  f"整题={exact * 100:.1f}%  字符={char_acc * 100:.1f}%", flush=True)
            if exact >= best:
                best = exact
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    exact, char_acc = evaluate()
    print(f"[RESULT] 最佳验证：整题 {exact * 100:.1f}%，字符 {char_acc * 100:.1f}%")

    # 导出前先切 eval，running stats 才是稳定的
    model.eval()

    def fuse(conv_idx: int) -> tuple[np.ndarray, np.ndarray]:
        """把 BN 折进卷积：y = sum(w*x) * s + (beta - mean*s)，s = gamma/sqrt(var+eps)。

        这样导出的权重就是一个普通卷积，numpy 前向里不需要再实现 BN，
        训练时享受 BN 的收敛速度，推理端一点复杂度都不加。
        """
        conv = model.features[conv_idx]
        bn = model.features[conv_idx + 1]
        scale = (bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach()
        # 权重可能躺在显卡上，转 numpy 前必须先回内存
        w = (conv.weight.detach() * scale[:, None, None, None]).cpu().numpy()
        b = (
            bn.bias.detach()
            - bn.running_mean.detach() * scale
            + conv.bias.detach() * scale
        ).cpu().numpy()
        return w.astype(np.float32), b.astype(np.float32)

    w1, b1 = fuse(0)
    w2, b2 = fuse(4)
    w3, b3 = fuse(8)
    w4, b4 = fuse(12)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out_path,
        conv1_w=w1, conv1_b=b1, conv2_w=w2, conv2_b=b2,
        conv3_w=w3, conv3_b=b3, conv4_w=w4, conv4_b=b4,
        fc_w=model.fc.weight.detach().numpy().astype(np.float32),
        fc_b=model.fc.bias.detach().numpy().astype(np.float32),
        charset=np.array(list(CHARSET)),
    )
    size_kb = out_path.stat().st_size / 1024
    print(f"[RESULT] 已导出 {out_path}（{size_kb:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
