"""高层动作：一次完整登录（算式验证码自动重试）、注销、状态查询、自动重连监视器。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .ocr import CaptchaSolver, MathCaptcha, MathCaptchaSolver
from .portal import (
    LoginForm,
    LoginOutcome,
    PortalClient,
    PortalError,
    PortalStatus,
    probe_internet,
)

Logger = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


@dataclass
class LoginConfig:
    base_url: str = "http://10.6.6.6"
    gui_id: str = ""
    account: str = ""
    password: str = ""
    isp: str = "自动选择"
    #: 最多提交几次验证码（每次提交都要联网 POST 一次）
    max_captcha_retry: int = 10
    #: 最多看多少张图。认不出算式时只换图、不提交，所以可以多换几张
    max_blank_tries: int = 30
    #: 自动识别彻底失败时，是否允许弹窗手输
    manual_captcha: bool = False


def _is_captcha_error(message: str) -> bool:
    return "验证码" in (message or "")


# 门户主页 FAQ 公布的错误对照表（顺序敏感：越具体的放越前）
_ERROR_RULES: tuple[tuple[str, str], ...] = (
    # —— 门户自己的表单校验
    ("验证码", "验证码识别错了，换一张再来"),
    ("密码长度应大于0小于17", "密码最多 16 位，检查一下是不是多输了"),
    # —— 门户给出的认证结果
    ("用户未实名", "这个运营商账号还没实名登记：带身份证和一卡通去信息中心（德远楼 E116）"),
    ("账号或密码错误", "账号或密码不对，核对后重填（密码最多 16 位）"),
    ("超过最大并发终端数", "这个上网账号已经有 2 台设备在线了，先在别的设备断开再来"),
    ("NOSERVICE", "运营商侧服务不可用（NOSERVICE），等 5 分钟再试"),
    ("认证超时", "运营商认证超时，等 5 分钟再试"),
    ("Timeout", "运营商认证超时，等 5 分钟再试"),
    # —— 运营商 RADIUS 回包
    ("Reject by concurrency control", "账号在别的地方在线（并发控制拒绝），先断开其它设备或联系运营商"),
    ("Limit Users Err", "账号在线数超限了，先断开其它设备"),
    ("limit exceed", "账号在线数超限了，先断开其它设备"),
    ("subscriber is deregistered or the password is incorrect", "账号未开通或密码错，确认账号后联系运营商"),
    ("subscriber status is incorrect", "账号未开通，联系运营商"),
    ("AAA authenticate terminal failed", "账号绑定的设备信息不对，需要联系运营商解绑"),
    ("NAS-Port-Id", "账号绑定了别的 NAS/端口，联系运营商解绑"),
    ("Service Not Available", "账号未开通或无效，联系运营商确认"),
    ("ai-Service-Password", "密码不对，核对后重填"),
    ("Passwd_Err", "密码不对，核对后重填"),
    ("Request Deny", "账号可能无效，联系运营商确认"),
    ("UserName_Err", "上网账号可能无效，核对后联系运营商"),
    ("UserName invalid", "上网账号可能无效，核对后联系运营商"),
    ("illegal charactor", "上网账号里有非法字符，检查一下填对了没"),
    ("Account OverDue", "账号欠费了，联系运营商缴费"),
    # —— 兜底
    ("绑定信息错误", "账号绑定的设备信息不对，需要联系运营商解绑"),
    ("未开通", "账号未开通，联系运营商或信息中心"),
)


def _friendly(message: str) -> str:
    """把门户/运营商的原始错误翻译成人话。"""
    m = (message or "").strip()
    if not m:
        return "未知错误"
    low = m.lower()
    for key, nice in _ERROR_RULES:
        if key.lower() in low:
            return nice
    return m


def _confirm_online(cli: PortalClient, log: Logger, tries: int = 2) -> Optional[PortalStatus]:
    """提交成功后复核在线状态；门户偶尔要等一两秒才刷新。"""
    for i in range(tries):
        st = cli.status()
        if st.online:
            return st
        if i + 1 < tries:
            time.sleep(1.5)
    return None


def attempt_login(
    cfg: LoginConfig,
    solver: MathCaptchaSolver,
    log: Logger = _noop,
    client: Optional[PortalClient] = None,
    verify: bool = True,
    on_captcha: Optional[Callable[[bytes, MathCaptcha], None]] = None,
) -> LoginOutcome:
    """完整登录：取图 -> 识别算式 -> 算结果 -> 提交。

    换图是便宜的（几百毫秒），提交是贵的；所以认不出算式时只换图不提交，
    只有真的解析出一个合法答案才去 POST。服务端明确说「验证码错误」就再换一张。
    一旦是账号密码类的硬错误立刻返回，不反复拿账号去撞。
    """
    own_client = client is None
    cli = client or PortalClient(cfg.base_url)
    max_submit = max(1, int(cfg.max_captcha_retry))
    max_fetch = max(max_submit, int(getattr(cfg, "max_blank_tries", max_submit * 3)))
    last = LoginOutcome(ok=False, message="未执行")
    submits = 0
    fetches = 0

    try:
        while fetches < max_fetch and submits < max_submit:
            fetches += 1
            try:
                form = cli.prepare_login()
            except PortalError as exc:
                last = LoginOutcome(ok=False, message=str(exc))
                log(f"× {exc}")
                return last

            try:
                cap = solver.solve(form.captcha_png)
            except Exception as exc:  # noqa: BLE001
                last = LoginOutcome(ok=False, message=f"验证码识别引擎异常：{exc}")
                log(f"× 识别引擎异常：{exc}")
                return last

            if on_captcha:
                try:
                    on_captcha(form.captcha_png, cap)
                except Exception:
                    pass

            if not cap.ok:
                log(f"· 第 {fetches} 张：算式没认出来，换一张")
                continue

            submits += 1
            log(f"· 第 {fetches} 张：识别为 {cap.describe()}，提交登录…")
            try:
                out = cli.submit_login(
                    form, cfg.gui_id, cfg.account, cfg.password, cap.answer, cfg.isp
                )
            except PortalError as exc:
                last = LoginOutcome(ok=False, message=str(exc))
                log(f"× {exc}")
                return last

            if out.ok:
                if verify:
                    st = _confirm_online(cli, log)
                    if st:
                        log("√ 门户确认：已联网")
                        return LoginOutcome(
                            ok=True, message="登录成功，已联网", status_code=out.status_code
                        )
                    return LoginOutcome(
                        ok=True,
                        message="已提交登录，但门户未显示已联网",
                        status_code=out.status_code,
                    )
                return out

            last = out
            if _is_captcha_error(out.message):
                log(f"· 验证码被拒（{out.message}），换一张重试")
                continue

            nice = _friendly(out.message)
            log(f"× 登录失败：{nice}")
            if nice != out.message:
                log(f"  服务端原话：{out.message}")
            return LoginOutcome(ok=False, message=nice, errors=list(out.errors) + [out.message])
    finally:
        if own_client:
            cli.session.close()

    if submits >= max_submit and last.message == "未执行":
        log("× 连续多张都没认出算式，请手动输入或稍后再试")
        return LoginOutcome(ok=False, message="验证码连续识别失败（可以手动输入）")
    log("× 连续多次都没能识别对验证码，请手动输入或稍后再试")
    return LoginOutcome(ok=False, message="验证码连续识别失败（可以手动输入）")


# --------------------------------------------------------------------------- #
# 手动兜底：自动识别没辙时，把一张新图交给界面，由用户自己填答案
# --------------------------------------------------------------------------- #
@dataclass
class ManualLogin:
    """一次已经备好隐藏字段和验证码、只等人填答案的登录会话。"""

    client: PortalClient
    form: LoginForm

    @property
    def captcha_png(self) -> bytes:
        return self.form.captcha_png


def prepare_manual_login(
    cfg: LoginConfig, log: Logger = _noop
) -> Optional[ManualLogin]:
    """取一张全新的算式图交给用户手输（会话保持，填完直接提交）。"""
    try:
        cli = PortalClient(cfg.base_url)
        form = cli.prepare_login()
    except PortalError as exc:
        log(f"× {exc}")
        return None
    return ManualLogin(client=cli, form=form)


def submit_manual_login(
    cfg: LoginConfig,
    manual: ManualLogin,
    answer: str,
    log: Logger = _noop,
    verify: bool = True,
) -> LoginOutcome:
    """把用户手输的答案提交上去。"""
    answer = (answer or "").strip()
    if not answer:
        return LoginOutcome(ok=False, message="没填答案")
    log(f"· 手动提交验证码 {answer}…")
    try:
        out = manual.client.submit_login(
            manual.form, cfg.gui_id, cfg.account, cfg.password, answer, cfg.isp
        )
    except PortalError as exc:
        log(f"× {exc}")
        return LoginOutcome(ok=False, message=str(exc))
    if out.ok:
        if verify:
            st = _confirm_online(manual.client, log)
            if st:
                log("√ 门户确认：已联网")
                return LoginOutcome(ok=True, message="登录成功，已联网", status_code=out.status_code)
        return LoginOutcome(
            ok=True, message=out.message or "已提交登录", status_code=out.status_code
        )
    nice = _friendly(out.message)
    log(f"× 登录失败：{nice}")
    return LoginOutcome(ok=False, message=nice, errors=list(out.errors) + [out.message])


def do_logoff(cfg: LoginConfig, solver: CaptchaSolver, log: Logger = _noop) -> LoginOutcome:
    """注销走的是 /CheckCode 那套四位字母数字验证码。"""
    cli = PortalClient(cfg.base_url)
    tries = max(1, int(cfg.max_captcha_retry))
    try:
        for i in range(1, tries + 1):
            try:
                form = cli.prepare_logoff()
                code = solver.solve(form.captcha_png)
            except (PortalError, Exception) as exc:  # noqa: BLE001
                log(f"× {exc}")
                return LoginOutcome(ok=False, message=str(exc))
            if not code:
                log(f"· 第 {i}/{tries} 次：验证码没认出，换一张")
                continue
            log(f"· 第 {i}/{tries} 次：验证码 {code}，提交注销…")
            out = cli.submit_logoff(form, code)
            if out.ok:
                time.sleep(0.8)
                st = cli.status()
                if not st.online:
                    log("√ 已断开网络")
                    return LoginOutcome(ok=True, message="已断开网络")
                return LoginOutcome(ok=True, message="已提交注销，门户仍显示在线")
            if _is_captcha_error(out.message):
                log(f"· 验证码被拒（{out.message}），换一张重试")
                continue
            log(f"× 注销失败：{_friendly(out.message)}")
            return LoginOutcome(ok=False, message=_friendly(out.message))
    finally:
        cli.session.close()
    return LoginOutcome(ok=False, message="验证码连续识别失败")


def check_status(base_url: str, timeout: float = 8.0) -> PortalStatus:
    return PortalClient(base_url, timeout=timeout).status()


# --------------------------------------------------------------------------- #
# 自动重连监视器
# --------------------------------------------------------------------------- #
class AutoReconnector:
    """后台线程：周期性查在线状态，掉线就自动登录。"""

    def __init__(
        self,
        get_cfg: Callable[[], LoginConfig],
        solver: MathCaptchaSolver,
        interval: Callable[[], int] | int = 15,
        log: Logger = _noop,
        on_state: Optional[Callable[[bool, str], None]] = None,
        use_internet_probe: bool = True,
    ) -> None:
        self._get_cfg = get_cfg
        self._solver = solver
        self._interval = interval
        self._log = log
        self._on_state = on_state
        self._use_probe = use_internet_probe
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._logged_in_this_round = False

    # -- 生命周期 ------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="auto-reconnect", daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 3.0) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=join_timeout)
        self._thread = None

    # -- 内部 ---------------------------------------------------------------- #
    def _sleep(self, seconds: float) -> bool:
        """可被打断的睡眠；返回 False 表示收到停止信号。"""
        return not self._stop.wait(seconds)

    def _interval_seconds(self) -> float:
        v = self._interval() if callable(self._interval) else self._interval
        try:
            return max(3.0, float(v))
        except Exception:
            return 15.0

    def check_once(self, auto_login: bool = True) -> bool:
        """查一次状态，必要时自动登录。返回是否在线。"""
        cfg = self._get_cfg()
        try:
            st = check_status(cfg.base_url)
        except Exception as exc:  # noqa: BLE001
            self._log(f"· 状态查询异常：{exc}")
            if self._on_state:
                self._on_state(False, f"状态查询异常：{exc}")
            return False

        if st.online:
            self._logged_in_this_round = False
            if self._on_state:
                self._on_state(True, f"已联网 {st.ip}".strip())
            return True

        if self._use_probe and probe_internet(timeout=4):
            # 门户说没连、外网却通：不能贸然重登（比如同账号在别的设备在线）
            self._log("· 门户显示未联网，但外网可达，跳过自动登录")
            if self._on_state:
                self._on_state(True, "外网可达（门户状态滞后）")
            return True

        if self._on_state:
            self._on_state(False, "未联网")
        if not auto_login:
            return False
        if self._logged_in_this_round:
            return False

        self._log("! 检测到断网，自动登录中…")
        out = attempt_login(cfg, self._solver, log=self._log, verify=False)
        self._logged_in_this_round = bool(out.ok)
        if out.ok:
            time.sleep(1.0)
            st2 = check_status(cfg.base_url)
            if st2.online:
                self._log(f"√ 自动重连成功 {st2.ip}".rstrip())
                if self._on_state:
                    self._on_state(True, f"已联网 {st2.ip}".strip())
                return True
        if self._on_state:
            self._on_state(False, out.message)
        return False

    def _loop(self) -> None:
        # 刚启动先给 1.5 秒，避免和界面初始化抢资源
        if not self._sleep(1.5):
            return
        while not self._stop.is_set():
            try:
                self.check_once(auto_login=True)
            except Exception as exc:  # noqa: BLE001
                self._log(f"× 自动重连线程异常：{exc}")
            if not self._sleep(self._interval_seconds()):
                return
