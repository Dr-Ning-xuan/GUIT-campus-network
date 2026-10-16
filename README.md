# 校园网一键联网（桂林信息科技学院）
说真的你信科无敌了，天价网费还限制设备。没事还非要折腾这玩意干什么。

<img width="482" height="597" alt="QQ_1792117708158" src="https://github.com/user-attachments/assets/30be85b4-b60e-4506-bfe2-2df861ff0f5d" />
<img width="482" height="597" alt="QQ_1792117654520" src="https://github.com/user-attachments/assets/24f588bc-e1f0-426d-866e-28717923ff74" />
<img width="482" height="597" alt="QQ_1792127536265" src="https://github.com/user-attachments/assets/f9b5449a-b25b-4829-9c5c-c5ab3856511e" />

# GUIT 校园网一键联网

一个给校园网 Web 认证门户用的 Windows 桌面小工具。填好账号密码，点一下就连上；
验证码自动识别，断网自动重连，可以缩在托盘里常驻。

> 纯 HTTP 协议实现，**不驱动浏览器**：直接取验证码图片 → 离线 OCR → POST 表单。



<sub>未联网 / 已联网 / 联网失败 三种状态右侧会切换不同立绘</sub>

## 功能

- **验证码离线识别**：内置 ddddocr，识别错了自动换一张重试（默认最多 6 次）
- **错误停手保护**：账号/密码这类硬错误立刻停止，不会反复重试把账号锁掉
- **断网自动重连**：后台按设定间隔轮询，掉线自动补登录
- **开机自启 + 托盘常驻**：启动即最小化到托盘，不占任务栏
- **一键断开**：需要切换账号时直接注销当前认证
- **界面状态**：显示当前 IP、在线状态、登录日志；右下角立绘随状态切换（未联网 / 已联网 / 联网失败）
- **网络测速入口**：一键打开测速页面

## 快速开始

### 直接下载

到 [Releases](../../releases) 页面下载，两种形态任选：

| 文件 | 说明 |
| --- | --- |
| `GUIT校园网一键联网.exe` | **单文件版**：双击即用。首次启动需要解压，慢几秒 |
| `GUIT校园网一键联网-文件夹版.zip` | **文件夹版**：解压后双击里面的 exe。启动快，兼容性最好 |

### 从源码运行

需要 Python 3.11。

```powershell
python -m venv .venv

# 依赖里有 PySide6，pip 解析会比较慢；用 uv 快很多
$env:UV_CACHE_DIR = ".\.uv-cache"
.venv\Scripts\uv.exe pip install --python .venv\Scripts\python.exe -r requirements.txt

.venv\Scripts\python.exe run.py
```

### 跑测试 / 打包

```powershell
.venv\Scripts\python.exe -m pytest      # 48 项离线测试，不碰网络
pwsh -File build.ps1                     # 打包（默认单文件版）
pwsh -File build.ps1 -Onedir             # 打包成文件夹版
pwsh -File build\ship.ps1                # 打包 + 验证 + 复制到桌面
```

打包完会跑一次产物级验证：直接执行 `exe --selftest`，确认依赖、OCR 和 GUI 都能加载。

## 工作原理

适配的是 ASP.NET WebForms 的认证门户（IIS + `.aspx`）。关键点：

- 每次 POST 都要带上**上一次 GET** 里拿到的 `__VIEWSTATE`、`__VIEWSTATEGENERATOR`、
  `__VIEWSTATEENCRYPTED`、`__EVENTVALIDATION`，外加同一会话的 Cookie
- 验证码是独立接口，**每次请求都重新生成**，所以必须"取图 → 识别 → 立刻提交"，不能缓存
- 服务端**先校验验证码、再校验密码长度**；密码有长度上限
- 失败和成功**都返回 HTTP 200**，只能靠页面文本判断

登录字段（可按自己学校的门户调整）：

```
__VIEWSTATE / __VIEWSTATEGENERATOR / __VIEWSTATEENCRYPTED / __EVENTVALIDATION
ctl00$MainContent$TextBoxISP   运营商（默认 自动选择）
SSOGUITID                      学(工)号（≤12 位）
SSOACC                         上网账号（≤12 位）
SSOPWD                         密码（≤16 位）
TextBoxCC                      验证码（4 位）
CheckBoxSaveMe / LoginBtn      记住我 / 登录
```

在线状态从注销页读取，页面里会给出「当前用户 IP」和「已联网 / 未联网」。

### 判断成败的一个坑

有的门户会把自己渲染的成功提示也塞进 `<p class="validation-summary-errors">` 里，
只看这个 class 会把成功当失败。这里的做法是**失败词优先**：
先匹配一批失败关键词（失败 / 错误 / 拒绝 / 无效 / 超时 / 未开通 / 欠费 / 并发 / 已在线 / …），
没有命中失败词、且出现成功词（成功 / 已联网 / 上线）才判定成功。

## 配置

程序同目录的 `config.json`（**明文保存密码**，注意别把这个文件发出去）：

```json
{
  "base_url": "http://10.6.6.6",
  "gui_id": "学工号",
  "account": "上网账号",
  "password": "密码",
  "isp": "自动选择",
  "auto_reconnect": true,
  "auto_connect_on_start": true,
  "poll_seconds": 15,
  "autostart": true,
  "save_credentials": true
}
```

- 取消勾选「保存账号密码」时，`gui_id` / `account` / `password` 三个 key 会从文件里**整个删掉**，
  下次启动回落到默认值，而不是留一个空密码
