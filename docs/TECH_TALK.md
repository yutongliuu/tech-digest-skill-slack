# 一个 Claude Code Skill 的诞生

> 从飞书 tech-digest 到跨平台 Slack agent skill —— 个性化技术资讯推送 · 跨平台落地 · 企业内网踩坑实录

---

## 第一部分：这是什么 & 为什么做

### 1. 一句话 demo

每天 10:00，Slack 自动收到这样一张卡片：

```
┌────────────────────────────────────────────────┐
│  📚 HuggingFace 论文                            │
│  ─────────────────────────────────────────── │
│  SEAOTTER: Sensor Embedded Autoencoding...    │
│  传感器嵌入式自动编码与一次性转码方法，旨在高效   │
│  重建机器人系统中的高分辨率视觉数据。            │
│  [查看]  [感兴趣]  [不感兴趣]                  │
│                                                │
│  ⭐ GitHub Trending                            │
│  ─────────────────────────────────────────── │
│  PaddlePaddle/PaddleOCR                        │
│  轻量级 OCR 工具包，支持 100+ 语言，可将 PDF   │
│  或图像转换为结构化数据。                       │
│  [查看]  [感兴趣]  [不感兴趣]                  │
└────────────────────────────────────────────────┘
```

点「感兴趣 / 不感兴趣」会被记录，下次推送据此调整。也可以在 Claude Code 里说"发技术推荐"立即手动触发一次。

### 2. 解决的真实问题

| 痛点 | 现状 |
|------|------|
| 信息过载 | HF 每天 30+ 论文、GitHub Trending 25+ 项目、Anthropic 不定期更新 |
| 主动浏览成本高 | 每天打开三个网站、各看十几条，没多少人能坚持 |
| 现有方案不理想 | RSS 推太多没筛选；Twitter 算法不靠谱；订阅号信息割裂 |

核心价值是三个：

- **千人千面**：每人的「感兴趣/不感兴趣」点击塑造各自的推荐
- **零成本日常获取**：定时自动推送，不需要主动打开任何网站
- **越用越准**：行为记录持续喂回，推荐质量随时间提升

### 3. 用户用起来什么样

```
配置 1 次（约 30 分钟）
       ↓
每天 10:00 Slack 自动收到推送卡片
       ↓
点几下「感兴趣 / 不感兴趣」
       ↓
明天的推送更贴合你的口味
       ↓
想随时手动触发？Claude Code 里说"发技术推荐"
```

5 步完成配置：克隆代码 → 装 Python → 申请 Slack App → 填 `.env` → 跑 install 脚本。

---

## 第二部分：整体架构

### 4. 一张图看懂

```
                ┌──────────────── 推送链路（每天定时跑一次）────────────────┐
                │                                                            │
   数据源        │   tech_digest.py        genrec_pipeline.py    send_recommendations.py
 ┌─────────┐    │  ┌──────────────┐       ┌────────────────┐    ┌────────────────────┐
 │ HF 论文 │───┼─▶│ 抓取 + 去重    │──────▶│ 召回 + LLM 重排│───▶│ LLM 摘要 + 发卡片  │──┐
 │ GitHub  │    │  │ build_report │report │ 个性化排序     │recs│ 构建 Slack 卡片    │  │
 │Anthropic│    │  └──────┬───────┘       └───────▲────────┘    └────────────────────┘  │
 └─────────┘    │         │ 读/写                  │ 读历史                              │
                │     state.json        slack_card_actions.jsonl                         │
                └───────────────────────────▲──────────────────────────────────────────┬┘
                                            │ 追加                                      │ Slack API
                                            │                                          ▼
                ┌────── 回调链路（按需启动）──────┐                       用户 Slack 收到卡片
                │   slack_socket_mode.py         │                              │
                │  ┌────────────────────────┐    │  点击 感兴趣/不感兴趣          │
                │  │ Socket Mode 长连接      │◀───┼──────────────────────────────┘
                │  │ 接收按钮点击 → 写 jsonl │    │
                │  └────────────────────────┘    │
                └────────────────────────────────┘
```

**关键点**：用户的点击行为（回调链路记录）会成为下一次推送链路的个性化输入，形成"推送 → 反馈 → 更好的推送"闭环。

