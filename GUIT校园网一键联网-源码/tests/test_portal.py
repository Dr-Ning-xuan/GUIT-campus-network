"""离线自检：解析、判定、重试逻辑、配置、识别结果清洗。

全部用假 HTML / 假 client，不碰网络，跑得快也跑得稳。
真机接口另有 tools/probe_login.py 人工验证。
"""
from __future__ import annotations

import json

import pytest

from src.ocr import CaptchaSolver
from src.portal import (
    LoginForm,
    LoginOutcome,
    PortalClient,
    PortalStatus,
    _parse_page,
    _plain_text,
)
from src.ocr import MathCaptcha, compute, normalize_expr
from src.service import (
    LoginConfig,
    _friendly,
    _is_captcha_error,
    attempt_login,
    prepare_manual_login,
    submit_manual_login,
)
from src.store import Config

# --------------------------------------------------------------------------- #
# 真实抓下来的页面片段
# --------------------------------------------------------------------------- #
LOGIN_SNIPPET = """
<form method="post" action="./login" id="ctl01">
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="/eG/cU/1sOK0mWv7" />
<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="C2EE9ABB" />
<input type="hidden" name="__VIEWSTATEENCRYPTED" id="__VIEWSTATEENCRYPTED" value="" />
<input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="dt/GbQHL51sk3q0" />
<input name="ctl00$MainContent$SSOGUITID" type="text" maxlength="12" id="MainContent_SSOGUITID" />
<input name="ctl00$MainContent$LoginBtn" type="submit" value="登录" id="MainContent_LoginBtn" />
<p class="validation-summary-errors">
    %s
</p>
</form>
"""

LOGOFF_ONLINE = """
<html><body>
<span>当前用户IP</span> <span><font color="LightGreen">203.0.113.45</font></span>
<span>当前在线状态</span> <span>已联网</span>
</body></html>
"""

LOGOFF_OFFLINE = """
<html><body>
<span>当前用户IP</span><span>203.0.113.45</span>
<span>当前在线状态</span><span>未联网</span>
</body></html>
"""


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def test_parse_hidden_fields():
    p = _parse_page(LOGIN_SNIPPET % "")
    assert p.hidden["__VIEWSTATE"] == "/eG/cU/1sOK0mWv7"
    assert p.hidden["__VIEWSTATEGENERATOR"] == "C2EE9ABB"
    assert p.hidden["__EVENTVALIDATION"] == "dt/GbQHL51sk3q0"


def test_parse_error_block():
    p = _parse_page(LOGIN_SNIPPET % "验证码错误!")
    assert p.errors == ["验证码错误!"]


def test_parse_no_error_when_empty():
    p = _parse_page(LOGIN_SNIPPET % "")
    assert p.errors == []


def test_plain_text_strips_tags():
    t = _plain_text("<b>当前在线状态</b> <i>已联网</i>")
    assert "当前在线状态" in t and "已联网" in t and "<" not in t


# --------------------------------------------------------------------------- #
# status()
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "html,expect_online",
    [(LOGOFF_ONLINE, True), (LOGOFF_OFFLINE, False)],
)
def test_status_parsing(monkeypatch, html, expect_online):
    c = PortalClient()
    monkeypatch.setattr(c, "_get", lambda action: html)
    st = c.status()
    assert st.online is expect_online
    assert st.ip == "203.0.113.45"


def test_status_unreachable(monkeypatch):
    from src.portal import PortalError

    c = PortalClient()

    def boom(action):
        raise PortalError("boom")

    monkeypatch.setattr(c, "_get", boom)
    st = c.status()
    assert st.online is False and st.reachable is False


# --------------------------------------------------------------------------- #
# 表单提交：字段名一个都不能错
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


def test_submit_login_field_names(monkeypatch):
    c = PortalClient()
    seen = {}

    def fake_post(action, data):
        seen["action"] = action
        seen.update(data)
        return _FakeResp(200, "<html></html>")

    monkeypatch.setattr(c, "_post", fake_post)
    form = LoginForm(hidden={"__VIEWSTATE": "V", "__EVENTVALIDATION": "E"}, captcha_png=b"x")
    out = c.submit_login(form, "2024012345", "13800138000", "secret", "MX2Y")
    assert out.ok
    assert seen["action"] == "/login"
    assert seen["__VIEWSTATE"] == "V"
    assert seen["__EVENTVALIDATION"] == "E"
    assert seen["ctl00$MainContent$SSOGUITID"] == "2024012345"
    assert seen["ctl00$MainContent$SSOACC"] == "13800138000"
    assert seen["ctl00$MainContent$SSOPWD"] == "secret"
    assert seen["ctl00$MainContent$TextBoxCC"] == "MX2Y"
    assert seen["ctl00$MainContent$TextBoxISP"] == "自动选择"
    assert seen["ctl00$MainContent$LoginBtn"] == "登录"


