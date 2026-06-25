# email-monitor

全自动监控你的邮箱:分类、告警、归档、起草、摘要 -- 经过验证,而非仅仅生成。

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ 先读这里 -- 设计理念

email-monitor 是一个**薄编排层 skill**。它不自建邮件存储、不自建调度、不自建推送,而是复用本机
已有的三块基座 -- Gmail IMAP 工具链、schedule-reminder 事务池、Discord relay -- 只补上缺口:
增量监控、分类/起草编排、归档/摘要钩子。两条红线绝对:**回复永不自动发送**(只产草稿,用户在
Gmail 点 Send);**邮件正文永不离开本机模型**(Discord 只收脱敏标题,公开仓不存任何 PII)。

📜 **[完整设计理念 -> PHILOSOPHY.md](PHILOSOPHY.md)**

---

## 它是什么(不是什么)

- **是:**无人值守的收件箱分诊回路 -- 增量收新邮件(UID 水位线,只读)、按重要性分类(规则 ->
  廉价打分 -> 仅不确定少数走 LLM)、重要项推 Discord、垃圾归档、每个事项作为 task 写入
  schedule-reminder 池、起草简洁纯 ASCII 回复供你审阅、每日摘要。
- **不是:**自动发信器(只起草)、第二个事务数据库(用 schedule-reminder)、批量收件箱清理器
  (那个直接用 `gmail-imap-label.py`)。

## 安装

```
/plugin install github:DaizeDong/email-monitor
```

或手动克隆:

```bash
git clone https://github.com/DaizeDong/email-monitor.git ~/.claude/plugins/email-monitor
```

还需一个私有配套仓 `email-monitor-config`,存账户拓扑/规则/模板/DPAPI 指针(secrets 已 gitignore)。
详见 `reference/summary-and-deploy.md`。

## 快速开始

```bash
python skills/email-monitor/scripts/em_tick.py --config <路径>/registry.json --dry
pwsh skills/email-monitor/scripts/register-task.ps1 -Config <路径>/registry.json
```

## 如何触发

"监控我的邮箱"、"分诊收件箱"、"帮我起草回复"、"有什么重要邮件"、"每日邮件摘要"。注册心跳后无人值守运行。

## 示例输出

脱敏 Discord 提醒 `[URGENT] user1: payment failed account`、池事务
`Reply to mail re AcmeCorp start date`、以及结尾恰为 `Daize Dong` 的纯净 Gmail 草稿。

## 局限

v0.1:L2 LLM 分类与回复文案由调用会话产出(skill 提供确定性闸/模板/路由)。仅支持 Gmail IMAP。
状态变更监控(已读/改标签/删除)列为 roadmap v0.4。

## 语言

中文 (`README_CN.md`) · English (`README.md`, 权威版)

## Roadmap · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE)(MIT)。