### 5. 三层分离的设计原则

```
┌─────────────────────┐
│ 展示层（渠道相关）  │  send_recommendations.py / slack_socket_mode.py
├─────────────────────┤
│ 算法层（业务核心）  │  genrec_pipeline.py
├─────────────────────┤
│ 数据层（抓取去重）  │  tech_digest.py
└─────────────────────┘
```

三层职责清晰：

- **数据层**：只负责抓数据 + 与上次对比标记 new/same
- **算法层**：只接受 report，输出每个用户的推荐 JSONL，不关心展示
- **展示层**：只接受 JSONL，渲染并通过具体渠道送达

改任一层不影响另两层。这也是后面"飞书 → Slack 移植"时**核心代码一行不用动**的根本原因——只换了展示层和回调层两个文件。

---

## 第三部分：核心技术实现

### 6. 个性化推荐：规则召回 + LLM 重排

为什么不直接把所有候选丢给 LLM 排：成本高、上下文有限、延迟大。两阶段方案是经典做法：

```
候选池 (30+)
     │
     ├─ 第 1 阶段：规则召回（毫秒级）
     │   基于"用户话题画像"快速打分，取 top 6
     │
     ├─ 第 2 阶段：LLM 重排（秒级）
     │   把 6 个候选 + 用户历史给 LLM，让它精排出 3 个
     │
     ▼
  最终推荐 (3 个)
```

**用户话题画像怎么算**：

```
单条得分 = 行为权重 × 时间衰减

行为权重         时间衰减
─────────       ─────────
like:    +3.0    7  天内: 1.0
read:    +1.0    30 天内: 0.5
dislike: -3.0    更早:    0.2
```

每条点击映射到话题（LLM / CV / Robotics / Security / Data / Infra），累加得到 `{"topic:LLM": 9.0, "topic:CV": -3.0}` 这样的画像。然后用画像给每个候选打分，分高的进 top 6。

**LLM 重排**：把 6 个候选 + 用户最近点击 + 长期偏好总结一起喂给 LLM，让它返回精排后的 3 个 id。失败兜底：直接取规则召回的前 3 个。

**冷启动死锁的解决**：用户列表 100% 来自行为日志，但第一次运行没人点过按钮 → 用户列表为空 → 一条都发不出去 → 永远没有点击。打破死锁的设计是 `.env` 里的 `SLACK_DEFAULT_USERS=U07xxx,U07yyy` —— 种子用户名单。他们以"空历史"被并入 `history_by_user`，先收到非个性化推荐；一旦点了按钮，下一轮就从日志自然进入个性化流程。

### 7. 自动推送：定时器 + 接收器的协同

这是这个项目最有意思的设计之一。先看场景：

```
用户期望：每天 10:00 收推送 + 点按钮反馈被记录
现实约束：本地 Windows，定时器跑过就退出；接收器需要常驻
```

如果按朴素的方式做，要分别配两个常驻进程（定时器 + 接收器），用户得管两套服务。这套方案换了个思路 —— **让"推送"顺手把"接收器"带起来**：

```
触发方式                            内部流程
─────────────────                  ────────────────────────────
方式 A：每天 10:00 定时任务         │
方式 B：手动 run_daily.ps1          │ ──┐
方式 C：Claude Code 说"发技术推荐"  │   │
                                       ▼
                            ┌─────────────────────────────┐
                            │ run_daily.ps1               │
                            │ ① 检查接收器进程在不在？     │
                            │   不在 → 后台启动一个        │
                            │ ② 跑抓取 + 重排 + 发卡片     │
                            └─────────────────────────────┘
                                       │
                            接收器保持运行（直到关机）
```

**协同效果**：

| 时机 | 谁主动 | 接收器状态 |
|------|--------|----------|
| 开机后第一次推送 | Windows 任务计划器 | 不在 → 自动拉起 |
| 之后所有推送 | 任务计划器 / Claude / 手动 | 已经在跑 → 跳过启动 |
| 关机 | — | 接收器随之退出 |
| 次日开机后第一次推送 | 任务计划器 | 又自动拉起 |

