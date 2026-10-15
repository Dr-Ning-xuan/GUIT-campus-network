"""桂林信息科技学院 多运营商接入管理平台 —— 门户 HTTP 客户端。

纯协议实现，不依赖浏览器：
    取登录页 -> 取验证码 -> POST 登录 / POST 注销 / 查询在线状态

实测要点（2026-10-15）：
  * 站点是 ASP.NET WebForms，POST 必须原样回传 __VIEWSTATE / __EVENTVALIDATION。
  * /CheckCode 每请求一次就生成一张新图并覆盖服务端 session 里的答案，
    所以「取图 -> 识别 -> 立刻提交」必须走同一个 requests.Session。
  * /CheckCode 必须带 no-cache，否则可能命中中间层缓存拿到旧图（验证码必错）。
  * 登录失败返回 200 且错误文本在 <p class="validation-summary-errors"> 里
    （例如 "验证码错误!"），不是靠状态码判断。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

import requests

DEFAULT_BASE = "http://10.6.6.6"

LOGIN_ACTION = "/login"
LOGOFF_ACTION = "/logoff"
CHECKING_ACTION = "/checking"
CAPTCHA_ACTION = "/CheckCode"

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class PortalError(Exception):
    """门户不可达 / 响应异常。"""


# --------------------------------------------------------------------------- #
# HTML 解析
# --------------------------------------------------------------------------- #
class _PageParser(HTMLParser):
    """抽取 hidden 字段、错误提示、图片。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: dict[str, str] = {}
        self.images: list[dict[str, str]] = []
        self.errors: list[str] = []
        self._in_error_block = 0
        self._error_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "input":
            t = a.get("type", "text").lower()
            name = a.get("name", "")
            if t == "hidden" and name:
                self.hidden[name] = a.get("value", "")
        elif tag == "img":
            self.images.append(a)
        if tag == "p" and "validation-summary-errors" in a.get("class", ""):
            self._in_error_block += 1
            self._error_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._in_error_block:
            self._in_error_block -= 1
            text = " ".join("".join(self._error_parts).split())
            if text:
                self.errors.append(text)

    def handle_data(self, data: str) -> None:
        if self._in_error_block:
            self._error_parts.append(data)


# 判定服务端提示语是「成功」还是「失败」。失败词优先，避免「登录成功但...」这类误判。
_FAILURE_WORDS = (
    "失败", "错误", "拒绝", "无效", "超时", "未开通", "未实名", "欠费",
    "并发", "最大终端", "已在线", "err", "reject", "deny", "overdue",
    "timeout", "noservice", "limit exceed",
)
_SUCCESS_WORDS = ("成功", "已联网", "登录成功", "上线")


def _looks_like_success(text: str) -> bool:
    t = (text or "").lower()
    if any(w in t for w in _FAILURE_WORDS):
        return False
    return any(w in t for w in _SUCCESS_WORDS)


def _parse_page(raw_html: str) -> _PageParser:
    p = _PageParser()
    try:
        p.feed(raw_html)
        p.close()
    except Exception:
        pass
    return p


def _plain_text(raw_html: str) -> str:
    t = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.I)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(t.split())


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class LoginForm:
    """一次登录所需的一次性材料。"""

    hidden: dict[str, str]
    captcha_png: bytes

    @property
    def captcha_size(self) -> tuple[int, int]:
        return (len(self.captcha_png), 0)


@dataclass
class LoginOutcome:
    ok: bool
    message: str = ""
    errors: list[str] = field(default_factory=list)
    status_code: int = 0
    redirected_to: str = ""


@dataclass
class PortalStatus:
    online: bool
    ip: str = ""
    text: str = ""
    reachable: bool = True


