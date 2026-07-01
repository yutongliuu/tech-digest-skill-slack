# 开发者文档

面向想要理解、修改或扩展本项目的开发者。普通使用者请看 [README.md](./README.md)。

## 目录

- [1. 整体架构](#1-整体架构)
- [2. 文件职责](#2-文件职责)
- [3. 数据流与数据格式](#3-数据流与数据格式)
- [4. 推荐算法详解](#4-推荐算法详解)
- [5. Slack 集成](#5-slack-集成)
- [6. 环境变量参考](#6-环境变量参考)
- [7. 常见扩展](#7-常见扩展)
- [8. 调试技巧](#8-调试技巧)

---

## 1. 整体架构

系统由两条相互独立又彼此喂养的链路组成：

```
                ┌──────────────────── 推送链路（每天定时跑一次）────────────────────┐
                │                                                                   │
   数据源        │   tech_digest.py          genrec_pipeline.py    send_recommendations.py
 ┌─────────┐    │  ┌──────────────┐         ┌────────────────┐    ┌──────────────────┐
 │ HF 论文  │───┼─▶│ 抓取 + 去重    │────────▶│ 召回 + LLM 重排  │───▶│ LLM 摘要 + 发卡片  │──┐
 │ GitHub  │    │  │ build_report │ report  │ 个性化排序        │recs│ 构建 Slack 卡片    │  │
 │ Anthropic│   │  └──────┬───────┘         └───────▲────────┘    └──────────────────┘  │
 └─────────┘    │         │ 读/写                    │ 读历史                            │
                │     state.json          slack_card_actions.jsonl                      │
                └─────────────────────────────────▲──────────────────────────────────┬─┘
                                                   │ 追加                              │ Slack API
                                                   │                                  ▼
                ┌──────────── 回调链路（常驻后台进程）────────┐               用户 Slack 收到卡片
                │   slack_socket_mode.py                     │                      │
                │  ┌────────────────────────────┐            │   点击 感兴趣/不感兴趣  │
                │  │ Socket Mode 长连接接收点击事件 │◀───────────┼──────────────────────┘
                │  │ 追加到 slack_card_actions     │            │
                │  └────────────────────────────┘            │
                └────────────────────────────────────────────┘
```

**关键点**：用户的点击行为（回调链路记录）会成为下一次推送链路的个性化输入，形成"推送 → 反馈 → 更好的推送"闭环。

---

## 2. 文件职责

```
tech-digest-skill/
├── SKILL.md                      # Agent 指令文件（Claude Code / nanobot 共用）
├── README.md                     # 用户手册
├── DEVELOPER.md                  # 本文档
├── install.sh                    # macOS 本地一键安装
├── .env / .env.example           # 配置（凭证 + 开关）
│
├── scripts/
│   ├── tech_digest.py            # 【数据层】抓取三个源，输出带 diff 的 report
│   ├── genrec_pipeline.py        # 【推荐层】规则召回 + LLM 重排，输出 per-user 推荐
│   ├── send_recommendations.py   # 【展示层】LLM 摘要 + 构建 Slack 卡片 + 发送
│   ├── slack_socket_mode.py      # 【回调层】Socket Mode 长连接，记录用户点击
│   ├── run_daily_recommendations.sh  # 串起 pipeline + send 的入口脚本
│   ├── run_slack_socket.sh       # 启动回调接收器（本地用，写 pid）
│   ├── launch_daily.sh           # launchd 调用：source .env 后跑 run_daily
│   └── launch_socket.sh          # launchd 调用：source .env 后跑 socket
│
└── windows/                      # Windows 专用安装与运行
    ├── install.ps1               # 一键装依赖 + 注册任务计划程序定时任务
    ├── run_daily.ps1             # 手动跑一次完整推送
    ├── run_socket.ps1            # 启动回调接收器（前台）
    └── check.ps1                 # 健康自查（任务/接收器/反馈状态）
```

### 三个核心 Python 模块的分工

| 模块 | 输入 | 输出 | 职责 |
|---|---|---|---|
| `tech_digest.py` | 网络 + `state.json` | `report`（内存 dict） | 只负责抓数据、和上次对比标记 new/same，不做任何个性化 |
| `genrec_pipeline.py` | `report` + 行为日志 | `recommendations.jsonl` | 核心，每个用户算一份个性化推荐 |
| `send_recommendations.py` | `recommendations.jsonl` | Slack 卡片 | 只负责摘要 + 渲染 + 发送，不做推荐决策 |

三者职责清晰分层（数据 / 算法 / 展示），改其中一层一般不影响另两层。

---

## 3. 数据流与数据格式

### 3.1 `state.json`（抓取状态）

`tech_digest.py` 用它做"和上次相比有没有新内容"的去重判断。

```json
{
  "anthropic": { "ids": ["/news/xxx"], "items": [...], "today": "2026-06-03" },
  "hf":        { "ids": ["2606.00386"], "items": [...] },
  "gh":        { "ids": ["microsoft/markitdown"], "items": [...] },
  "updated_at": "2026-06-03T01:00:00+00:00"
}
```

每次抓取后，把本次的 id 列表和上次对比，给每个 item 打 `status: new|same` 标签，并覆盖写回。

### 3.2 `slack_card_actions.jsonl`（行为日志，个性化数据来源）

每行一条用户点击记录，由 `slack_socket_mode.py` 追加写入：

```json
{
  "received_at": "2026-06-03T08:30:00+00:00",
  "event_type": "block_actions",
  "open_id": "U0XXXXXXX",
  "user_id": "yutong",
  "open_message_id": "1717400000.000100",
  "action_tag": "card_action_like",
  "action_value": {
    "push_id": "rec-20260603-090000",
    "item_id": "2606.00386",
    "source": "hf",
    "url": "https://hf-mirror.com/papers/2606.00386",
    "title": "PaddleOCR-VL-1.6: ...",
    "action": "like"
  }
}
```

其中 `action_value.action` 是关键，取值 `like` / `dislike` / `read`。这个字段在 `send_recommendations.py` 构建按钮时写入按钮的 `value`（Slack 中 `value` 是字符串，存的是 JSON），用户点击时 Slack 通过 `block_actions` 事件原样回传，`slack_socket_mode.py` 再 `json.loads` 还原成 dict。

> `open_id` 存的是 Slack 用户 id（`U` 开头），是个性化与投递的关键标识；`user_id` 存的是 username（可能为空）。

### 3.3 `recommendations.jsonl`（推荐结果）

`genrec_pipeline.py` 输出，每行是某个用户的一份推荐：

```json
{
  "user_id": "U0XXXXXXX",
  "generated_at": "2026-06-03T01:00:00+00:00",
  "items": [
    { "id": "2606.00386", "title": "...", "summary": "...", "url": "...", "source": "hf", "score": 9.0 },
    { "id": "microsoft/markitdown", "title": "...", "summary": "...", "url": "...", "source": "gh", "score": 0.0 }
  ]
}
```

`send_recommendations.py` 逐行读取，每个 `user_id` 发一张卡片。

> 注意：`user_id` 字段存的是 Slack 用户 id（`U` 开头，来自行为日志）。发送时作为 `chat.postMessage` 的 `channel` 参数——向用户 id 发送会自动开 bot↔用户 的私聊（DM）。也可填频道 id（`C` 开头）发到频道。

---

## 4. 推荐算法详解

全部逻辑在 `genrec_pipeline.py`。整体是经典的**两阶段召回-重排**：先用规则快速从大池子里筛出候选，再用 LLM 精排。

### 4.1 第一步：拆分历史（`_extract_history`）

把每个用户的点击历史按时间分成两段：

- **recent**（近 `recent_days` 天，默认 15 天）：用于实时的话题打分
- **older**（更早）：交给 LLM 总结成长期偏好

recent 内部会去重（同一 action+title+source+url 只留一条）。

### 4.2 第二步：话题打分（`_build_topic_scores`）

对 recent 历史里的每条记录算分，累加到话题维度：

```
单条得分 = 行为权重 × 时间衰减
```

**行为权重**（`_action_weight`）：

| 行为 | 权重 |
|---|---|
| like（感兴趣） | +3.0 |
| read（查看） | +1.0 |
| dislike（不感兴趣） | −3.0 |

**时间衰减**（`_decay`）：

| 距今 | 衰减系数 |
|---|---|
| ≤ 7 天 | 1.0 |
| ≤ 30 天 | 0.5 |
| 更早 | 0.2 |

**话题归类**（`TOPIC_KEYWORDS`）：用正则把标题映射到话题。当前 6 个话题：

```python
LLM      : llm / language model / transformer / chatbot / agent
CV       : vision / image / diffusion / segmentation / detection
Robotics : robot / manipulation / motion / grasp
Security : security / privacy / attack / adversarial
Data     : dataset / benchmark / evaluation / corpus
Infra    : system / infrastructure / deployment / serving / runtime
```

最终得到形如 `{"topic:LLM": 9.0, "topic:CV": -3.0}` 的用户话题画像。

### 4.3 第三步：长期偏好总结（`_summarize_history`）

把 older 历史丢给 LLM，让它输出 2-3 句中文的偏好总结，作为重排时的补充上下文。无 older 历史则跳过。

### 4.4 第四步：规则召回（`_select_by_rule`）

对每个数据源的候选池，给每个候选打分：

```
候选得分 = max(该候选命中的所有话题的用户话题分)
```

按分数降序取前 `candidate_k` 个（默认 6）作为粗筛结果。

### 4.5 第五步：LLM 重排（`_rerank_by_llm`）

把粗筛的候选 + recent 历史 + older 偏好总结一起给 LLM，让它返回精排后的 top-K 个 id（HF 默认 3、GitHub 默认 3）。

**降级策略**：如果 LLM 调用失败或返回非法 id，自动回退到规则召回的前 K 个，保证一定有结果。

### 4.6 各源的特殊处理

```python
source_plan = [
    ("hf",        candidate_k=6, top_k=3),
    ("gh",        candidate_k=6, top_k=3),
    ("anthropic", candidate_k=6, top_k=-1),   # -1 = 全部保留，不重排
]
```

Anthropic 的 `top_k=-1` 表示有更新就全推（量少且时效性强，不做个性化筛选）。

### 4.7 冷启动

新用户没有行为历史 → 话题分全为 0 → 规则召回退化成"按候选池原始顺序取前 N 个" → 推荐接近随机。积累约 10 次点击后，话题画像才有区分度。这是基于行为的推荐系统的固有特性。

**先有鸡还是先有蛋的死锁**：用户列表 100% 来自行为日志（`_extract_history`），但第一次运行时没人点过按钮 → 用户列表为空 → `recommendations.jsonl` 为空 → 没人收到卡片 → 永远没有点击。`main()` 用 `--seed-users`（环境变量 `SLACK_DEFAULT_USERS`）打破它：把种子用户以空历史并入 `history_by_user`，让他们先收到非个性化推荐。已有历史的用户不受影响（`setdefault` 不覆盖）。种子用户一旦点击，下一轮就从日志自然进入个性化流程。

---

## 5. Slack 集成

### 5.1 发送（Web API）

`send_recommendations.py`：

1. 直接用 Bot Token（`xoxb-`）作为 Bearer 认证（比飞书省去换 `tenant_access_token` 的一步）
2. `_build_blocks` 构建 Block Kit 消息（按 source 分组，每条带「查看 / 感兴趣 / 不感兴趣」三个按钮）
3. POST 到 `chat.postMessage`，body 带 `channel` + `blocks` + `text`(fallback)
4. Slack 即使 HTTP 200 也可能 `ok=false`，代码显式检查并把 `error` 抛出

按钮设计：

- **查看**：带 `url`，点击直接跳转（Slack 中带 url 的 button 不触发 `block_actions` 回调，所以行为日志里一般没有 `read`）
- **感兴趣 / 不感兴趣**：不带 url，点击触发 `block_actions` 回调，`value`（JSON 字符串）里的 `action` 为 `like` / `dislike`

> Slack `value` 必须是字符串且 ≤2000 字符，所以我们把 dict 用 `json.dumps` 序列化进去，回调侧再 `json.loads` 还原。

### 5.2 接收（Socket Mode 长连接）

`slack_socket_mode.py` 用 `slack_bolt` 的 `App` + `SocketModeHandler`，注册一个 `action_id` 以 `card_action_` 开头的处理器。用户点击按钮 → Slack 通过 Socket Mode 推送 `block_actions` 事件 → `ack()` 后提取 `action.value` 追加到日志。

> Socket Mode 等价于飞书的「长连接接收事件」：bot 主动连出去，不需要公网 IP / 域名 / HTTPS 证书，部署在本地或内网都能收到回调。需要 App 开启 Socket Mode 并配一个带 `connections:write` 的 App-Level Token（`xapp-`）。

---

## 6. 环境变量参考

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | ✅ | — | Bot User OAuth Token，`xoxb-` 开头（发消息用） |
| `SLACK_APP_TOKEN` | ✅ | — | App-Level Token，`xapp-` 开头，需带 `connections:write`（Socket Mode 用） |
| `SLACK_DEFAULT_USERS` | ✅ | 空 | 种子用户，逗号分隔的 Slack 用户/频道 id。解决冷启动：无历史时也给这些人发（非个性化），点击后转个性化 |
| `PUSH_TIME` | — | `10:00` | 每日推送时间 `HH:MM`，**用本机本地时区**。install 脚本据此注册定时任务；改完需重跑 install。格式非法则回退 10:00 |
| `GENREC_ENDPOINT` | ✅ | — | LLM API 地址（OpenAI 兼容） |
| `GENREC_API_KEY` | ✅ | — | LLM API Key |
| `GENREC_MODEL` | — | `deepseek-chat` | 模型名 |
| `TECH_DIGEST_DATA_DIR` | — | `~/.tech-digest` | 数据/日志目录 |
| `HF_ENDPOINT` | — | `https://huggingface.co` | HF 地址，国内可设 `https://hf-mirror.com` |
| `TECH_DIGEST_MODE` | — | `all` | `all`=三源；`daily`=只 HF+GitHub（跳过 Anthropic） |
| `TECH_DIGEST_PROXY` | — | 空 | HTTP 代理 |
| `ANTHROPIC_NEWS_FALLBACK` | — | r.jina.ai | Anthropic 抓取失败时的兜底代理地址 |

命令行参数优先级高于环境变量（见各脚本 `argparse` 的 `default=os.environ.get(...)`）。

---

## 7. 常见扩展

### 7.1 新增一个数据源（例如 arXiv）

1. 在 `tech_digest.py` 写 `fetch_arxiv()`，返回 `[{id, title, summary, url}]`
2. 在 `build_report()` 里加一段，写入 `out["arxiv"]` 和 `state["arxiv"]`
3. 在 `genrec_pipeline.py` 的 `_build_candidates()` 加 `add_items("arxiv", "arxiv", ...)`
4. 在 `source_plan` 加 `("arxiv", candidate_k, top_k)`
5. 在 `send_recommendations.py` 的 `SOURCE_LABELS` 加 `"arxiv": "📄 arXiv"`

### 7.2 调整推荐口味

- 改话题：编辑 `TOPIC_KEYWORDS`（加话题 / 调关键词正则）
- 改行为权重：编辑 `_action_weight`（比如让 dislike 更狠）
- 改时间衰减：编辑 `_decay`
- 改每源条数：`run_daily_recommendations.sh` 里给 pipeline 传 `--hf-top-k` / `--gh-top-k`

### 7.3 换 LLM

只改 `.env` 的 `GENREC_*` 三个变量，无需改代码。任何 OpenAI 兼容接口都行（OpenAI / DeepSeek / Ollama / vLLM…）。

### 7.4 换推送渠道（Slack → 其他）

`send_recommendations.py` 的 `_build_blocks` / `_send_blocks` 是 Slack 专属。要换渠道（如飞书 / 企业微信），重写这两个函数即可，推荐算法层（`genrec_pipeline.py`）完全不用动。回调侧同理重写 `slack_socket_mode.py`。

---

## 8. 调试技巧

### 单独测抓取

```bash
python3 scripts/tech_digest.py --mode daily --state /tmp/state.json | python3 -m json.tool
```

### 单独测推荐生成（不发卡片）

```bash
set -a && source .env && set +a
python3 scripts/genrec_pipeline.py --mode daily \
  --state /tmp/state.json \
  --log ~/.tech-digest/logs/slack_card_actions.jsonl \
  --out /tmp/recs.jsonl
cat /tmp/recs.jsonl | python3 -m json.tool
```

### 测发卡片但不真发（dry-run）

```bash
set -a && source .env && set +a
python3 scripts/send_recommendations.py --in /tmp/recs.jsonl --llm-summary --dry-run
```

`--dry-run` 会打印将要发送的 Block Kit JSON 而不调用 Slack API。

### 验证回调是否在工作

```bash
# 看 Socket Mode 长连接是否建立（Bolt 启动 / 已连接 Slack 的日志）
tail ~/.tech-digest/logs/slack_socket.out

# 点一次卡片按钮后，看日志是否新增记录
tail -1 ~/.tech-digest/logs/slack_card_actions.jsonl | python3 -m json.tool
```

### 用国内镜像验证

```bash
HF_ENDPOINT=https://hf-mirror.com TECH_DIGEST_MODE=daily \
  bash scripts/run_daily_recommendations.sh
```

### Windows：一键健康自查（`windows\check.ps1`）

Windows 用户排查"为什么没自动推送"时，先跑这个只读自查脚本：

```powershell
powershell -ExecutionPolicy Bypass -File windows\check.ps1
```

它会逐项报告：

| 检查项 | 说明 |
|---|---|
| 定时任务 `TechDigest-Daily` | 是否存在、下次/上次运行时间、上次结果码 |
| `.env` | 凭证文件是否就位 |
| Socket Mode 接收器 | 进程是否在跑（没在跑是正常的，下次推送会自动拉起） |
| `recommendations.jsonl` | 上次推送距今多久 |
| `slack_card_actions.jsonl` | 已记录多少条点击反馈 |

**常见排查场景**：

- **任务显示 `[MISS]`**：定时任务没注册成功（公司策略 / 权限）。重跑 `windows\install.ps1`；`install.ps1` 现在会在注册后立即验证，失败会明确报错而非静默跳过。
- **`Last run` 是 `1999/11/30` + 结果码 `267011`**：表示任务从未运行过（刚注册或没到触发时间），不是错误。
- **接收器 `[INFO] not running`**：正常。`run_daily.ps1` 每次推送前会自动检查并后台拉起接收器，无需手动常驻。

> 注册任务用的是 `-LogonType Interactive`：任务在用户登录态下运行。注销 Windows 账户时不会触发；锁屏一般仍可运行。`-StartWhenAvailable` 保证错过的运行在下次可用时补跑。