**用户感知**：永远不用关心接收器。无论从哪种方式触发推送，接收器都已经在跑或被顺手拉起。

**任务计划程序的两个关键开关**（错过 10:00 也能补救）：

```powershell
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `            # 错过的任务在下次可用时补跑
    -WakeToRun `                      # 允许唤醒电脑跑任务（插电时）
    -RunOnlyIfNetworkAvailable        # 没网就跳过
```

实际表现：

| 场景 | 行为 |
|------|------|
| 10:00 电脑开着 | 准点跑 ✅ |
| 10:00 休眠（插电） | 唤醒后跑 ✅ |
| 10:00 关机，11:00 开机 | 开机后立即补跑 ✅ |
| 一整天没开机 | 这天跳过（不会无限补） |

### 8. Claude Code Skill 是怎么工作的

```
~/.claude/skills/tech-digest/SKILL.md
    ↓ Claude Code 启动时扫描
   读取 frontmatter:
   ────────────
   name: tech-digest
   description: 发送每日 AI/技术个性化推荐到 Slack。
                当用户要求"发技术推荐""跑一次推荐"...时使用。
   ────────────
    ↓
用户在任意 Claude Code 会话说："发技术推荐"
    ↓
Claude 把用户意图和所有 skill 的 description 做语义匹配
    ↓
匹配到 tech-digest → 读 SKILL.md 正文 → 跑里面写的命令
    ↓
执行 windows\run_daily.ps1 → 推送卡片到 Slack
```

**为什么这套设计很好用**：

- 用户不用记命令，自然语言触发
- 跨任意工作目录可用（user-level skill）
- 描述写得清楚，Claude 就能在多个 skill 之间正确路由

**开发过程的一个反直觉教训**：起初我以为给 SKILL.md 加更多禁止条款（"不要 X、不要 Y"）能让 Claude 更直接地跑命令、少绕弯路。结果完全相反——禁令清单越长，Claude 反而越纠结那些被禁的事情。最后回退到极简版（只说"做什么"不说"别做什么"）反而最稳。

> **对 LLM 指令，简单 > 详细禁止。**

### 9. Slack 集成

**发送：chat.postMessage + Block Kit**

```python
blocks = [
  {"type": "header", "text": {"type": "plain_text", "text": title}},
  {"type": "section", "text": {"type": "mrkdwn", "text": "*📚 HF 论文*"}},
  {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{summary}"}},
  {"type": "actions", "elements": [
    {"type": "button", "text": "查看", "url": url, "action_id": "card_action_read"},
    {"type": "button", "text": "感兴趣", "value": json.dumps({...}), "action_id": "card_action_like"},
    {"type": "button", "text": "不感兴趣", "value": json.dumps({...}), "action_id": "card_action_dislike"},
  ]},
]
requests.post("https://slack.com/api/chat.postMessage",
              headers={"Authorization": f"Bearer {bot_token}"},
              json={"channel": user_id, "blocks": blocks, "unfurl_links": False})
```

几个细节：

- 卡片标题用纯 mrkdwn 而非超链接（点了按钮还跳，避免两个入口跳同一个 URL）
- `unfurl_links=False`：防止 Slack 自动给 url 加 preview 卡，叠在按钮下面看着乱
- 按钮 `value` 是字符串，所以把 dict 用 `json.dumps` 塞进去，回调侧再 `json.loads` 还原

**接收：Socket Mode**

Slack 提供的"反向连接"模式。对比 webhook：

| 模式 | 谁主动 | 公网要求 |
|------|--------|---------|
| HTTP webhook | Slack 服务器主动来找你 | 必须有公网 HTTPS URL |
| **Socket Mode** | **你的进程主动连出去** | **不需要公网** |

本地电脑藏在公司路由器后面，没有公网门牌号，所以 webhook 模式根本走不通。Socket Mode 像"主动打电话给客服"——线路是你拨出去建好的，Slack 把事件顺着这条线推回来。本地、内网机器都能跑。

代码极简（用 `slack_bolt`）：