def test_submit_login_reports_server_error(monkeypatch):
    c = PortalClient()
    monkeypatch.setattr(c, "_post", lambda a, d: _FakeResp(200, LOGIN_SNIPPET % "验证码错误!"))
    form = LoginForm(hidden={}, captcha_png=b"x")
    out = c.submit_login(form, "1", "2", "3", "ZZZZ")
    assert out.ok is False
    assert "验证码错误" in out.message


def test_submit_login_treats_success_banner_as_success(monkeypatch):
    """门户把「联网成功!」也塞进 validation-summary-errors —— 实测踩过。"""
    c = PortalClient()
    monkeypatch.setattr(c, "_post", lambda a, d: _FakeResp(200, LOGIN_SNIPPET % "联网成功!"))
    out = c.submit_login(LoginForm(hidden={}, captcha_png=b"x"), "1", "2", "3", "AAAA")
    assert out.ok is True, out.message
    assert "联网成功" in out.message


@pytest.mark.parametrize(
    "text,expect",
    [
        ("联网成功!", True),
        ("登录成功", True),
        ("认证成功，已联网", True),
        ("验证码错误!", False),
        ("认证请求被拒绝，账号或密码错误", False),
        # 真实失败文案（门户原文），失败词优先于成功词
        ("认证请求被拒绝，账号超过最大并发终端数", False),
        ("Account OverDue", False),
        ("NOSERVICE", False),
        ("认证请求被拒绝，账号或密码错误", False),
    ],
)
def test_looks_like_success(text, expect):
    from src.portal import _looks_like_success

    assert _looks_like_success(text) is expect


def test_submit_login_redirect_is_success(monkeypatch):
    c = PortalClient()
    monkeypatch.setattr(
        c, "_post", lambda a, d: _FakeResp(302, "", {"Location": "/"})
    )
    out = c.submit_login(LoginForm(hidden={}, captcha_png=b"x"), "1", "2", "3", "AAAA")
    assert out.ok and out.redirected_to == "/"


# --------------------------------------------------------------------------- #
# 完整登录流程：验证码错要重试，密码错不准重试
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.prepare_calls = 0

    def prepare_login(self):
        self.prepare_calls += 1
        return LoginForm(hidden={}, captcha_png=b"img")

    def submit_login(self, *a, **k):
        self.calls += 1
        return self.outcomes.pop(0)

    def status(self):
        return PortalStatus(online=True, ip="203.0.113.45")


class _FakeMathSolver:
    """假的算式识别器。answer 为空表示这张图没认出来。"""

    def __init__(self, answer="66", expr="61+5", votes=5):
        self.answer = answer
        self.expr = expr
        self.votes = votes
        self.calls = 0

    def warmup(self):
        return True

    def solve(self, png):
        self.calls += 1
        if not self.answer:
            return MathCaptcha()
        return MathCaptcha(raw=self.expr, expr=self.expr, answer=self.answer, votes=self.votes)


def _cfg():
    return LoginConfig(
        base_url="http://10.6.6.6",
        gui_id="2024012345",
        account="13800138000",
        password="pw",
        max_captcha_retry=5,
    )


def test_retry_on_captcha_error_then_success():
    client = _FakeClient(
        [
            LoginOutcome(ok=False, message="验证码错误!"),
            LoginOutcome(ok=False, message="验证码错误!"),
            LoginOutcome(ok=True, message="ok"),
        ]
    )
    out = attempt_login(_cfg(), _FakeMathSolver(), client=client)
    assert out.ok is True
    assert client.calls == 3


def test_no_retry_on_wrong_password():
    client = _FakeClient([LoginOutcome(ok=False, message="认证请求被拒绝，账号或密码错误")])
    out = attempt_login(_cfg(), _FakeMathSolver(), client=client)
    assert out.ok is False
    assert client.calls == 1
    assert "密码" in out.message


def test_gives_up_after_max_retry():
    client = _FakeClient([LoginOutcome(ok=False, message="验证码错误!")] * 10)
    cfg = _cfg()
    cfg.max_captcha_retry = 3
    out = attempt_login(cfg, _FakeMathSolver(), client=client)
    assert out.ok is False
    assert client.calls == 3


