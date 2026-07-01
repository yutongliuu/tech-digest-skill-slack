---
name: tech-digest
description: 发送每日 AI/技术个性化推荐到 Slack。当用户要求"发技术推荐""跑一次推荐""推送今日技术动态""tech digest"或类似请求时使用。抓取 HuggingFace 论文、GitHub Trending、Anthropic 动态，用 LLM 根据用户历史点击做个性化重排后推送 Slack 卡片。
---

# tech-digest skill

按需向 Slack 推送一次个性化的 AI/技术推荐卡片。

**重要**：加载此 skill 后，你必须**立即用 Bash 工具实际执行**下面「触发推送」里对应操作系统的命令，不能只是把命令展示给用户。执行完把命令输出（尤其是 `{"sent": ...}` 那行）报告给用户。这个 skill 的唯一目的就是跑那条命令——加载 skill ≠ 完成任务，你必须真正调用 Bash 工具运行它。

## 触发推送

根据当前操作系统选对应命令——只跑一条，不要两个都跑：

### Windows
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\skills\tech-digest\windows\run_daily.ps1"
```

### macOS / Linux
```bash
cd ~/.claude/skills/tech-digest && set -a && source .env && set +a && bash scripts/run_daily_recommendations.sh
```

> 如果 skill 不是装在 `~/.claude/skills/tech-digest`，把路径换成实际安装目录。

成功的标志是看到 `{"sent": N, "failed": 0, ...}` 这样的输出。

## 检查回调接收器状态（仅当用户问起时）

回调接收器（Slack Socket Mode）负责记录用户点击「感兴趣/不感兴趣」反馈，需要常驻后台运行：

### Windows
```powershell
Get-Content "$env:USERPROFILE\.tech-digest\logs\slack_socket.out" -Tail 20
```

### macOS / Linux
```bash
tail -n 20 ~/.tech-digest/logs/slack_socket.out
```

看到 Bolt 启动日志（`⚡️ Bolt app is running!` / `Now connected to Slack`）即为正常。

## 规则

- 只运行入口脚本（Windows: `windows\run_daily.ps1`；mac/Linux: `scripts/run_daily_recommendations.sh`），不要单独调用 `tech_digest.py`、`genrec_pipeline.py`、`send_recommendations.py`。
- 不要总结或复述推荐卡片的内容（卡片已直接发到用户 Slack）。
- 任务完成后立即停止，不要做额外的调查、研究或改进。
- 脚本若报错，原样报告错误并停止，不要尝试调试或修改代码。
- 用户没问起时，不要主动检查回调接收器状态、不要查看日志文件。
