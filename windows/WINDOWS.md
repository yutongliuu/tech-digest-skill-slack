# Windows 安装手册

> 本手册专为 **Windows + 公司网络（Netskope 等代理）**环境写。
> macOS / Linux 用户请看 [README.md](./README.md)。
>
> 适用范围：本地 Windows 电脑 24 小时不一定开着。本方案用「任务计划程序」配每天 10:00 推送，**错过会在开机后自动补跑**（10 点电脑没开也没关系，开机就补）。

---

## 你能得到什么

```
每天 10:00 (或开机后第一次)
   │
   ▼
Windows 任务计划程序自动触发
   │
   ▼
抓取 HF / GitHub / Anthropic → DeepSeek 重排和摘要 → Slack 私聊推送
```

- 同时支持手动触发：随时在终端跑一条命令立即推一次
- 不需要服务器、不需要 24 小时不关机
- 项目自包含公司证书，pip / 联网请求都能透过 Netskope

---

## 准备清单

- [ ] **Windows 10/11**
- [ ] **能登录公司 Slack**（个人工作区也行）+ 已创建 Slack App、拿到两把 Token（`xoxb-` / `xapp-`），具体看 [README.md「前提条件」](./README.md#前提条件)
- [ ] **DeepSeek API Key** 或其他 OpenAI 兼容接口
- [ ] **管理员权限**（装 Python、注册定时任务时需要 UAC 弹窗确认）
- [ ] **30-45 分钟**

---

## 第 1 步：安装 Python

打开 **PowerShell**（开始菜单搜 PowerShell），运行：

```powershell
winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
```

装完**关闭并重开 PowerShell**（让新命令生效），验证：

```powershell
python --version    # 应输出 Python 3.12.x
```

> ❓ **`python` 找不到？**
> winget 装的 Python 在 `%LOCALAPPDATA%\Programs\Python\Python312-arm64\`（ARM 机器）或 `Python312\`。重开 PowerShell 通常 PATH 会刷新；还不行就用绝对路径：`& "$env:LOCALAPPDATA\Programs\Python\Python312-arm64\python.exe" --version`

---

## 第 2 步：拿到项目代码

把整个 `tech-digest-skill-slack` 文件夹放到本地，建议路径：

```
C:\Users\<你>\tech-digest-skill-slack\
```

> 拿代码的方式可以是：从 GitHub 下载 ZIP、`git clone`、或者直接拷贝文件夹。

进入项目目录：

```powershell
cd C:\Users\<你>\tech-digest-skill-slack
```

---

## 第 3 步：处理公司证书（Netskope）

公司网络用 **Netskope** 给所有 HTTPS 流量做中间人解密，pip 和 Python 联网会因为不认识 Netskope 证书而失败。我们要把它**导出来**，放进项目里。

> 💡 如果你**之前装 npm 时已经导出过**这个证书（在 `claude-slack-bot\netskope.crt`），直接复制过来用即可：
> ```powershell
> copy C:\Users\<你>\claude-slack-bot\netskope.crt .
> ```
> 然后**跳到第 4 步**。

### 3.1 检查电脑里的公司证书

```powershell
Get-ChildItem -Path Cert:\LocalMachine\Root |
  Where-Object { $_.Subject -match "Netskope|Zscaler|Bluecoat|Forcepoint" } |
  Select-Object Subject, Thumbprint
```

应该看到一行类似：
```
Subject:    CN=certadmin, O=Netskope Inc., ...
Thumbprint: DD8D2A596CAB36EDF89B1B0904A6EA57C169E371
```

**记下 Thumbprint**。如果没有任何输出，说明你公司没装这类代理，跳到第 4 步即可。

### 3.2 导出证书到项目里

把上面的 Thumbprint 替换进下面这段，然后跑：

```powershell
$thumbprint = "你刚记下的Thumbprint"
$cert = Get-ChildItem -Path Cert:\LocalMachine\Root | Where-Object { $_.Thumbprint -eq $thumbprint }
$base64 = [System.Convert]::ToBase64String($cert.RawData, 'InsertLineBreaks')
"-----BEGIN CERTIFICATE-----`r`n$base64`r`n-----END CERTIFICATE-----" |
  Set-Content -Path ".\netskope.crt" -Encoding ASCII
Write-Output "导出完成: $(Resolve-Path .\netskope.crt)"
```

看到 "导出完成" 就行。

> 之后所有 PowerShell 脚本会**自动读这个文件**让 pip 和 `requests` 库信任公司证书，你不用每次手动设环境变量。

---

## 第 4 步：填配置 `.env`

```powershell
copy .env.example .env
notepad .env
```

把 4 项必填值填进去（细节见 [README「前提条件」](./README.md#前提条件)）：

```
SLACK_BOT_TOKEN=xoxb-...你的Bot Token
SLACK_APP_TOKEN=xapp-1-...你的App Token
SLACK_DEFAULT_USERS=U07XXXXXXX        # 你自己的 Slack 用户 ID
GENREC_API_KEY=sk-...DeepSeek Key
```

> 📌 **怎么查自己的 Slack 用户 ID**：Slack 里点你的头像 → Profile → 右上角「⋮ More」→ "Copy member ID"，得到 `U` 开头那串。

> ⏰ **想改每日推送时间**：`.env` 里有一行 `PUSH_TIME=10:00`（24 小时制）。改成你想要的点，比如 `PUSH_TIME=08:30`。用的是**你这台电脑显示的本地时间**——电脑几点就几点推。改完在第 5 步重跑 `install.ps1` 生效。

填完保存关闭。

---

## 第 5 步：一键装好 + 设定时任务

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1
```

它会：
1. 检测 Python
2. 用公司证书装好 4 个 Python 包（`requests` / `beautifulsoup4` / `slack_bolt` / `slack_sdk`）
3. 在 Windows 任务计划程序里注册 **TechDigest-Daily** 任务
   - 每天 **10:00** 自动跑
   - **错过的话开机后尽快补跑**
   - 只在**有网络**时跑

> ⚠️ 注册定时任务时如果弹 **UAC** 让你确认，点 **Yes**。

完成后会显示 `=== 安装完成 ===`。

---

## 第 6 步：手动跑一次验证

不等明天 10 点，立即测一下：

```powershell
powershell -ExecutionPolicy Bypass -File windows\run_daily.ps1
```

正常会看到：

```
=== tech-digest 推送（Windows）===
Python    : C:\Users\...\python.exe
数据目录  : C:\Users\<你>\.tech-digest
[1/2] 抓取 + 重排...
[2/2] 生成摘要 + 发送到 Slack...
{"sent":1,"failed":0,"skipped":0}
done: ...\recommendations.jsonl
```

然后**打开 Slack** —— 你应该收到 `tech-digest` bot 发来的卡片 🎉

---

## 第 7 步（可选）：让「点按钮记反馈」生效

到这一步你已经能**收到推送**，但**点「感兴趣 / 不感兴趣」按钮时反馈不会被记录**（个性化训练用）。要让它生效，需要让回调接收器（Socket Mode）常驻运行。

### 简单方案：手动启动一个窗口

```powershell
powershell -ExecutionPolicy Bypass -File windows\run_socket.ps1
```

让这个窗口**保持开着**，你点的按钮就会被记录。窗口关掉就停。

### 进阶方案：开机自启 + 后台运行

把下面的命令复制到 **任务计划程序** 中（开始菜单搜 "任务计划程序" / Task Scheduler）：

1. 创建基本任务 → 名称 `TechDigest-Socket`
2. 触发器：**当我登录时**
3. 操作：**启动程序**
   - 程序：`powershell.exe`
   - 参数：`-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\<你>\tech-digest-skill-slack\windows\run_socket.ps1"`
4. 完成

这样每次登录 Windows，回调接收器就会在后台静默运行。

> 不做这一步也不影响每日推送，只是收不到点击反馈、个性化进化不了。第一次用建议先跳过，等有点击需求再回来配。

---

## 常见问题

### bot 推送出来一条「Claude 正在思考...」之类的不相关消息
那是另一个 skill（Slack ↔ Claude bot）的 bot，跟这个 tech-digest 的 bot 不是一个。这俩是独立的 Slack App。

### 收不到推送 / `{"sent": 0}`
1. 确认 `.env` 里 **`SLACK_DEFAULT_USERS`** 填了你自己的 Slack 用户 id（`U` 开头），首次必填
2. 看错误日志：`type "$env:USERPROFILE\.tech-digest\logs\daily.err"`

### `pip install` 报 `SELF_SIGNED_CERT_IN_CHAIN`
公司证书没导出 / 路径不对。重新做第 3 步，确保 `.\netskope.crt` 在项目根目录下。

### `requests` 调 Slack 报 SSL 错误
同上，证书问题。检查 `netskope.crt` 是否存在；脚本会自动设 `REQUESTS_CA_BUNDLE` 指向它。

### Slack 在国内访问慢 / 有时连不上
正常现象。脚本带了 3 次重试，多数能恢复。彻底解决需要给电脑配出海代理。

### 定时任务怎么改时间 / 关闭
开始菜单搜「任务计划程序」（Task Scheduler）→ 任务计划程序库 → 找 `TechDigest-Daily` → 右键「属性」改触发器，或「禁用 / 删除」。

### 想停掉所有自动行为
```powershell
Unregister-ScheduledTask -TaskName "TechDigest-Daily" -Confirm:$false
Unregister-ScheduledTask -TaskName "TechDigest-Socket" -Confirm:$false   # 如果配了
```

---

## 数据存哪？

默认在 `C:\Users\<你>\.tech-digest\`：

```
.tech-digest\
├── state.json                      # 上次抓取的内容，用于去重
└── logs\
    ├── recommendations.jsonl       # 每次生成的推荐快照
    ├── slack_card_actions.jsonl    # 用户点击记录（个性化数据）
    └── (slack_socket.out 如果你配了接收器)
```

---

## 卸载

```powershell
# 停掉定时任务
Unregister-ScheduledTask -TaskName "TechDigest-Daily" -Confirm:$false
Unregister-ScheduledTask -TaskName "TechDigest-Socket" -Confirm:$false -ErrorAction SilentlyContinue

# 删项目
Remove-Item -Recurse -Force C:\Users\<你>\tech-digest-skill-slack

# (可选) 删数据
Remove-Item -Recurse -Force "$env:USERPROFILE\.tech-digest"
```