- `config.json`、`_boot.log`、`_selftest.log` 写完后会自动加上**隐藏属性**，不碍眼

## 目录结构

```
.
├─ run.py                打包入口（运行时先写 _boot.log，崩了也有据可查）
├─ build.ps1             一键打包
├─ src/
│   ├─ version.py        版本号（唯一来源，打包和界面都读它）
│   ├─ portal.py         门户协议层：取图 / 识别 / 登录 / 注销 / 状态
│   ├─ ocr.py            ddddocr 懒加载与结果清洗
│   ├─ service.py        登录编排（验证码重试）+ 断网自动重连线程
│   ├─ store.py          配置读写 + 文件隐藏
│   ├─ autostart.py      注册表开机自启
│   ├─ gui.py            PySide6 界面 + 托盘
│   └─ main.py           入口（含 --selftest）
├─ tools/
│   ├─ make_assets.py    从素材图生成图标 + 状态立绘
│   ├─ make_version.py   生成 exe 版本资源
│   ├─ verify_exe.py     产物级验证（跑 exe --selftest）
│   ├─ captcha_bench.py  识别率压测
│   ├─ cache_probe.py    验证码缓存探测
│   ├─ probe_login.py    真机链路验证（默认假账号）
│   ├─ screenshot_gui.py 界面截图
│   └─ make_montage.py   验证码样本拼图
├─ tests/test_portal.py  48 项离线测试
├─ build/ship.ps1        打包 + 验证 + 交付
└─ assets/               图标、状态立绘、验证码样本
```

## 打包形态怎么选

| | 文件夹版 `--onedir` | 单文件版 `--onefile` |
| --- | --- | --- |
| 体积 | 约 380 MB（600+ 个文件） | 约 188 MB |
| 启动 | 秒开 | 每次启动都要解压到 `%TEMP%`，慢几秒 |
| 开机自启 | 推荐 | 解压期间会让人以为没启动 |
| 对 `%TEMP%` 的要求 | 无 | 需要可写；权限受限时会静默退出 |

**结论**：要开机自启就用文件夹版；单文件版适合临时拷给别人用。

## 常见问题

**Q：验证码老认错？**
程序会自动换一张重试（默认 6 次），仍不行说明站点换了验证码类型或者识别库不适配，
可以用 `tools/captcha_bench.py` 批量测一下识别率。

**Q：开机自启没反应？**
先确认注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 里有对应的项，
再看任务管理器 → 启动应用里有没有被禁用。如果用的是单文件版，
启动时要在 `%TEMP%` 里解压，慢几秒是正常的。

**Q：单文件版双击没反应？**
看看 exe 旁边有没有生成 `_boot.log`。如果启动器报
`Could not create temporary directory!`，说明 `%TEMP%` 不可写，换文件夹版即可。

## 免责声明

本项目仅供**学习与个人使用**。使用前请确认符合你所在学校/单位的网络使用规定；
请只使用**自己的**账号凭据。作者不对任何滥用行为或由此产生的后果负责。

## 更新日志

### v1.2.0

**新增**

- **启动时自动联网**：打开程序先查一次状态，没在线就自动连上。可在「运行选项」里关闭。
- **网络测速**：主界面新增「网络测速」按钮，一键打开测速页面。
- **联网失败立绘**：右侧立绘从两态扩展为三态 —— 未联网 / 已联网 / 联网失败。
  登录失败时会切换到出错表情并停留，不会立刻被状态查询覆盖掉。

**修复**

- **开机自启**：自启项改为优先指向**文件夹版**。
  单文件版每次启动都要把约 188 MB 解压到 `%TEMP%`，而开机瞬间磁盘正忙，
  托盘可能几十秒才出现，容易被误认为「自启没生效」（实测文件夹版 0.8 秒、单文件版 3.9 秒）。
  程序启动时还会自动把指向单文件版的旧自启项纠正过来。

### v1.1.0

**新增**

- **自定义图标**：窗口 / 任务栏 / 托盘统一使用自绘图标，托盘图标右下角叠加在线状态点（绿=在线，红=离线）。
- **「保存账号密码」开关**：取消勾选时，`gui_id` / `account` / `password` 三个 key 会从 `config.json` 里**整个移除**，
  而不是留一个空密码，下次启动回落到默认值。
- **状态立绘**：右侧显示随联网状态切换的动画立绘（未联网委屈 / 已联网得意）。
- **「最小化」按钮**：收进托盘继续运行，断网自动重连不受影响。
- **exe 版本资源**：文件说明 / 文件版本 / 产品名称 / 版权等属性完整填写。

**改进**

- 状态文字放大，刷新按钮移到状态文字旁边，界面更紧凑。
- `config.json`、`_boot.log`、`_selftest.log` 写完后自动加**隐藏属性**，不再散落在桌面上。
- 版本号收敛到 `src/version.py` 单一来源，打包脚本自动同步到 exe 属性和窗口标题。

### v1.0.0

- 首个版本。

**功能**

- 纯 HTTP 协议登录（不驱动浏览器）：取验证码图片 → 离线 OCR → POST 表单。
- 验证码识别错误自动换一张重试；账号/密码这类硬错误立刻停手，不反复重试。
- 断网自动重连、开机自启、托盘常驻、一键断开。
- 界面显示当前 IP、在线状态和登录日志。

**修复**

- 门户会把**成功提示**也渲染进 `<p class="validation-summary-errors">`，导致登录成功却被判定为失败。
  改为**失败词优先**判断：先匹配失败关键词，没有命中且出现成功词才判定成功。
## License

MIT