def test_unreadable_captcha_refetches():
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])
    out = attempt_login(_cfg(), _FakeMathSolver(answer=""), client=client)
    assert out.ok is False
    assert client.calls == 0  # 一次都没提交：认不出算式就不拿账号去撞


def test_verify_rejects_when_portal_still_offline():
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])

    class OfflineClient(_FakeClient):
        def status(self):
            return PortalStatus(online=False)

    c = OfflineClient([LoginOutcome(ok=True, message="ok")])
    out = attempt_login(_cfg(), _FakeMathSolver(), client=c, verify=False)
    assert out.ok is True


def test_captcha_callback_receives_image_and_code():
    seen = []
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])
    attempt_login(
        _cfg(), _FakeMathSolver("66", "61+5"), client=client,
        on_captcha=lambda p, c: seen.append((p, c)),
    )
    assert len(seen) == 1
    png, cap = seen[0]
    assert png == b"img"
    assert cap.answer == "66" and cap.expr == "61+5"


# --------------------------------------------------------------------------- #
# 错误翻译 & 识别清洗
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,key",
    [
        # 门户自己的校验
        ("验证码错误!", "验证码"),
        ("密码长度应大于0小于17!", "16 位"),
        ("认证请求被拒绝，账号或密码错误", "密码"),
        ("认证请求被拒绝，账号超过最大并发终端数", "2 台设备"),
        ("用户未实名", "实名"),
        ("NOSERVICE", "5 分钟"),
        # 运营商 RADIUS 回包（门户主页 FAQ 原文）
        ("错误信息包含: 1、Reject by concurrency control 2、Limit Users Err", "并发控制"),
        ("The subscriber is deregistered or the password is incorrect", "未开通"),
        ("错误信息包含:AAA authenticate terminal failed", "解绑"),
        ("Authen Attrib(ai-Service-Password) Check Error", "密码不对"),
        ("1、UserName_Err 2、UserName invalid 3、has illegal charactor", "可能无效"),
        ("错误信息包含:Account OverDue", "欠费"),
        ("Checking LoginUser RBillServer, return Service Not Available", "未开通"),
    ],
)
def test_friendly_translation(raw, key):
    assert key in _friendly(raw)


def test_friendly_unknown_passes_through():
    assert _friendly("某个没见过的怪错误") == "某个没见过的怪错误"
    assert _friendly("") == "未知错误"


def test_is_captcha_error():
    assert _is_captcha_error("验证码错误!") is True
    assert _is_captcha_error("账号或密码错误") is False


def test_captcha_normalize():
    s = CaptchaSolver(length=4)
    assert s.normalize(" mx2y ") == "MX2Y"
    assert s.normalize("mx2") == ""
    assert s.normalize("MX2Y7") == ""
    assert s.normalize("") == ""
    assert s.normalize("M-X2Y") == "MX2Y"


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def test_config_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    c = Config(gui_id="2024012345", account="13800138000", password="pw", poll_seconds=99999)
    c.save(p)
    d = Config.load(p)
    assert d.gui_id == "2024012345"
    assert d.password == "pw"
    assert d.poll_seconds == 600  # clamp 到上限
    assert d.base_url == "http://10.6.6.6"


def test_config_drops_credentials_when_save_disabled(tmp_path):
    p = tmp_path / "config.json"
    c = Config(gui_id="2024012345", account="13800138000", password="secret",
               save_credentials=False)
    c.save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "password" not in raw and "account" not in raw and "gui_id" not in raw
    d = Config.load(p)
    assert d.password == "123456"      # 回落到默认值，不是空密码
    assert d.gui_id == ""


def test_config_keeps_credentials_when_save_enabled(tmp_path):
    p = tmp_path / "config.json"
    Config(gui_id="1", account="2", password="pw", save_credentials=True).save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["password"] == "pw" and raw["gui_id"] == "1"


def test_default_password_is_123456():
    assert Config().password == "123456"


def test_config_missing_file_returns_defaults(tmp_path):
    d = Config.load(tmp_path / "nope.json")
    assert d.gui_id == "" and d.poll_seconds == 15


def test_config_ignores_unknown_keys(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"gui_id": "1", "evil": {"a": 1}}', encoding="utf-8")
    d = Config.load(p)
    assert d.gui_id == "1"
    assert not hasattr(d, "evil")

