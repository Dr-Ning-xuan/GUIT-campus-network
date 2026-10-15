"""给主窗口拍张照，用于人工确认界面效果。

用法: python tools/screenshot_gui.py [输出png]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.gui import MainWindow  # noqa: E402
from src.ocr import get_solver  # noqa: E402
from src.store import Config  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (ROOT / "build" / "gui-shot.png")
    force_offline = len(sys.argv) > 2 and sys.argv[2] == "offline"
    out.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    cfg = Config()
    cfg.gui_id = "2024012345"
    cfg.account = "13800138000"
    cfg.password = "examplepw"
    cfg.auto_reconnect = True
    cfg.poll_seconds = 15

    win = MainWindow(cfg, get_solver(), start_hidden=False)
    win.resize(560, 700)
    win.cb_show.setChecked(False)          # 别把密码明文画进截图
    win.ed_pwd.setEchoMode(win.ed_pwd.EchoMode.Password)
    win.show()

    def shoot() -> None:
        if force_offline:
            win.set_state(False, "未联网")
            QTimer.singleShot(700, lambda: (win.grab().save(str(out)), print(f"[OK] {out}"), app.quit()))
            return
        win.grab().save(str(out))
        print(f"[OK] {out}")
        app.quit()

    QTimer.singleShot(9000, shoot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