@dataclass
class CheckResult:
    ok: bool
    fields: dict[str, str] = field(default_factory=dict)
    message: str = ""


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class PortalClient:
    BASE = DEFAULT_BASE

    def __init__(self, base_url: str = DEFAULT_BASE, timeout: float = 10.0) -> None:
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": DEFAULT_UA, **_NO_CACHE_HEADERS})

    # -- 内部 ---------------------------------------------------------------- #
    def _get(self, action: str) -> str:
        try:
            r = self.session.get(self.base + action, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PortalError(f"无法连接认证服务器 {self.base}：{exc}") from exc
        r.encoding = "utf-8"
        return r.text

    def _post(self, action: str, data: dict) -> requests.Response:
        headers = {"Referer": self.base + action, "Origin": self.base}
        try:
            r = self.session.post(
                self.base + action,
                data=data,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise PortalError(f"提交 {action} 失败：{exc}") from exc
        r.encoding = "utf-8"
        return r

    # -- 公共 API ------------------------------------------------------------ #
    def prepare_login(self) -> LoginForm:
        """取登录页隐藏字段 + 一张全新验证码（同一 session）。"""
        page = self._get(LOGIN_ACTION)
        hidden = dict(_parse_page(page).hidden)
        captcha = self.fetch_captcha()
        return LoginForm(hidden=hidden, captcha_png=captcha)

    def fetch_captcha(self) -> bytes:
        """单独取一张新验证码（会覆盖服务端 session 里的答案）。

        带随机 query 做 cache-buster：实测偶发撞到过同一张图（字节流完全一致），
        加这个参数对服务端无影响，但能挡掉中间层缓存拿到旧图。
        """
        url = f"{self.base}{CAPTCHA_ACTION}?t={random.random():.16f}"
        try:
            r = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PortalError(f"获取验证码失败：{exc}") from exc
        if r.status_code != 200 or len(r.content) < 100:
            raise PortalError(f"验证码响应异常：HTTP {r.status_code}, {len(r.content)} 字节")
        return r.content

    def submit_login(
        self,
        form: LoginForm,
        gui_id: str,
        account: str,
        password: str,
        captcha: str,
        isp: str = "自动选择",
    ) -> LoginOutcome:
        data = dict(form.hidden)
        data.update(
            {
                "ctl00$MainContent$TextBoxISP": isp or "自动选择",
                "ctl00$MainContent$SSOGUITID": gui_id,
                "ctl00$MainContent$SSOACC": account,
                "ctl00$MainContent$SSOPWD": password,
                "ctl00$MainContent$TextBoxCC": captcha,
                "ctl00$MainContent$LoginBtn": "登录",
            }
        )
        r = self._post(LOGIN_ACTION, data)
        return self._judge(r)

    def _judge(self, r: requests.Response) -> LoginOutcome:
        if r.status_code in (301, 302, 303, 307):
            return LoginOutcome(
                ok=True,
                message="登录成功（服务器已跳转）",
                status_code=r.status_code,
                redirected_to=r.headers.get("Location", ""),
            )
        page = _parse_page(r.text)
        errors = page.errors
        if errors:
            text = " / ".join(errors)
            # 实测：门户成功时也把「联网成功!」渲染进 <p class="validation-summary-errors">，
            # 所以不能见到这个块就判失败，必须先排除失败词再认成功词。
            if _looks_like_success(text):
                return LoginOutcome(ok=True, message=text, status_code=r.status_code)
            return LoginOutcome(
                ok=False, message=text, errors=errors, status_code=r.status_code
            )
        # 没有错误块：可能是成功页，也可能本轮没渲染错误 —— 交给调用方用 status() 复核
        return LoginOutcome(ok=True, message="已提交，未返回错误", status_code=r.status_code)

    def status(self) -> PortalStatus:
        """查在线状态：解析 /logoff 页的『当前在线状态 已联网/未联网』。"""
        try:
            page = self._get(LOGOFF_ACTION)
        except PortalError:
            return PortalStatus(online=False, reachable=False, text="认证服务器不可达")
        text = _plain_text(page)
        ip = ""
        m = re.search(r"当前用户IP[:\s]*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", text)
        if m:
            ip = m.group(1)
        online = False
        m2 = re.search(r"当前在线状态\s*(已联网|未联网|已断开|未知)", text)
        if m2:
            online = m2.group(1) == "已联网"
        elif "已联网" in text and "未联网" not in text:
            online = True
        return PortalStatus(online=online, ip=ip, text=text)

    def prepare_logoff(self) -> LoginForm:
        page = self._get(LOGOFF_ACTION)
        hidden = dict(_parse_page(page).hidden)
        captcha = self.fetch_captcha()
        return LoginForm(hidden=hidden, captcha_png=captcha)

    def submit_logoff(self, form: LoginForm, captcha: str) -> LoginOutcome:
        data = dict(form.hidden)
        data.update(
            {
                "ctl00$MainContent$TextBoxCC": captcha,
                "ctl00$MainContent$LogoffBtn": "注销",
            }
        )
        r = self._post(LOGOFF_ACTION, data)
        return self._judge(r)

    def query_user(self, gui_id: str, account: str, id6: str, captcha: str) -> CheckResult:
        """信息验证页：查询实名/在网信息（只读用途）。"""
        page = self._get(CHECKING_ACTION)
        hidden = dict(_parse_page(page).hidden)
        data = dict(hidden)
        data.update(
            {
                "ctl00$MainContent$TextBoxISP": "N/A",
                "ctl00$MainContent$TextBoxRoom": "N/A",
                "ctl00$MainContent$TextBoxName": "N/A",
                "ctl00$MainContent$TextBoxGUITID": gui_id,
                "ctl00$MainContent$TextBoxACC": account,
                "ctl00$MainContent$TextBoxCID6": id6,
                "ctl00$MainContent$TextBoxCC": captcha,
                "ctl00$MainContent$CheckBtn": "检查",
            }
        )
        r = self._post(CHECKING_ACTION, data)
        p = _parse_page(r.text)
        if p.errors:
            return CheckResult(ok=False, message=" / ".join(p.errors))
        fields: dict[str, str] = {}
        for m in re.finditer(
            r'name="([^"]+)"[^>]*value="([^"]*)"[^>]*readonly', r.text, re.I
        ):
            fields[m.group(1)] = m.group(2)
        return CheckResult(ok=True, fields=fields, message="OK")


# --------------------------------------------------------------------------- #
# 外网连通性（可选二次确认）
# --------------------------------------------------------------------------- #
_INTERNET_PROBES = (
    "http://connect.rom.miui.com/generate_204",
    "http://www.msftconnecttest.com/connecttest.txt",
)


def probe_internet(timeout: float = 5.0) -> bool:
    """真正能上外网？用 captive-portal 探测点，被劫持/跳转都算不通。"""
    for url in _INTERNET_PROBES:
        try:
            r = requests.get(
                url, timeout=timeout, allow_redirects=False, headers={"User-Agent": DEFAULT_UA}
            )
        except requests.RequestException:
            continue
        if "generate_204" in url and r.status_code == 204:
            return True
        if "connecttest" in url and r.status_code == 200 and b"Microsoft Connect Test" in r.content:
            return True
    return False