```python
app = App(token=os.environ["SLACK_BOT_TOKEN"])

@app.action(re.compile(r"^card_action_"))
def handle(ack, body, action):
    ack()
    value = json.loads(action["value"])
    record = {
        "received_at": _now_iso(),
        "open_id": body["user"]["id"],
        "action_value": value,
        # ...
    }
    _append_log(LOG_PATH, record)

SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
```

`ack()` 必须 3 秒内调（否则 Slack 给用户显示"加载中"转圈），所以处理逻辑放在 ack 之后。

### 10. 推荐质量：摘要 + 去重

**LLM 摘要**：英文论文摘要直接给用户太长，让 DeepSeek 压成 1-2 句中文。Prompt 简单：

```
task: Compress to 1-2 short Chinese sentences. Do not invent facts.
      Return JSON: {"summary":"..."}. Summary must be non-empty.
item: {title, source, summary}
```

GitHub repo 没有内置 summary 字段，要先调 `api.github.com/repos/{owner}/{repo}` 拿 description，再拉 README 前几行，拼起来给 LLM 压缩。**未认证 GitHub API 每小时只能调 60 次**，公司出口 IP 共享时经常被打满，所以加 `GITHUB_TOKEN`（无任何权限的 PAT）提到 5000/h。

**去重**：state.json 记录上次推过的 ID。本次抓取后给每个 item 打 `status: new|same`，候选池构建时跳过 `same`：

```python
def add_items(...):
    for it in items:
        if it.get("status") == "same":
            continue   # 上次推过了，跳过
        candidates.append(...)
```

策略上**只推新内容，没新内容就跳过这个数据源**，不从旧的里补。HF 论文每天几乎全新，不缺；GitHub Trending 变化慢，偶尔某个数据源当天为空也是正常的——比强行推重复内容好。

---

## 第四部分：跨平台落地

### 11. 飞书 → Slack 移植

```
飞书原版                           Slack 版
─────────────                     ──────────────
tech_digest.py        ────────▶   tech_digest.py        (零改动)
genrec_pipeline.py    ────────▶   genrec_pipeline.py    (只加 --seed-users)
send_recommendations  ────────▶   send_recommendations  (重写：卡片 → Block Kit)
feishu_card_longconn  ────────▶   slack_socket_mode     (重写：WS → Socket Mode)
```

**飞书 → Slack 概念映射**：

| 飞书 | Slack |
|------|-------|
| WebSocket 长连接 (`lark-oapi`) | Socket Mode (`slack_bolt`) |
| `tenant_access_token` 换取 | Bot Token 直接用 |
| interactive 卡片 (lark_md) | Block Kit (blocks) |
| `card.action.trigger` 回调 | `block_actions` 事件 |
| 按钮 `value` 用 dict | `value` 用 JSON 字符串 |
| `open_id` (ou_xxx) | Slack user_id (U_xxx) |

最幸运的发现：**飞书 WS 长连接和 Slack Socket Mode 在架构层面几乎一一对应** —— 都是"客户端反向连出去，不需要公网"。所以回调机制的代码逻辑可以照搬，只换 SDK。

### 12. 企业内网踩坑实录

公司电脑 + 内网代理 + Windows 三重 buff 叠加，开发期间踩了 7 个真实坑：

**坑 1：Netskope 拦截所有 HTTPS**

公司用 Netskope 给所有 HTTPS 流量做中间人解密，导致 `pip install` 和 `requests.get` 都报 `SELF_SIGNED_CERT_IN_CHAIN`。

```powershell
# 从系统证书库导出公司 CA
Get-ChildItem -Path Cert:\LocalMachine\Root |
  Where-Object { $_.Subject -match "Netskope" } |
  # ... 导出为 PEM 格式存到项目里
```

然后让 pip 和 requests 都信任：

```powershell
$env:REQUESTS_CA_BUNDLE = "C:\path\to\netskope.crt"  # requests 用
pip install --cert "C:\path\to\netskope.crt" ...    # pip 用
```

**坑 2：Windows Python 打印中文崩溃**

```
UnicodeEncodeError: 'charmap' codec can't encode characters...
```

