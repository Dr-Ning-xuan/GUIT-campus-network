"""验证 src/crnn.py 的 numpy 前向与 PyTorch 前向数值一致。

训练端带 BatchNorm，导出时已经把 BN 折进卷积权重，所以这里用一份
「无 BN 的等价模型」加载 npz 作参照。任何对齐错位（padding、池化取整、
维度顺序）都会让 logits 整片跑偏，而这在准确率上只表现为「模型没用」，极难反查。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.crnn import H, W, CrnnSolver  # noqa: E402

NUM_CLASS = 15


class PlainCRNN(nn.Module):
    """与训练端同结构，但没有 BN —— 权重是 BN 融合之后的。"""

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(128, 128, 3, padding=1), nn.ReLU(),
        )
        self.fc = nn.Linear(128 * (H // 8), NUM_CLASS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        f = self.features(x)
        n, c, h, w = f.shape
        f = f.permute(0, 3, 1, 2).reshape(n, w, c * h)
        return self.fc(f)


def main() -> int:
    npz_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models/crnn.npz"
    if not npz_path.is_absolute():
        npz_path = ROOT / npz_path
    if not npz_path.exists():
        print(f"[FAIL] 没有 {npz_path}")
        return 1

    with np.load(npz_path) as z:
        sd = {
            "features.0.weight": torch.from_numpy(np.asarray(z["conv1_w"])),
            "features.0.bias": torch.from_numpy(np.asarray(z["conv1_b"])),
            "features.3.weight": torch.from_numpy(np.asarray(z["conv2_w"])),
            "features.3.bias": torch.from_numpy(np.asarray(z["conv2_b"])),
            "features.6.weight": torch.from_numpy(np.asarray(z["conv3_w"])),
            "features.6.bias": torch.from_numpy(np.asarray(z["conv3_b"])),
            "features.9.weight": torch.from_numpy(np.asarray(z["conv4_w"])),
            "features.9.bias": torch.from_numpy(np.asarray(z["conv4_b"])),
            "fc.weight": torch.from_numpy(np.asarray(z["fc_w"])),
            "fc.bias": torch.from_numpy(np.asarray(z["fc_b"])),
        }
    model = PlainCRNN()
    model.load_state_dict(sd)
    model.eval()

    solver = CrnnSolver(npz_path)
    if not solver.ready:
        print(f"[FAIL] 加载失败：{solver.last_error}")
        return 1

    rng = np.random.default_rng(0)
    worst = 0.0
    for trial in range(5):
        gray = rng.random((H, W)).astype(np.float32)
        with torch.no_grad():
            ref = model(torch.from_numpy(gray)[None, None]).numpy()[0]
        got = solver.infer(gray)
        diff = float(np.abs(ref - got).max())
        worst = max(worst, diff)
        print(f"[{trial}] 形状 {ref.shape} vs {got.shape}  最大差 {diff:.3e}")

    ok = worst < 1e-3
    print(f"[RESULT] 最大偏差 {worst:.3e} -> {'一致' if ok else '不一致，前向没对齐'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
