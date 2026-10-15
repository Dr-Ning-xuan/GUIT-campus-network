# 校园网一键联网（桂林信息科技学院）
说真的你信科无敌了，天价网费还限制设备。没事还非要折腾这玩意干什么。
给 [10.6.6.6](http://10.6.6.6) 的「多运营商接入管理平台统一认证系统」做的桌面自动登录器喵~

- **纯协议**登录（不驱动浏览器）：取图 → ddddocr 离线识别验证码 → POST 表单
- 验证码识别错了**自动换一张重试**；账号密码这类硬错误立刻停手，不会把你的号锁了
- 托盘常驻 + 断网自动重连 + 开机自启 + 一键断开
- 界面显示当前 IP / 在线状态 / 登录日志

## 用哪个版本

| 版本 | 形态 | 说明 |
| --- | --- | --- |
| **onedir**（推荐） | 一个文件夹 | **已验证在中文桌面路径下可直接运行**。解压后双击文件夹里的 校园网一键联网.exe |
| onefile | 单个 校园网一键联网.exe | 更干净，但启动时要先把 186MB 解压到 **TEMP**。TEMP 可写就能用；受限环境下会静默退出（见下） |

## 实测结论（2026-10-15，真机）

| 项 | 结果 |
| --- | --- |
| 登录接口 | POST http://10.6.6.6/login （ASP.NET WebForms） |
| 必带隐藏字段 | __VIEWSTATE / __VIEWSTATEGENERATOR / __EVENTVALIDATION |
| 账号字段 | SSOGUITID(学工号) / SSOACC(上网账号) / SSOPWD(密码) / TextBoxCC(验证码) |
| 长度上限 | 密码 ≤ 16、学工号 ≤ 12、上网账号 ≤ 12（**服务端强制**，界面已同步限制） |
| 验证码 | GET /CheckCode，80×24 PNG，4 位；**每请求一次换一张**，必须边取边提交 |
| 缓存 | 必须带 no-cache，否则可能拿到旧图；代码里另加了随机 query 做 cache-buster |
| 失败判定 | HTTP 200 + validation-summary-errors 里的中文提示 |
| 在线状态 | GET /logoff 页的「当前在线状态 已联网/未联网」 |
| 识别率 | 20/20 张全对，**9 ms/张**（ddddocr，离线） |
| 链路验证 | 假账号跑 3 次，服务端回「密码长度应大于0小于17!」而不是「验证码错误」→ 取图→识别→提交链路健康 |

错误文案翻译覆盖了门户主页 FAQ 的全部条目（并发超限、未实名、欠费、绑定错误、RADIUS 各类回包等），见 service._ERROR_RULES。

## onefile 的已知坑（踩过，记录一下）

PyInstaller onefile 的 bootloader 启动时要：

1. 取 TEMP（GetTempPath()）
2. 在下面建 _MEIxxxxxx 目录，**并给它设置安全描述符**
3. 解压 → 启动子进程

**第 2 步需要目标目录的 WRITE_DAC 权限。** 实测本机现象：

失败时 **Python 一行都不会执行**，连 _boot.log 都写不出来 —— 所以入口 run.py 特意加了「每走一步实时落盘」的取证钩子。

**结论：onedir 不涉及解压，永远安全；onefile 只在 TEMP 可写（有 WRITE_DAC）时能用。**

## 目录

~~~
campus-login/
├─ run.py                 # 打包入口（含崩溃取证钩子）
├─ requirements.txt
├─ build.ps1              # 一键打包（自检 → 图标 → PyInstaller → 产物验证）
├─ src/
│  ├─ portal.py           # 门户 HTTP 客户端（登录/注销/状态/外网探测）
│  ├─ ocr.py              # ddddocr 懒加载 + 结果清洗
│  ├─ service.py          # 登录编排（验证码重试策略）+ 自动重连线程
│  ├─ store.py            # config.json 读写
│  ├─ autostart.py        # 注册表开机自启
│  ├─ gui.py              # PySide6 界面 + 托盘
│  └─ main.py             # 入口（含 --selftest）
├─ tests/test_portal.py   # 离线自检（不碰网络）
└─ tools/
   ├─ make_assets.py      # 从素材图生成 icon.ico + 两个状态动图（改图只跑它）
   ├─ verify_exe.py       # 产物级验证（跑 exe --selftest）
   ├─ captcha_bench.py    # 验证码识别率压测
   ├─ cache_probe.py      # 验证码缓存探测
   ├─ probe_login.py      # 真机链路验证（默认假账号）
   ├─ screenshot_gui.py   # 界面截图
   └─ make_montage.py     # 把验证码样本拼成一张图人工核对
~~~

## 开发 / 打包

~~~
.venv/Scripts/python.exe run.py              # 源码跑
.venv/Scripts/python.exe -m pytest -q        # 离线自检
pwsh -File build.ps1 -SkipInstall            # 打包 onefile
pwsh -File build.ps1 -SkipInstall -Onedir    # 打包 onedir（推荐）
~~~

装依赖用 **uv**（.venv/Scripts/uv.exe）—— pip 24 在这台机器上对着 PySide6 的依赖树解析 10 分钟都没收敛，uv 用 14 秒搞定：

~~~
$env:UV_CACHE_DIR = 'D:/DSH/campus-login/.uv-cache'
.venv/Scripts/uv.exe pip install --python .venv/Scripts/python.exe -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
~~~

## 界面

- 图标（窗口 / 任务栏 / 托盘）取自 `assets/icon.ico`；托盘图标右下角会叠一颗绿/红点表示在线状态。
- 「运行选项」框右侧是状态立绘：**未联网播 `state-offline.gif`（委屈），已联网切 `state-online.gif`（得意）**，下面是联网状态、当前 IP 和刷新按钮。
- 「保存账号密码」勾上才把学工号/账号/密码写进 config.json；**不勾时这三个 key 会从文件里整个删掉**（不是写空串），下次打开回落到默认密码 `123456`。
- 想换图：把新图丢进素材目录（默认 `D:\Desktop\新建文件夹`，1/2/3 三张），跑
  ``.venv\Scripts\python.exe tools/make_assets.py`，再重新打包即可。

## 配置

exe 同目录的 config.json（**明文保存密码**，别把这文件发人喵）：

~~~
{
  "base_url": "http://10.6.6.6",
  "gui_id": "学(工)号",
  "account": "上网账号",
  "password": "密码",
  "auto_reconnect": true,
  "poll_seconds": 15
}
~~~