Windows 控制台默认 cp1252（PowerShell）/ cp936（CMD），Python stdout 默认跟随。Fix：

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
```

**坑 3：PowerShell 脚本里的中文也会触发解析错误**

`.ps1` 文件保存时如果不带 BOM，PowerShell 按 ANSI 读取，中文变乱码 → 解析失败。最干净的做法：**脚本里全用英文输出**，中文留给 .md 文档。

**坑 4：GitHub API 限速 60/h**

公司出口 IP 几百人共用，未认证 GitHub API 调用很快就用完每小时 60 次配额，结果 GitHub repo 摘要全部显示"暂无简介"。Fix：

```bash
# .env
GITHUB_TOKEN=ghp_xxx  # 任何权限都不勾的 PAT，限额从 60/h → 5000/h
```

**坑 5：Slack Socket Mode 接收不到事件**

调试 1 小时才发现根因：改过 OAuth scope 后没 reinstall app，**Slack 服务端的事件订阅快照是旧版**，事件不投递到 Socket Mode。Fix：

```
api.slack.com/apps → 你的 App → Install App → Reinstall to Workspace
```

每次改权限/事件订阅后都要 reinstall，否则 token 不变但事件路由没更新。

**坑 6：定时任务"静默失败"**

`Register-ScheduledTask` 在公司组策略限制下可能**注册无报错但任务不存在**。Fix 是 install.ps1 注册后立即验证：

```powershell
Register-ScheduledTask ... -ErrorAction Stop  # 用 Stop 模式
$check = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $check) {
    Write-Output "[ERROR] Task NOT found after registration."
    exit 1
}
```

并提供 check.ps1 让用户随时一键自查任务在不在、上次跑没跑、反馈记了几条。

**坑 7：时区漂移**

任务计划器用本机时区。开发者在国内但电脑设的美西时区 → "10:00" 跑成中国时间凌晨 1 点。Fix 是把时间做成 `.env` 里的 `PUSH_TIME=HH:MM`，**明确说明用本地时区**，让用户按自己电脑显示的时间填，避免脑内换算。

---

## 第五部分：接入指南

### 13. 同事如何开始用

仓库地址：**https://github.com/yutongliuu/tech-digest-skill-slack**

5 步快速启动：

```bash
# 1. 拉代码（不用装 git 也行：网页 Download ZIP）
git clone https://github.com/yutongliuu/tech-digest-skill-slack
cd tech-digest-skill-slack

# 2. 看对应平台手册
# Windows 用户  → windows/WINDOWS.md
# macOS / Linux → README.md

# 3. 准备 4 项凭证（每人自己申请）
#    - Slack Bot Token (xoxb-)
#    - Slack App Token (xapp-)
#    - DeepSeek API Key (sk-)        ← 自己的，别用别人的
#    - GitHub Personal Access Token  ← 可选，强烈建议

# 4. 填 .env
cp .env.example .env
# 编辑 .env，填上面 4 项 + 自己的 Slack User ID

# 5. 跑安装脚本
# Windows
powershell -ExecutionPolicy Bypass -File windows\install.ps1
# macOS / Linux
bash install.sh
```

完事，每天到点收推送。想改时间？改 `.env` 里 `PUSH_TIME=HH:MM` 重跑 install 脚本即可。

---

## 附录 A：核心数据格式

```
~/.tech-digest/
├── state.json                       # 抓取去重档案
└── logs/
    ├── recommendations.jsonl        # 每次推送的快照
    └── slack_card_actions.jsonl     # 用户点击日志（个性化数据源）