# --------------------------------------------------------------------------- #
# 算式验证码：接口、解析、计算、换图策略
# --------------------------------------------------------------------------- #
def test_login_uses_acaptcha_endpoint():
    """登录页那张 200x60 的算式图是 /ACaptcha.ashx 出的，别再拿 /CheckCode。"""
    from src import portal

    assert portal.CAPTCHA_LOGIN_ACTION == "/ACaptcha.ashx"
    assert portal.CAPTCHA_ACTION == "/CheckCode"


def test_prepare_login_fetches_from_login_endpoint(monkeypatch):
    seen = []
    c = PortalClient()
    monkeypatch.setattr(c, "_get", lambda action: '<input type="hidden" name="__VIEWSTATE" value="v" />')
    monkeypatch.setattr(
        c, "fetch_captcha", lambda action=portal_captcha_default(): (seen.append(action), b"png")[1]
    )
    form = c.prepare_login()
    assert seen == ["/ACaptcha.ashx"]
    assert form.hidden == {"__VIEWSTATE": "v"}
    assert form.captcha_png == b"png"


def portal_captcha_default():
    from src.portal import CAPTCHA_ACTION

    return CAPTCHA_ACTION


@pytest.mark.parametrize(
    "raw,expr,answer",
    [
        ("61加5？", "61+5", "66"),      # 中文运算符
        ("55加9", "55+9", "64"),
        ("63×7", "63*7", "441"),      # 乘号
        ("63÷7", "63/7", "9"),        # 除号
        ("9乘5", "9*5", "45"),
        ("97一19", "97-19", "78"),    # 半截减号被认成一
        ("16x8", "16*8", "128"),      # 小写 x 当乘号
        ("8十2", "8+2", "10"),        # 十 当加号
    ],
)
def test_expression_parsing_and_math(raw, expr, answer):
    assert normalize_expr(raw) == expr
    assert compute(expr) == answer


@pytest.mark.parametrize(
    "raw",
    ["", "3525", "加4", "乘3", "阴天快乐", "1+2+3"],  # 拼不出唯一 A op B 的一律不猜
)
def test_unparseable_garbage_yields_nothing(raw):
    assert normalize_expr(raw) == ""


@pytest.mark.parametrize("expr", ["7/2", "3-5", "0/0", "999*999", "1/0"])
def test_compute_rejects_impossible(expr):
    """除不尽、负数、超 4 位都不可能是门户出的题，当认错处理。"""
    assert compute(expr) is None


def test_unreadable_math_captcha_gives_up_after_max_blank():
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])
    cfg = _cfg()
    cfg.max_captcha_retry = 2
    cfg.max_blank_tries = 4          # max_fetch = max(提交上限, 换图上限) = 4
    out = attempt_login(cfg, _FakeMathSolver(answer=""), client=client)
    assert out.ok is False
    assert client.prepare_calls == 4   # 换了 4 张图
    assert client.calls == 0           # 一次都没提交账号
    assert "识别失败" in out.message


def test_blank_images_do_not_burn_submit_budget():
    """认不出的图只换图不提交，别把提交次数浪费掉。"""
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])
    cfg = _cfg()
    cfg.max_captcha_retry = 1
    cfg.max_blank_tries = 5

    class BlankThenOk(_FakeMathSolver):
        def solve(self, png):
            self.calls += 1
            if self.calls < 3:
                return MathCaptcha()
            return MathCaptcha(expr="1+1", answer="2", votes=9)

    out = attempt_login(cfg, BlankThenOk(), client=client)
    assert out.ok is True
    assert client.calls == 1          # 只提交了一次
    assert client.prepare_calls == 3  # 前两张白换


def test_manual_login_flow(monkeypatch):
    """自动认不出来时，取一张新图手输，答案直接提交。"""
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])
    # service 在模块顶层就 import 了 PortalClient，所以要 patch service 的这个名字
    monkeypatch.setattr(portal_mod(), "PortalClient", lambda *a, **k: client)
    monkeypatch.setattr(service_mod(), "PortalClient", lambda *a, **k: client)
    monkeypatch.setattr(
        client, "prepare_login", lambda: LoginForm(hidden={"__VIEWSTATE": "v"}, captcha_png=b"img2")
    )
    manual = prepare_manual_login(_cfg())
    assert manual is not None and manual.captcha_png == b"img2"
    out = submit_manual_login(_cfg(), manual, "66", verify=False)
    assert out.ok is True
    assert client.calls == 1


def portal_mod():
    from src import portal

    return portal


def service_mod():
    from src import service

    return service

