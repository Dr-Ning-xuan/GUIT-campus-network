"""PySide6 界面：主窗口 + 托盘常驻 + 后台任务。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
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
from .ocr import CaptchaSolver
from .service import (
    AutoReconnector,
    LoginConfig,
    attempt_login,
    check_status,
    do_logoff,
)
from .store import Config, config_path

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


def app_icon(online: Optional[bool] = None) -> QIcon:
    """应用图标 = assets/icon.ico（迷迭香），传状态时右下角叠一颗绿/红点。

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


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, solver: CaptchaSolver, start_hidden: bool = False) -> None:
        super().__init__()
        self.cfg = cfg
        self.solver = solver
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
            solver=self.solver,
            interval=lambda: self.spin_poll.value(),
            log=self.bridge.log.emit,
            on_state=self.bridge.state.emit,
        )

        # 预热识别引擎（不占界面）
        self._warm = TaskThread(lambda l, c: (self.solver.warmup() or True, "识别引擎就绪"))
        self._warm.done.connect(lambda ok, msg: self.log("· 验证码识别引擎已就绪"))
        self._warm.log.connect(lambda m: None)
        self._warm.start()

        self._set_badge(False)   # 开局先委屈着，查到在线再变得意
        self.tray.show()
        if not start_hidden:
            self.show()
        else:
            self.tray.showMessage(APP_TITLE, "已最小化到托盘运行喵~", QSystemTrayIcon.Information, 2500)

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
        ops.addWidget(self.btn_login, 1)
        ops.addWidget(self.btn_logoff)
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
        self.chk_autostart = QCheckBox("开机自启（最小化到托盘）")
        self.chk_autostart.setChecked(autostart.is_enabled())
        ov.addWidget(self.chk_autostart)
        self.chk_tray = QCheckBox("点关闭按钮时最小化到托盘，不退出")
        self.chk_tray.setChecked(True)
        ov.addWidget(self.chk_tray)

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
        self._badge_online = None
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
        footer.addStretch(1)
        self.lbl_footer = QLabel("宁轩制作，有最终解释权")
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

    def _set_badge(self, online: bool) -> None:
        """右侧立绘：未联网播 state-offline.gif，已联网播 state-online.gif。"""
        online = bool(online)
        if self._badge_online is online:
            return
        path = _asset("state-online.gif" if online else "state-offline.gif")
        if not path:
            return
        movie = QMovie(str(path))
        if not movie.isValid():
            return
        movie.setCacheMode(QMovie.CacheAll)
        self.lbl_badge.setMovie(movie)
        movie.start()
        self._badge_movie = movie      # 必须持引用，否则会被 GC 掉
        self._badge_online = online

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
        self.lbl_cap.setText(f"验证码：{code or '识别失败'}")

    # ---- 动作 --------------------------------------------------------------- #
    def _start_task(self, name: str, fn, lock_ui: bool = True) -> None:
        if self.task and self.task.isRunning():
            self.log("· 上一个任务还在跑，稍等一下喵~")
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
        elif name in ("login", "logoff"):
            self.log(("√ " if ok else "× ") + msg)
            self._refresh_after()

    def _initial_state_check(self) -> None:
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
            QMessageBox.warning(self, APP_TITLE, "学(工)号 / 上网账号 / 密码 都要填哦喵~")
            return

        def job(log, cap):
            log("— 开始联网 —")
            st = check_status(cfg.base_url, timeout=6)
            if st.reachable and st.online:
                return True, f"已经在线啦（{st.ip}），不用重复登录"
            out = attempt_login(cfg, self.solver, log=log, on_captcha=cap)
            return out.ok, out.message

        self._start_task("login", job)

    def on_logoff(self) -> None:
        self.save_cfg()
        cfg = self._login_config()
        if QMessageBox.question(self, APP_TITLE, "确定要断开网络吗？断开后就要重新认证了喵~") != QMessageBox.Yes:
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
                "迷迭香躲进托盘了喵~ 断网会自动帮你连回来",
                QSystemTrayIcon.Information,
                2500,
            )

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

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
                APP_TITLE, "迷迭香躲在托盘里继续守着网络喵~", QSystemTrayIcon.Information, 1800
            )
            return
        if self.reconnector:
            self.reconnector.stop()
        self.tray.hide()
        event.accept()
