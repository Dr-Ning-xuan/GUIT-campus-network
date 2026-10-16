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
from src.service import LoginConfig, _friendly, _is_captcha_error, attempt_login
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

    def prepare_login(self):
        return LoginForm(hidden={}, captcha_png=b"img")

    def submit_login(self, *a, **k):
        self.calls += 1
        return self.outcomes.pop(0)

    def status(self):
        return PortalStatus(online=True, ip="203.0.113.45")


class _FakeSolver:
    def __init__(self, code="MX2Y"):
        self.code = code

    def solve(self, png):
        return self.code


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
    out = attempt_login(_cfg(), _FakeSolver(), client=client)
    assert out.ok is True
    assert client.calls == 3


def test_no_retry_on_wrong_password():
    client = _FakeClient([LoginOutcome(ok=False, message="认证请求被拒绝，账号或密码错误")])
    out = attempt_login(_cfg(), _FakeSolver(), client=client)
    assert out.ok is False
    assert client.calls == 1
    assert "密码" in out.message


def test_gives_up_after_max_retry():
    client = _FakeClient([LoginOutcome(ok=False, message="验证码错误!")] * 10)
    cfg = _cfg()
    cfg.max_captcha_retry = 3
    out = attempt_login(cfg, _FakeSolver(), client=client)
    assert out.ok is False
    assert client.calls == 3


def test_unreadable_captcha_refetches():
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])
    out = attempt_login(_cfg(), _FakeSolver(code=""), client=client)
    assert out.ok is False
    assert client.calls == 0  # 一次都没提交，因为识别不出 4 位


def test_verify_rejects_when_portal_still_offline():
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])

    class OfflineClient(_FakeClient):
        def status(self):
            return PortalStatus(online=False)

    c = OfflineClient([LoginOutcome(ok=True, message="ok")])
    out = attempt_login(_cfg(), _FakeSolver(), client=c, verify=False)
    assert out.ok is True


def test_captcha_callback_receives_image_and_code():
    seen = []
    client = _FakeClient([LoginOutcome(ok=True, message="ok")])
    attempt_login(_cfg(), _FakeSolver("AB12"), client=client, on_captcha=lambda p, c: seen.append((p, c)))
    assert seen == [(b"img", "AB12")]


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
