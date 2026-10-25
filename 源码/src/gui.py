"""PySide6 界面：主窗口 + 托盘常驻 + 后台任务。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QAction,
    QColor,
    QCloseEvent,
    QIcon,
    QMovie,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import autostart
from .ocr import CaptchaSolver, MathCaptcha, MathCaptchaSolver, get_math_solver
from .service import (
    AutoReconnector,
    LoginConfig,
    attempt_login,
    check_status,
    do_logoff,
    prepare_manual_login,
    submit_manual_login,
)
from .store import Config, config_path
from .wakeup import WakeupServer

from src.version import __version__ as APP_VERSION

APP_TITLE = f"GUIT校园网一键联网 v{APP_VERSION}"
GREEN = "#22c55e"
RED = "#ef4444"
GRAY = "#94a3b8"


# --------------------------------------------------------------------------- #
# 图标
# --------------------------------------------------------------------------- #
def _asset(name: str) -> Optional[Path]:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for cand in (base / "assets" / name, Path(__file__).resolve().parent.parent / "assets" / name):
        if cand.exists():
            return cand
    return None


def _base_pixmap(size: int = 64) -> QPixmap:
    ico = _asset("icon.ico")
    if ico:
        pm = QIcon(str(ico)).pixmap(size, size)
        if not pm.isNull():
            return pm
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1e3a8a"))
    p.drawEllipse(size * 0.08, size * 0.28, size * 0.84, size * 0.68)
    p.setBrush(QColor("#f97316"))
    p.drawPolygon(*[QPoint(int(size * x), int(size * y)) for x, y in ((0.14, 0.46), (0.30, 0.06), (0.50, 0.40))])
    p.end()
    return pm


SPEEDTEST_URL = "https://test.ustc.edu.cn/"
#: 项目主页 / 更新地址（界面右下角的「更新地址」按钮指向这里）
UPDATE_URL = "https://github.com/Dr-Ning-xuan/GUIT-campus-network"

_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _find_browser() -> Optional[str]:
    """找一个能开 --app 模式的 Chromium 浏览器（Edge 几乎每台 Windows 都有）。"""
    for c in _BROWSER_CANDIDATES:
        if os.path.isfile(c):
            return c
    return None


def app_icon(online: Optional[bool] = None) -> QIcon:
    """应用图标 = assets/icon.ico，传状态时右下角叠一颗绿/红点。

    多尺寸一起塞进 QIcon，任务栏（32/48）和托盘（16）才不会糊。
    """
    ico = _asset("icon.ico")
    base = QIcon(str(ico)) if ico else QIcon(_base_pixmap(64))
    if online is None:
        return base

    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = base.pixmap(s, s)
        if pm.isNull():
            continue
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        w = s * 0.38
        p.setPen(QPen(QColor("#ffffff"), max(1.0, s * 0.07)))
        p.setBrush(QColor(GREEN if online else RED))
        p.drawEllipse(int(s - w - s * 0.04), int(s - w - s * 0.04), int(w), int(w))
        p.end()
        icon.addPixmap(pm)
    return icon if not icon.isNull() else base


# --------------------------------------------------------------------------- #
# 后台任务
# --------------------------------------------------------------------------- #
class TaskThread(QThread):
    """在后台线程里跑一次动作，避免卡界面。"""

    log = Signal(str)
    captcha = Signal(bytes, str)
    done = Signal(bool, str)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:  # noqa: D102
        try:
            ok, msg = self._fn(self.log.emit, self.captcha.emit)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"内部异常：{exc}"
        if not self._cancel:
            self.done.emit(bool(ok), str(msg))


class Bridge(QObject):
    """把非 Qt 线程（自动重连线程）的事件转成 Qt 信号。"""

    log = Signal(str)
    state = Signal(bool, str)


class _ManualCaptchaDialog(QDialog):
    """自动识别失败时的兜底：把算式图摆出来，让主人自己填结果。"""

    def __init__(self, png: bytes, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("手动输入验证码")
        self.setMinimumWidth(360)

        lay = QVBoxLayout(self)
        tip = QLabel("自动识别没认出这道算式。看一眼图，把结果填进下面。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        pix = QPixmap()
        if png:
            pix.loadFromData(png)
            if not pix.isNull():
                img.setPixmap(
                    pix.scaled(QSize(320, 96), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        img.setMinimumHeight(96)
        img.setStyleSheet("background:#ffffff;border:1px solid #cbd5e1;border-radius:6px;")
        lay.addWidget(img)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("算式结果，例如 66")
        self.edit.setMaxLength(4)
        lay.addWidget(self.edit)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.edit.returnPressed.connect(self.accept)

    def value(self) -> str:
        return self.edit.text().strip()


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(
        self,
        cfg: Config,
        solver: CaptchaSolver,
        math_solver: Optional[MathCaptchaSolver] = None,
        start_hidden: bool = False,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.solver = solver                       # 四位字母数字（注销/查询用）
        self.math_solver = math_solver or get_math_solver()   # 算式（登录用）
        self.task: Optional[TaskThread] = None
        self.bridge = Bridge()
        self.reconnector: Optional[AutoReconnector] = None
        self._quitting = False

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(640, 700)
        self.setMinimumWidth(600)

        self._build_ui()
        self._wire()

        self.reconnector = AutoReconnector(
            get_cfg=self._login_config,
            solver=self.math_solver,
            interval=lambda: self.spin_poll.value(),
            log=self.bridge.log.emit,
            on_state=self.bridge.state.emit,
        )

        # 常驻监听：别的实例双击时敲一下这个事件，我们就自己把窗口亮出来
        self._wake = WakeupServer()
        self._wake.create()
        self._wake_timer = QTimer(self)
        self._wake_timer.timeout.connect(self._poll_wake)
        self._wake_timer.start(250)

        # 预热识别引擎（不占界面）
        self._warm = TaskThread(
            lambda l, c: (self.math_solver.warmup() or True, "识别引擎就绪")
        )
        self._warm.done.connect(lambda ok, msg: self.log("· 验证码识别引擎已就绪"))
        self._warm.log.connect(lambda m: None)
        self._warm.start()

        self._set_badge("offline")   # 开局先委屈着，查到在线再变得意

        # 自启项如果还指着单文件版，顺手纠偏到文件夹版（秒开，不会让人以为没生效）
        try:
            if autostart.is_enabled() and autostart.is_stale():
                autostart.enable(True)
                self._pending_note = "· 已把开机自启改指向文件夹版（启动更快）"
        except Exception:
            pass
        self.tray.show()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QTimer.singleShot(
                800,
                lambda: self.log("× 系统托盘不可用，关闭窗口后会直接退出（不是收进托盘）"),
            )
        if not start_hidden:
            self.show()
        else:
            self.tray.showMessage(APP_TITLE, "已最小化到托盘运行", QSystemTrayIcon.Information, 2500)

        QTimer.singleShot(400, self._initial_state_check)

    # ---- UI ---------------------------------------------------------------- #
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 状态控件：不单独占一行了，稍后放进「运行选项」右侧那个立绘框里
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color:{GRAY};font-size:24px;")
        self.lbl_state = QLabel("未知")
        self.lbl_state.setStyleSheet("font-size:22px;font-weight:800;color:#0f172a;")
        self.lbl_ip = QLabel("--")
        self.lbl_ip.setStyleSheet("color:#475569;font-size:13px;")
        self.btn_refresh = QPushButton("刷新状态")

        # 账号表单
        box = QGroupBox("账号信息")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)
        # 长度上限与门户表单一致（实测服务端会拦：密码长度应大于0小于17!）
        self.ed_guiid = QLineEdit(self.cfg.gui_id)
        self.ed_guiid.setMaxLength(12)
        self.ed_acc = QLineEdit(self.cfg.account)
        self.ed_acc.setMaxLength(12)
        self.ed_pwd = QLineEdit(self.cfg.password)
        self.ed_pwd.setMaxLength(16)
        self.ed_pwd.setEchoMode(QLineEdit.Password)
        self.cb_show = QCheckBox("显示")
        row_pwd = QHBoxLayout()
        row_pwd.addWidget(self.ed_pwd)
        row_pwd.addWidget(self.cb_show)
        self.cb_isp = QComboBox()
        self.cb_isp.addItems(["自动选择", "中国电信", "中国联通", "中国移动"])
        self.cb_isp.setCurrentText(self.cfg.isp if self.cfg.isp else "自动选择")
        self.cb_isp.setEnabled(False)  # 门户登录页该字段只读，这里只作展示
        self.chk_save = QCheckBox("保存账号密码")
        self.chk_save.setChecked(self.cfg.save_credentials)
        self.chk_save.setToolTip("勾选后写进 config.json，下次打开自动填；不勾则不留痕")
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.chk_save)
        form.addRow(save_row)
        form.addRow("学(工)号", self.ed_guiid)
        form.addRow("上网账号", self.ed_acc)
        form.addRow("密    码", row_pwd)
        form.addRow("运 营 商", self.cb_isp)
        root.addWidget(box)

        # 操作按钮
        ops = QHBoxLayout()
        self.btn_login = QPushButton("一 键 联 网")
        self.btn_login.setObjectName("primary")
        self.btn_login.setMinimumHeight(44)
        self.btn_login.setStyleSheet(
            "QPushButton#primary{background:#2563eb;color:white;font-size:16px;"
            "font-weight:700;border-radius:10px;}"
            "QPushButton#primary:hover{background:#1d4ed8;}"
            "QPushButton#primary:disabled{background:#64748b;}"
        )
        self.btn_logoff = QPushButton("断开网络")
        self.btn_logoff.setMinimumHeight(44)
        self.btn_logoff.setFixedWidth(110)
        self.btn_speed = QPushButton("网络测速")
        self.btn_speed.setFixedWidth(104)
        self.btn_speed.setToolTip("打开中国科学技术大学测速网站")
        ops.addWidget(self.btn_login, 1)
        ops.addWidget(self.btn_logoff)
        ops.addWidget(self.btn_speed)
        root.addLayout(ops)

        # 选项（左边选项，右边状态立绘）
        opt = QGroupBox("运行选项")
        oh = QHBoxLayout(opt)
        ov = QVBoxLayout()
        left = ov
        line1 = QHBoxLayout()
        self.chk_auto = QCheckBox("断网自动重连")
        self.chk_auto.setChecked(self.cfg.auto_reconnect)
        self.spin_poll = QSpinBox()
        self.spin_poll.setRange(3, 600)
        self.spin_poll.setValue(self.cfg.poll_seconds)
        self.spin_poll.setSuffix(" 秒")
        self.spin_poll.setFixedWidth(90)
        line1.addWidget(self.chk_auto)
        line1.addWidget(QLabel("检测间隔"))
        line1.addWidget(self.spin_poll)
        line1.addStretch(1)
        ov.addLayout(line1)

        # 「识别最少票数」这个旋钮已经删了：现在的识别器是单次前向的 CRNN，
        # 没有多引擎投票，票数门槛只会让程序白换几十张图。留个说明给翻旧版本的人。
        self.chk_autostart = QCheckBox("开机自启（最小化到托盘）")
        self.chk_autostart.setChecked(autostart.is_enabled())
        ov.addWidget(self.chk_autostart)
        self.chk_tray = QCheckBox("点关闭按钮时最小化到托盘，不退出")
        self.chk_tray.setChecked(True)
        ov.addWidget(self.chk_tray)
        self.chk_autoconn = QCheckBox("启动时自动联网（打开就检查，没在线自动连）")
        self.chk_autoconn.setChecked(self.cfg.auto_connect_on_start)
        ov.addWidget(self.chk_autoconn)

        # 左栏底部那块空白：状态和按钮同一行，IP 落在下面
        self.btn_refresh.setFixedWidth(104)
        self.btn_min = QPushButton("最小化")
        self.btn_min.setFixedWidth(96)
        self.btn_min.setToolTip("收进托盘继续跑，断网自动重连不会停")

        ov.addSpacing(12)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        status_row.addWidget(self.dot)
        status_row.addWidget(self.lbl_state)
        status_row.addSpacing(4)
        status_row.addWidget(self.btn_refresh)
        status_row.addWidget(self.btn_min)
        status_row.addStretch(1)
        ov.addLayout(status_row)

        self.lbl_ip.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        ov.addWidget(self.lbl_ip)
        ov.addStretch(1)

        oh.addLayout(left, 1)

        # 右栏：只剩立绘
        self.lbl_badge = QLabel()
        self.lbl_badge.setFixedSize(218, 192)
        self.lbl_badge.setAlignment(Qt.AlignCenter)
        self.lbl_badge.setStyleSheet("background:transparent;")
        self.lbl_badge.setToolTip("未联网：委屈巴巴　/　已联网：得意比赞")
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.lbl_badge, 0, Qt.AlignTop | Qt.AlignHCenter)
        right.addStretch(1)
        oh.addLayout(right)

        self._badge_movie = None
        self._badge_state = None
        self._startup_auto_done = False
        root.addWidget(opt)

        # 验证码预览
        caprow = QHBoxLayout()
        self.lbl_cap = QLabel("验证码：--")
        self.lbl_cap.setStyleSheet("color:#64748b;")
        self.lbl_capimg = QLabel()
        self.lbl_capimg.setFixedSize(160, 48)
        self.lbl_capimg.setStyleSheet("border:1px solid #cbd5e1;border-radius:6px;background:#fff;")
        self.lbl_capimg.setAlignment(Qt.AlignCenter)
        self.lbl_capimg.setScaledContents(False)
        caprow.addWidget(self.lbl_cap)
        caprow.addStretch(1)
        caprow.addWidget(self.lbl_capimg)
        root.addLayout(caprow)

        # 日志
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(400)
        self.txt_log.setStyleSheet(
            "QPlainTextEdit{background:#0b1220;color:#cbd5e1;border-radius:8px;"
            "font-family:Consolas,'Microsoft YaHei UI';font-size:12px;}"
        )
        root.addWidget(self.txt_log, 1)

        # 页脚：右下角署名
        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self.btn_update = QPushButton("更新地址")
        self.btn_update.setFlat(True)
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.setToolTip(f"打开项目主页看看有没有新版本\n{UPDATE_URL}")
        self.btn_update.setStyleSheet(
            "QPushButton{border:none;color:#2563eb;font-size:11px;padding:0 2px;}"
            "QPushButton:hover{color:#1d4ed8;text-decoration:underline;}"
        )
        self.btn_update.clicked.connect(self.on_open_update)
        footer.addWidget(self.btn_update)
        footer.addStretch(1)
        self.lbl_footer = QLabel(f"GUIT校园网一键联网  v{APP_VERSION}")
        self.lbl_footer.setStyleSheet("color:#94a3b8;font-size:11px;")
        footer.addWidget(self.lbl_footer)
        root.addLayout(footer)

        # 托盘
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_TITLE)
        menu = QMenu()
        self.act_show = QAction("显示主窗口", self)
        self.act_login = QAction("一键联网", self)
        self.act_logoff = QAction("断开网络", self)
        self.act_quit = QAction("退出", self)
        menu.addAction(self.act_show)
        menu.addSeparator()
        menu.addAction(self.act_login)
        menu.addAction(self.act_logoff)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)

    def _wire(self) -> None:
        self.btn_login.clicked.connect(self.on_login)
        self.btn_logoff.clicked.connect(self.on_logoff)
        self.btn_refresh.clicked.connect(self._initial_state_check)
        self.btn_min.clicked.connect(self.on_minimize)
        self.btn_speed.clicked.connect(self.on_speedtest)
        self.chk_autoconn.toggled.connect(lambda _v: self.save_cfg())
        self.cb_show.toggled.connect(
            lambda on: self.ed_pwd.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        self.chk_auto.toggled.connect(self.on_auto_toggled)
        self.chk_autostart.toggled.connect(self.on_autostart_toggled)
        self.chk_tray.toggled.connect(lambda _v: self.save_cfg())
        self.chk_save.toggled.connect(lambda _v: self.save_cfg())
        self.spin_poll.valueChanged.connect(self.on_poll_changed)
        self.bridge.log.connect(self.log)
        self.bridge.state.connect(self.set_state)
        self.tray.activated.connect(self.on_tray_activated)
        self.act_show.triggered.connect(self.show_window)
        self.act_login.triggered.connect(self.on_login)
        self.act_logoff.triggered.connect(self.on_logoff)
        self.act_quit.triggered.connect(self.quit_app)
        for ed in (self.ed_guiid, self.ed_acc, self.ed_pwd):
            ed.editingFinished.connect(self.save_cfg)

    # ---- 配置 --------------------------------------------------------------- #
    def save_cfg(self) -> None:
        self.cfg.gui_id = self.ed_guiid.text().strip()
        self.cfg.account = self.ed_acc.text().strip()
        self.cfg.password = self.ed_pwd.text()
        self.cfg.isp = self.cb_isp.currentText()
        self.cfg.auto_reconnect = self.chk_auto.isChecked()
        self.cfg.poll_seconds = self.spin_poll.value()
        self.cfg.autostart = self.chk_autostart.isChecked()
        self.cfg.save_credentials = self.chk_save.isChecked()
        self.cfg.auto_connect_on_start = self.chk_autoconn.isChecked()
        self.cfg.min_votes = 0   # 单次前向识别器没有投票概念，写死 0（字段保留只为兼容老 config）
        try:
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            self.log(f"× 配置保存失败：{exc}")

    def _login_config(self) -> LoginConfig:
        return LoginConfig(
            base_url=self.cfg.base_url,
            gui_id=self.ed_guiid.text().strip(),
            account=self.ed_acc.text().strip(),
            password=self.ed_pwd.text(),
            isp=self.cb_isp.currentText(),
            max_captcha_retry=self.cfg.max_captcha_retry,
            max_blank_tries=max(10, int(self.cfg.max_captcha_retry) * 3),
            manual_captcha=bool(self.cfg.manual_captcha),
            min_votes=0,
        )

    # ---- 界面状态 ----------------------------------------------------------- #
    def log(self, msg: str) -> None:
        from datetime import datetime

        self.txt_log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")

    def set_state(self, online: bool, detail: str = "") -> None:
        color = GREEN if online else RED
        self.dot.setStyleSheet(f"color:{color};font-size:24px;")
        self.lbl_state.setText("已联网" if online else "未联网")
        self.lbl_state.setStyleSheet(f"font-size:22px;font-weight:800;color:{color};")
        self.lbl_ip.setStyleSheet("color:#475569;font-size:13px;")
        self.lbl_ip.setText(detail or "")
        self.tray.setIcon(app_icon(online))
        self.tray.setToolTip(f"{APP_TITLE} — {'已联网' if online else '未联网'}\n{detail}")
        self._set_badge(online)

    def _set_badge(self, state) -> None:
        """右侧立绘三态：online=得意 / offline=委屈 / error=联网出错。

        传 bool 时兼容旧写法（True→online，False→offline）。
        """
        if isinstance(state, bool):
            state = "online" if state else "offline"
        if self._badge_state == state:
            return
        fname = {
            "online": "state-online.gif",
            "offline": "state-offline.gif",
            "error": "state-error.gif",
        }.get(state, "state-offline.gif")
        path = _asset(fname)
        if not path:
            return
        movie = QMovie(str(path))
        if not movie.isValid():
            return
        movie.setCacheMode(QMovie.CacheAll)
        self.lbl_badge.setMovie(movie)
        movie.start()
        self._badge_movie = movie      # 必须持引用，否则会被 GC 掉
        self._badge_state = state

    def set_busy(self, busy: bool) -> None:
        self.btn_login.setEnabled(not busy)
        self.btn_logoff.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)
        self.btn_login.setText("处理中…" if busy else "一 键 联 网")

    def _show_captcha(self, png: bytes, code: str) -> None:
        if png:
            pix = QPixmap()
            pix.loadFromData(png)
            if not pix.isNull():
                self.lbl_capimg.setPixmap(
                    pix.scaled(self.lbl_capimg.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        self.lbl_cap.setText(f"算式：{code or '未识别'}")

    # ---- 动作 --------------------------------------------------------------- #
    def _start_task(self, name: str, fn, lock_ui: bool = True) -> None:
        if self.task and self.task.isRunning():
            self.log("· 上一个任务还在跑，请稍候")
            return
        if lock_ui:
            self.set_busy(True)
        t = TaskThread(fn, self)
        t.log.connect(self.log)
        t.captcha.connect(self._show_captcha)
        t.done.connect(lambda ok, msg: self._task_done(name, ok, msg, lock_ui))
        self.task = t
        t.start()

    def _task_done(self, name: str, ok: bool, msg: str, lock_ui: bool = True) -> None:
        if lock_ui:
            self.set_busy(False)
        if name == "status":
            # _do_status 返回 (online, detail)，直接喂给状态条
            self.set_state(bool(ok), msg)
            self._maybe_auto_connect(bool(ok))
        elif name == "login":
            self.log(("√ " if ok else "× ") + msg)
            if ok:
                self._refresh_after()
            else:
                # 联网失败：换上出错的表情并停在这里。
                # 不自动刷新状态，否则 600ms 后又被"未联网"覆盖掉，根本看不到这张图。
                self._set_badge("error")
                if "验证码连续识别失败" in msg:
                    # 自动认不出来：给张新图让主人自己填，别就这么算了
                    QTimer.singleShot(200, self._offer_manual_captcha)
        elif name == "logoff":
            self.log(("√ " if ok else "× ") + msg)
            self._refresh_after()

    def _offer_manual_captcha(self) -> None:
        """自动认不出算式时，取一张新图让主人手输，别把这一轮白白扔掉。"""
        cfg = self._login_config()
        if not getattr(cfg, "manual_captcha", False):
            self.log("· 「手动输入验证码」没开，跳过兜底（可在 config.json 里打开）")
            return
        self.log("· 自动识别失败，取一张新图请主人手动输入…")
        manual = prepare_manual_login(cfg, log=self.log)
        if manual is None:
            return
        dlg = _ManualCaptchaDialog(manual.captcha_png, self)
        if dlg.exec() != QDialog.Accepted:
            self.log("· 已取消手动输入验证码")
            return
        answer = dlg.value()
        if not answer:
            return
        self.set_busy(True)
        try:
            out = submit_manual_login(cfg, manual, answer, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"× 手动提交异常：{exc}")
            self.set_busy(False)
            return
        self.set_busy(False)
        self.log(("√ " if out.ok else "× ") + out.message)
        if out.ok:
            self._set_badge("online")
            self._refresh_after()
        else:
            self._set_badge("error")

    def _maybe_auto_connect(self, online: bool) -> None:
        """启动后第一次查到「没在线」，就顺手连上去。

        "打开软件自动连接"：只在本次启动的第一次状态查询时触发一次，
        之后手动点「刷新状态」不会再自己登录（免得反复踢掉在线状态）。
        """
        if online or self._startup_auto_done:
            return
        self._startup_auto_done = True
        if not self.cfg.auto_connect_on_start:
            self.log("· 启动检查：未联网，但「启动时自动联网」没勾，先不动")
            return
        self.log("· 启动检查：检测到未联网，自动开始联网…")
        QTimer.singleShot(600, self.on_login)

    def on_speedtest(self) -> None:
        """测速附页。

        用 Edge/Chrome 的 --app 模式开一个无地址栏窗口：内核是真的，
        test.ustc.edu.cn 的 backend/pow.php 工作量证明能正常跑，
        又不用把 200MB 的 QtWebEngine 塞进包里。
        """
        browser = _find_browser()
        if browser:
            try:
                subprocess.Popen(
                    [browser, f"--app={SPEEDTEST_URL}", "--window-size=980,900"],
                    close_fds=True,
                )
                self.log("· 已打开测速页面（中国科学技术大学测速网站）")
                return
            except OSError as exc:
                self.log(f"× 调用浏览器失败：{exc}")
        QDesktopServices.openUrl(QUrl(SPEEDTEST_URL))
        self.log("· 已用默认浏览器打开测速页面")

    def _initial_state_check(self) -> None:
        note = getattr(self, "_pending_note", "")
        if note:
            self._pending_note = ""
            self.log(note)
        # 状态查询不锁界面：门户不可达时也不该让按钮变灰
        self._start_task("status", self._do_status, lock_ui=False)

    def _refresh_after(self) -> None:
        QTimer.singleShot(600, lambda: self._start_task("status", self._do_status, lock_ui=False))

    def _do_status(self, log, _cap):
        cfg = self._login_config()
        try:
            st = check_status(cfg.base_url, timeout=6)
        except Exception as exc:  # noqa: BLE001
            log(f"× 状态查询异常：{exc}")
            return False, "查询异常"
        if not st.reachable:
            log("× 认证服务器不可达（是不是没连校园网？）")
            return False, "认证服务器不可达"
        log(f"· 状态查询：{'已联网' if st.online else '未联网'} {st.ip}".strip())
        return st.online, (st.ip if st.online else "")

    def on_login(self) -> None:
        self.save_cfg()
        cfg = self._login_config()
        if not cfg.gui_id or not cfg.account or not cfg.password:
            QMessageBox.warning(self, APP_TITLE, "学(工)号 / 上网账号 / 密码 都要填")
            return

        def job(log, cap):
            log("— 开始联网 —")
            st = check_status(cfg.base_url, timeout=6)
            if st.reachable and st.online:
                return True, f"已经在线啦（{st.ip}），不用重复登录"
            # cap 是 Signal(bytes, str) 的 emit；算式对象要先转成人话再发
            out = attempt_login(
                cfg,
                self.math_solver,
                log=log,
                on_captcha=lambda png, c: cap(png, c.describe()),
            )
            return out.ok, out.message

        self._start_task("login", job)

    def on_logoff(self) -> None:
        self.save_cfg()
        cfg = self._login_config()
        if QMessageBox.question(self, APP_TITLE, "确定要断开网络吗？断开后需要重新认证") != QMessageBox.Yes:
            return

        def job(log, cap):
            out = do_logoff(cfg, self.solver, log=log)
            return out.ok, out.message

        self._start_task("logoff", job)

    def on_auto_toggled(self, on: bool) -> None:
        self.save_cfg()
        if on:
            if self.reconnector and not self.reconnector.running:
                self.reconnector.start()
                self.log(f"· 已开启断网自动重连，每 {self.spin_poll.value()} 秒检测一次")
        else:
            if self.reconnector:
                self.reconnector.stop()
            self.log("· 已关闭断网自动重连")

    def on_poll_changed(self, v: int) -> None:
        self.save_cfg()
        if self.chk_auto.isChecked():
            self.log(f"· 检测间隔改为 {v} 秒")

    def on_autostart_toggled(self, on: bool) -> None:
        try:
            autostart.apply(on, start_minimized=True)
            self.cfg.autostart = on
            self.save_cfg()
            self.log(("· 已设置开机自启" if on else "· 已取消开机自启"))
        except Exception as exc:  # noqa: BLE001
            self.log(f"× 开机自启设置失败：{exc}")
            QMessageBox.warning(self, APP_TITLE, f"开机自启设置失败：{exc}")

    def on_minimize(self) -> None:
        """收进托盘：窗口藏起来，托盘和自动重连继续跑。"""
        self.hide()
        if self.tray.isVisible():
            self.tray.showMessage(
                APP_TITLE,
                "已最小化到托盘，断网会自动重连",
                QSystemTrayIcon.Information,
                2500,
            )

    def show_window(self) -> None:
        # 托盘图标有可能被 Windows 收进「隐藏的图标」区，顺手确认它还在
        if not self.tray.isVisible():
            self.tray.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def on_open_update(self) -> None:
        """在浏览器里打开项目的更新地址。"""
        QDesktopServices.openUrl(QUrl(UPDATE_URL))
        self.log(f"· 已在浏览器打开更新地址：{UPDATE_URL}")

    def _poll_wake(self) -> None:
        """别的实例在敲门：把窗口叫回来。"""
        if self._wake.poll():
            if self.isMinimized():
                self.showNormal()
            self.show_window()
            self.log("· 已经有一份在跑，把窗口叫出来了")

    def on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()

    def quit_app(self) -> None:
        self._quitting = True
        if self.reconnector:
            self.reconnector.stop()
        self.tray.hide()
        QApplication.quit()

    # ---- 事件 --------------------------------------------------------------- #
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.save_cfg()
        if self.chk_tray.isChecked() and not self._quitting:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                APP_TITLE, "已最小化到托盘，程序继续在后台运行", QSystemTrayIcon.Information, 1800
            )
            return
        if self.reconnector:
            self.reconnector.stop()
        self.tray.hide()
        event.accept()
