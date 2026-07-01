# tech-digest-skill

每天自动抓取 HuggingFace 论文、GitHub Trending、Anthropic 动态，用 LLM 根据你的历史点击行为做个性化重排，发送交互式卡片到 Slack。

```
┌─────────────────────────────────────────────┐
│  📚 HuggingFace 论文                         │
│  ────────────────────────────────────────── │
│  FlashAttention-3: Fast and Accurate ...    │
│  超长序列注意力机制的新一代实现，速度提升 2x  │
│  [查看]  [感兴趣]  [不感兴趣]               │
│                                             │
│  ⭐ GitHub Trending                         │
│  ────────────────────────────────────────── │
│  open-mmlab/mmdetection                    │
│  OpenMMLab 目标检测工具箱，支持 50+ 算法    │
│  [查看]  [感兴趣]  [不感兴趣]               │
└─────────────────────────────────────────────┘
```

点击「感兴趣」或「不感兴趣」，下一次推送会根据你的反馈调整内容。

> 💡 **Windows + 公司网络（Netskope 等代理）用户**：安装步骤见下方 [安装 → Windows](#windows)，包含证书处理、PowerShell 脚本和任务计划程序定时配置。

---

## 目录

- [前提条件](#前提条件)
- [安装](#安装)
- [接入你的 Agent](#接入你的-agent)
- [工作原理](#工作原理)
- [常见问题](#常见问题)

---

## 前提条件

### 1. Slack App

在 [Slack API 后台](https://api.slack.com/apps) 创建一个 App，完成以下配置。
推送和接收回调都走 **Socket Mode**（Slack 的长连接模式），不需要公网 IP / 域名 / HTTPS 证书，本地或内网机器都能跑。

**① 创建 App**

点 **Create New App** → **From scratch**，填名字、选工作区。

**② 开启 Socket Mode（拿到 App-Level Token）**

进入 **Socket Mode** → 打开 **Enable Socket Mode**。系统会让你生成一个 App-Level Token：
- Scope 选 `connections:write`
- 生成后复制这个 **`xapp-` 开头**的 token（只显示一次），填入 `.env` 的 `SLACK_APP_TOKEN`

**③ 配置权限（Bot Token Scopes）**

进入 **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**，添加：
- `chat:write`（发送消息，**必需**）
- `users:read`（可选，按 username 投递时需要）

**④ 安装到工作区（拿到 Bot Token）**

进入 **OAuth & Permissions** 顶部点 **Install to Workspace** → Allow。
安装后复制 **`xoxb-` 开头**的 **Bot User OAuth Token**，填入 `.env` 的 `SLACK_BOT_TOKEN`。

**⑤ 订阅交互事件（用于「感兴趣/不感兴趣」回调）**

Socket Mode 下，按钮点击通过 `block_actions` 交互事件回传，无需配置 Request URL。
确认 **Interactivity & Shortcuts** 已开启（开启 Socket Mode 后通常自动可用）即可。

> 想知道推送给「谁」？卡片是按 `recommendations.jsonl` 里每条记录的 `user_id` 投递的，
> 这里的 `user_id` 存的是 Slack 用户 id（形如 `U0XXXXXXX`）。向用户 id 发送时，
> Slack 会自动开一个 bot↔用户 的私聊（DM）。用户 id 来自他们点击按钮后记录的行为日志。

---

### 2. LLM API Key

需要一个兼容 OpenAI 接口的 LLM 服务，用于推荐重排和生成中文摘要。

| 服务 | 特点 | 地址 |
|---|---|---|
| DeepSeek | 便宜，推荐 | [platform.deepseek.com](https://platform.deepseek.com) |
| OpenAI | 效果好 | [platform.openai.com](https://platform.openai.com) |
| 本地 Ollama | 免费，需本机跑模型 | [ollama.com](https://ollama.com) |

---

### 3. Python 3.9+

```bash
python3 --version    # macOS / Linux
python --version     # Windows
```

Windows 没装的话：`winget install Python.Python.3.12`（装完重开终端）。

---

### 4. GitHub Personal Access Token（可选，强烈推荐）

GitHub 摘要会调用 `api.github.com/repos/...` 拿仓库描述与 README。**未认证时每小时只能调 60 次**，公司出口 IP 共享的话非常容易被打满，导致 GitHub 卡片显示"暂无简介"。

**带 token 调用每小时 5000 次**，绰绰有余。

生成方式（不需要任何权限）：

1. 浏览器打开 https://github.com/settings/tokens
2. 点 **Generate new token** → **Generate new token (classic)**
3. **Note** 随便填（如 `tech-digest-readme`）
4. **Expiration** 选你能接受的过期时间（90 天 / No expiration 都行）
5. **Scopes 全部不勾**（只读公开仓库元数据，不需要任何权限）
6. 点 **Generate token**，复制出现的 `ghp_...` token
7. 填到 `.env` 的 `GITHUB_TOKEN=`

不填这一项也能跑，只是 GitHub 类卡片在共享出口 IP 下大概率显示"暂无简介"。

---

## 安装

先完成上面「前提条件」拿到凭证，再按你的操作系统选一节。两节内容平行，看自己那节即可。

- [Windows](#windows)（含公司网络 Netskope 证书处理）
- [macOS / Linux](#macos--linux)

---

### Windows

> 适合公司 Windows 电脑。用「任务计划程序」定时推送，错过会在开机后自动补跑。

**第 1 步：拿到项目代码**

把整个项目文件夹放到本地，建议 `C:\Users\<你>\tech-digest-skill-slack\`，然后进目录：

```powershell
cd C:\Users\<你>\tech-digest-skill-slack
```

**第 2 步：处理公司证书（Netskope 等代理）**

公司网络若用 Netskope/Zscaler 等做 HTTPS 中间人解密，pip 和 Python 联网会因不认识证书而失败（报 `SELF_SIGNED_CERT_IN_CHAIN`）。先检查有没有这类证书：

```powershell
Get-ChildItem -Path Cert:\LocalMachine\Root |
  Where-Object { $_.Subject -match "Netskope|Zscaler|Bluecoat|Forcepoint" } |
  Select-Object Subject, Thumbprint
```

**没有任何输出** = 公司没用这类代理，跳到第 3 步。
**有输出**：记下 Thumbprint，替换进下面导出证书到项目根目录：

```powershell
$thumbprint = "你记下的Thumbprint"
$cert = Get-ChildItem -Path Cert:\LocalMachine\Root | Where-Object { $_.Thumbprint -eq $thumbprint }
$base64 = [System.Convert]::ToBase64String($cert.RawData, 'InsertLineBreaks')
"-----BEGIN CERTIFICATE-----`r`n$base64`r`n-----END CERTIFICATE-----" |
  Set-Content -Path ".\netskope.crt" -Encoding ASCII
Write-Output "导出完成: $(Resolve-Path .\netskope.crt)"
```

> 之后所有 PowerShell 脚本会自动读这个 `netskope.crt`，让 pip 和 `requests` 信任公司证书，无需每次手动设环境变量。该文件已被 `.gitignore` 排除，不会上传。

**第 3 步：填配置 `.env`**

```powershell
copy .env.example .env
notepad .env
```

填入必填项（细节见上方「前提条件」和「怎么查自己的 Slack 用户 ID」）：

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-1-...
SLACK_DEFAULT_USERS=U07XXXXXXX     # 你自己的 Slack 用户 ID
GENREC_API_KEY=sk-...              # DeepSeek 等 LLM Key
PUSH_TIME=10:00                    # 每日推送时间，本机本地时间
# 公司 DNS 访问 huggingface.co 不稳时建议开镜像：
# HF_ENDPOINT=https://hf-mirror.com
```

**第 4 步：一键安装 + 注册定时任务**

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1
```

它会：检测 Python → 用公司证书装依赖 → 在任务计划程序注册 `TechDigest-Daily`（每天按 `PUSH_TIME` 跑，错过开机补跑）。

**第 5 步：手动跑一次验证**

```powershell
powershell -ExecutionPolicy Bypass -File windows\run_daily.ps1
```

看到 `{"sent":1,"failed":0,...}` 且 Slack 收到卡片即成功。之后每天定时自动推送；每次推送会自动拉起「回调接收器」记录你的点击反馈，无需单独维护。

**随时自查状态**：

```powershell
powershell -ExecutionPolicy Bypass -File windows\check.ps1
```

---

### macOS / Linux

**第一步：克隆并初始化**

```bash
git clone https://github.com/YOUR_USERNAME/tech-digest-skill
cd tech-digest-skill
bash install.sh
```

首次运行生成 `.env` 并退出，提示你填写凭证。

**第二步：填写凭证**

打开 `.env`，填入 Slack 和 LLM 配置：

```bash
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_APP_TOKEN=xapp-1-xxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx

# 种子用户（必填——否则第一条推送发不出去，见下方「首次使用」）
SLACK_DEFAULT_USERS=U07XXXXXXXX

# LLM 配置（必填）
GENREC_ENDPOINT=https://api.deepseek.com/v1/chat/completions
GENREC_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GENREC_MODEL=deepseek-chat

# 每日推送时间（本机本地时间，可选，默认 10:00）
PUSH_TIME=10:00
```

> `.env` 已被 `.gitignore` 排除，不会上传到 GitHub。

**第三步：完成安装**

```bash
bash install.sh
```

脚本会自动完成：装 Python 依赖、启动 Slack 回调接收器（后台常驻、崩溃自动重启）、按 `PUSH_TIME` 设置每日定时推送（launchd / crontab）。

完成后手动触发一次确认收到卡片：

```bash
bash scripts/run_daily_recommendations.sh
```

---

#### 怎么查自己的 Slack 用户 ID（填 `SLACK_DEFAULT_USERS` 用）

用户 ID 不是邮箱也不是昵称，而是一串 `U` 开头的内部 ID（如 `U07ABC123`）。查法：

1. 在 Slack 里点左侧你自己的头像 → **Profile（个人资料）**
2. 资料面板右上角点 **⋮（More / 更多）**
3. 选 **Copy member ID（复制成员 ID）**
4. 粘到 `.env` 的 `SLACK_DEFAULT_USERS=` 后面

> 给同事用时，让他们各自这样查到自己的 ID 发给你，逗号拼接：`SLACK_DEFAULT_USERS=U07ABC,U07DEF`。

#### 为什么种子用户这一项必填？（冷启动）

推送是"千人千面"的，系统靠**用户点击历史**决定推给谁、推什么。但**第一次运行时没有任何点击历史**——于是不知道发给谁，一条都发不出去（先有鸡还是先有蛋）。

`SLACK_DEFAULT_USERS` 就是打破这个死锁的"第一批种子用户"：他们会先收到一份**非个性化**（接近随机）的推荐；一旦点了「感兴趣/不感兴趣」，系统就有了历史，之后转为个性化。点击约 10 次后效果明显提升。

---

## 接入你的 Agent

skill 的核心是 shell 脚本，任何能执行 shell 命令的 Agent 都可以驱动它。

---

### Claude Code（用户级 Skill）

本项目是一个标准的 [Claude Code Skill](https://docs.claude.com/en/docs/claude-code/skills)。安装到用户级 skills 目录后，**所有** Claude Code 会话都能自动发现它——你在任意目录打开 Claude Code，说一句"发技术推荐"就会触发，无需进入项目目录、无需记命令。

**原理**：`SKILL.md` 顶部的 frontmatter（`name` + `description`）告诉 Claude Code 这个 skill 是做什么的、什么时候该用。Claude Code 启动时扫描 `~/.claude/skills/` 下所有 skill 的 description，匹配到你的请求时自动调用。

**步骤一：把整个项目放到用户级 skills 目录**

```bash
mkdir -p ~/.claude/skills
cp -r tech-digest-skill ~/.claude/skills/tech-digest
```

> 目录名必须和 `SKILL.md` 里的 `name: tech-digest` 一致。

**步骤二：在新目录里完成配置**

```bash
cd ~/.claude/skills/tech-digest
bash install.sh          # 第一次：生成 .env
vim .env                 # 填入 Slack 和 LLM 凭证
bash install.sh          # 第二次：装依赖、起服务、设定时任务
```

**步骤三：用自然语言触发**

在**任意目录**打开 Claude Code：

```bash
claude
```

然后直接说：

> 发技术推荐

Claude Code 会自动匹配到 tech-digest skill，执行完整流程：抓取数据 → LLM 重排 → 发送 Slack 卡片。

**验证 skill 已被识别**：在 Claude Code 里输入 `/` 或询问"有哪些可用 skill"，应能看到 `tech-digest`。

---

### nanobot

nanobot 通过读取 skill 目录中的 `SKILL.md` 来了解如何调用这个 skill。

**步骤一：将 skill 复制到 nanobot workspace**

```bash
cp -r tech-digest-skill ~/.nanobot/workspace/skills/tech_digest
```

**步骤二：在 nanobot 中手动触发（测试用）**

向 nanobot 发送消息：

> 使用 tech_digest skill，跑一次推荐

nanobot 会读取 `SKILL.md` 中的指令，执行推送流程。

**步骤三：设置每日自动推送**

在 nanobot 的 cron 配置中添加任务（每天 09:00）：

```json
{
  "message": "使用 tech_digest skill。Run: bash skills/tech_digest/scripts/run_daily_recommendations.sh",
  "schedule": "0 9 * * *"
}
```

> 注意：如果你用 `install.sh` 安装，launchd/crontab 已经自动配置好了定时任务，这一步可以跳过。

---

### 其他 Agent

任何能执行 shell 命令的 Agent，只需知道两条命令：

| 操作 | 命令 |
|---|---|
| 触发一次推送 | `cd /path/to/tech-digest-skill && bash scripts/run_daily_recommendations.sh` |
| 检查回调接收器状态 | `tail ~/.tech-digest/logs/slack_socket.out` |

在你的 Agent 指令文件中加入这两条命令和触发时机说明即可。

---

## 工作原理

```
每日定时（PUSH_TIME）自动触发
        │
        ▼
genrec_pipeline.py
        ├── 抓取：HuggingFace API / GitHub Trending / Anthropic 博客
        ├── 读取历史点击记录（slack_card_actions.jsonl）
        ├── 规则召回：按话题关键词 × 点击权重打分
        └── LLM 重排：为每个用户选出最相关的 top-K 内容
        │
        ▼
send_recommendations.py
        ├── LLM 生成摘要：把英文描述压缩成 1-2 句中文
        ├── 构建 Slack Block Kit 卡片（HF 论文 / GitHub / Anthropic 分组）
        └── 通过 Slack chat.postMessage 发送
        │
        ▼
用户在 Slack 收到卡片，点击「感兴趣」或「不感兴趣」
        │
        ▼
slack_socket_mode.py（后台常驻，Socket Mode 长连接）
        └── 将点击记录追加到 slack_card_actions.jsonl
                  │
                  └── 下次推送时作为个性化依据
```

**冷启动说明**：首次使用没有历史记录，推荐内容接近随机。点击约 10 次「感兴趣/不感兴趣」后，个性化效果会明显提升。

---

## 常见问题

**Q：收不到推荐卡片**

1. 确认 `SLACK_DEFAULT_USERS` 填了有效的 Slack 用户 id（`U` 开头）——**首次使用必填**，否则没有任何收件人
2. 确认 `SLACK_BOT_TOKEN`（`xoxb-`）填写正确，且 App 已安装到工作区
3. 确认 Bot 有 `chat:write` 权限
4. 查看错误日志：
   - Windows：`type "$env:USERPROFILE\.tech-digest\logs\daily.err"`
   - macOS / Linux：`cat ~/.tech-digest/logs/daily.err`

**Q：第一次跑，日志显示 `"users": 0` / `"sent": 0`**

冷启动死锁：还没有人点过按钮，又没填种子用户。在 `.env` 里把你自己的 Slack 用户 id 填进 `SLACK_DEFAULT_USERS`，重新运行即可。查 id 的方法见上方「怎么查自己的 Slack 用户 ID」。

**Q：点击「感兴趣」按钮没有反馈**

回调接收器可能没有运行：

- **Windows**：`powershell -ExecutionPolicy Bypass -File windows\check.ps1`（看接收器在不在、上次跑没跑、反馈记了几条）
- **macOS**：`launchctl list | grep techdigest.socket`
- 查看连接日志（应能看到 Bolt 启动 / 已连接 Slack 的日志）：
  Windows `type "$env:USERPROFILE\.tech-digest\logs\slack_socket.out"` / mac·Linux `tail ~/.tech-digest/logs/slack_socket.out`

另外确认 App 的 **Socket Mode** 已开启、`SLACK_APP_TOKEN`（`xapp-`）填写正确。

**Q：想换 LLM**

修改 `.env` 中的三个参数，重新运行安装脚本即可（Windows `windows\install.ps1` / mac·Linux `bash install.sh`）：
```bash
GENREC_ENDPOINT=https://api.openai.com/v1/chat/completions
GENREC_API_KEY=sk-xxxx
GENREC_MODEL=gpt-4o-mini
```

**Q：想修改推送时间**

编辑 `.env` 里的 `PUSH_TIME=HH:MM`（24 小时制），然后重新运行安装脚本即可：

- Windows：`powershell -ExecutionPolicy Bypass -File windows\install.ps1`
- macOS / Linux：`bash install.sh`

> ⏰ 用的是**你这台电脑显示的本地时间**——电脑几点就几点推，不用换算时区。如果电脑时区和你所在地不一致，要么按电脑时区填，要么先把系统时区调对。

**Q：数据存在哪里**

默认在 `~/.tech-digest/`（Windows 是 `C:\Users\<你>\.tech-digest\`）：
```
~/.tech-digest/
├── state.json                   # 上次抓取的内容，用于去重
└── logs/
    ├── slack_card_actions.jsonl # 用户点击记录（个性化数据）
    ├── slack_socket.out         # 回调接收器日志
    └── daily.out                # 每日推送日志
```

**Q：pip 报 `SELF_SIGNED_CERT_IN_CHAIN` / requests 调 Slack 报 SSL 错误**

公司代理（Netskope/Zscaler 等）证书问题。见上方 [安装 → Windows](#windows) 第 2 步导出证书；确认 `netskope.crt` 在项目根目录。

**Q：GitHub 卡片显示"暂无简介"**

未认证 GitHub API 限速（60/小时，公司出口 IP 易被打满）。生成一个无权限 PAT 填到 `.env` 的 `GITHUB_TOKEN`，见「前提条件 4」。

**Q：HuggingFace 那栏经常空 / 抓取失败**

公司 DNS 解析 `huggingface.co` 不稳定。在 `.env` 加 `HF_ENDPOINT=https://hf-mirror.com` 走国内镜像。

**Q：Slack 在国内访问慢 / 有时连不上**

正常现象，脚本带 3 次重试，多数能恢复。彻底解决需给机器配出海代理。

**Q：Windows 怎么改/关定时任务**

开始菜单搜「任务计划程序」→ 找 `TechDigest-Daily` → 右键改触发器或禁用/删除。命令行停用：
```powershell
Unregister-ScheduledTask -TaskName "TechDigest-Daily" -Confirm:$false
```

---

## License

MIT