```

**`recommendations.jsonl`**（每行一个用户的一份推荐）：

```json
{
  "user_id": "U0XXXXXXX",
  "generated_at": "2026-06-08T01:00:00+00:00",
  "items": [
    {"id": "2606.00386", "title": "...", "summary": "...", "url": "...", "source": "hf", "score": 9.0},
    ...
  ]
}
```

**`slack_card_actions.jsonl`**（每行一条点击）：

```json
{
  "received_at": "2026-06-08T11:15:02+00:00",
  "open_id": "U0XXXXXXX",
  "action_tag": "card_action_like",
  "action_value": {"item_id": "...", "source": "gh", "action": "like", ...}
}
```

## 附录 B：环境变量速查

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | ✅ | — | `xoxb-` 开头 |
| `SLACK_APP_TOKEN` | ✅ | — | `xapp-` 开头，Socket Mode 用 |
| `SLACK_DEFAULT_USERS` | ✅ | — | 种子用户 ID，逗号分隔（破冷启动） |
| `GENREC_ENDPOINT` | ✅ | — | LLM API 地址（OpenAI 兼容） |
| `GENREC_API_KEY` | ✅ | — | LLM API Key |
| `GENREC_MODEL` | — | `deepseek-chat` | 模型名 |
| `PUSH_TIME` | — | `10:00` | 每日推送时间（本机时区） |
| `GITHUB_TOKEN` | — | 空 | 可选，提速率限制 |
| `TECH_DIGEST_DATA_DIR` | — | `~/.tech-digest` | 数据目录 |
| `HF_ENDPOINT` | — | `https://huggingface.co` | 国内可设 `hf-mirror.com` |
| `TECH_DIGEST_MODE` | — | `all` | `daily` 跳过 Anthropic |

## 附录 C：常见问题

**Q：收不到推送，`{"sent": 0}`**
A：检查 `.env` 里 `SLACK_DEFAULT_USERS` 是否填了 `U` 开头的真实 Slack User ID；看错误日志 `~/.tech-digest/logs/daily.err`。

**Q：按钮点了"转圈"不响应**
A：八成是接收器没跑或 Slack App 没 reinstall。先 `windows\check.ps1` 看接收器在不在；再去 api.slack.com Reinstall App。

**Q：pip 报 `SELF_SIGNED_CERT_IN_CHAIN`**
A：公司代理（Netskope/Zscaler 等）证书未导出。看 README/WINDOWS.md "处理公司证书" 章节。

**Q：想改推送时间**
A：改 `.env` 里 `PUSH_TIME=HH:MM`（24 小时制，本机时区），重跑 install 脚本。

**Q：GitHub 卡片显示"暂无简介"**
A：未认证 GitHub API 限速。生成一个无权限 PAT 填到 `GITHUB_TOKEN`。

---

## 附录 D：基础概念补充

面向不熟悉这些名词的读者。已经熟的可以跳过。

### D.1 Windows 内置的"闹钟"服务是什么？

正式名字叫 **任务计划程序（Task Scheduler）**，是 Windows 系统的一个**永远在后台运行的组件**——从你电脑开机的最早期就启动了，比桌面还早。

**它做什么**：维护一份"日程表"，时刻监控每个任务的触发条件（时间、事件），条件满足就自动拉起对应的程序。

**任务分两大类**：

| 触发方式 | 举例 |
|---------|------|
| 时间触发 | 每天 19:00 / 每小时 / 每周一 |
| 事件触发 | 开机时 / 用户登录时 / 系统空闲时 |

**它不是软件，是系统组件**：从 Windows XP 就自带，不用装、不能卸载、重装系统它还在。

**你的电脑里已经有几百个这种任务了**，全是 Windows 自己在用（磁盘碎片整理、Windows Update、Chrome/Edge 自动更新……）。想看的话开始菜单搜"任务计划程序"，打开左边树能看到全部列表。

**跨平台对照**：

| 系统 | 等价机制 |
|------|---------|
| Windows | 任务计划程序（Task Scheduler） |
| macOS | `launchd` |
| Linux | `cron` / `systemd timer` |
| Android | `AlarmManager` |

所有操作系统都自带一套。"到时间自动做事"是太基础的需求，操作系统必须原生支持。

**在本项目里**：`install.ps1` 里的 `Register-ScheduledTask` 就是把 `TechDigest-Daily` 这一条注册进任务计划程序的日程表。翻译成人话：**"Windows 你听着，每天 19:00 帮我跑一下 `run_daily.ps1`，跑完可以退了。"**

---

### D.2 Slack 里 App 和 Bot 是什么？有什么区别？

这俩概念**紧密耦合但不一样**，很容易混。用一个类比：

```
Slack App  = 公司雇的一整套产品/系统
Slack Bot  = 这个系统对应的一个"员工工号"，用来和大家聊天
```

具体对应关系：

```
你的 Slack App: "ClaudeBot"
├── 元数据：name / description / icon
├── 权限清单：chat:write, im:read, ...
├── 事件订阅：想接收哪些事件
├── Socket Mode 开关
│
└── 内置一个 Bot User（虚拟员工账号）:
    ├── 头像（用 App 的 icon）
    ├── 用户 ID：U0B88QZDR0A     ← 这才是 Bot
    ├── 能被 @, 被邀请进频道, 有 DM 通道
    └── 用户在 Slack 里看到的是它
```

**关键关系**：
- **一个 App 内置一个 Bot User**
- **Bot 是 App 的"化身"** —— 用户在 Slack 看到的是 Bot，背后是 App 在处理逻辑

**两把 Token 分别代表谁**：

| Token | 前缀 | 代表 | 用途 |
|-------|-----|-----|-----|
| Bot Token | `xoxb-...` | Bot 员工的**工牌** | 以 Bot 身份发消息 |
| App Token | `xapp-...` | App 层面的**基础凭证** | 建立 Socket Mode 长连接 |

**用工牌发消息，用 App 凭证建连接。** 两把 token 分层：Bot 干"业务"（说什么、发给谁），App 干"基础设施"（怎么和 Slack 建通道）。

> Slack 其实还有第三种 token 叫 User Token（`xoxp-...`），代表**真实用户**（你自己）。本项目没用到——Bot 是独立身份，不假冒任何真人。

---

### D.3 爬 GitHub 和 HuggingFace 需要翻墙吗？

**结论**：GitHub 直连稳定；HuggingFace 在中国大陆访问不稳定，但**不需要翻墙**——用国内镜像即可。

| 数据源 | 在中国大陆 | 建议 |
|-------|----------|------|
| **GitHub** | 直连**能用**，偶尔慢 | 无需处理 |
| **HuggingFace** | 直连**不稳**（DNS 解析可能失败，见下方 D.4） | 改用镜像 `hf-mirror.com` |
| **Anthropic** | 直连难，代码里带 `r.jina.ai` 兜底 | 无需处理 |

**HuggingFace 镜像怎么配**：`.env` 加一行

```bash
HF_ENDPOINT=https://hf-mirror.com
```

`hf-mirror.com` 是国内独立维护的 HF 公开内容镜像站，中国大陆能直连、内容和 HF 同步。

**Anthropic 的兜底机制**（代码里已有）：

```python
# tech_digest.py
fallback = os.environ.get("ANTHROPIC_NEWS_FALLBACK",
                          "https://r.jina.ai/http://www.anthropic.com/news")
```

`r.jina.ai` 是通用的网页代理服务，会帮你转发抓取请求。所以就算直连 anthropic.com 失败，也有备用路径。

---

### D.4 什么是 DNS，为什么"公司 DNS 有时行有时不行"

**DNS = 域名系统**，把你输入的域名（`huggingface.co`）翻译成 IP 地址（`52.84.150.39`）。

```
你输入 huggingface.co
  → 电脑问 DNS 服务器："这个域名的 IP 是多少？"
  → DNS 回答一个 IP
  → 电脑用 IP 去连
```

**你用的 DNS 是谁？**

| 环境 | DNS 服务器 |
|------|----------|
| 公司 WiFi | 公司自己的 DNS |
| 家里 WiFi | ISP（电信/联通）的 DNS |
| 手机 4G/5G | 运营商的 DNS |

**为什么公司 DNS "有时行有时不行"**（常见三种情况）：

| 情况 | 表现 | 说明 |
|------|------|------|
| **域名被黑名单** | 稳定失败 | 公司出于安全策略，故意不解析某些域名（视频网站、云盘、部分 AI 平台） |
| **DNS 响应慢** | 超时失败 | 公司 DNS 服务器忙 |
| **缓存问题** | 时通时不通 | DNS 缓存中的正确记录过期了、或错误记录暂存下来了 |

**"薛定谔式"表现**：同一个域名，10 分钟前能通、现在不通、一小时后又能通——这就是"时行时不行"的字面意思。

**对定时任务的影响**：抓取失败率不稳定，某天推送里就没这个数据源。**从根本上避免的方法是换一个不在公司限制清单里的域名**（比如 `hf-mirror.com` 取代 `huggingface.co`）。
