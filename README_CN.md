# email-monitor

增量处理新邮件，记录处理状态，生成待审阅草稿。

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](README.md)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.2.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ 设计哲学

email-monitor 是一个**薄编排层 skill**。它不自建邮件存储、不自建调度、不自建推送,而是复用本机
已有的三块基座 -- Gmail IMAP 工具链、schedule-reminder 事务池、Discord relay -- 只补上缺口:
增量监控、分类/起草编排、归档/摘要钩子。**回复只生成草稿，由你审阅后发送。**默认的 agent
分类会把邮件内容（包括正文）放入提示词，交给已安装的 `llmcall` 按当前策略路由；它可能调用
外部提供方，因此默认配置不能保证模型处理留在本机。Discord 提醒使用脱敏后的简短摘要。

若要求模型处理只能在本地进行，在私有配置中设置 `runtime.local_only=true`、
`classifier.mode="heuristic"` 和 `topic_labeling.enabled=false`。运行时会拒绝无法确认本地执行的
agent 分类与主题模型。邮箱访问和你配置的通知仍会连接各自的服务。

复用现有基座减少重复账本，但需要这些依赖保持可用。重要性分类、主题标签和归档分别
作决定，避免给邮件加标签时顺手隐藏它。默认模型路由以覆盖能力换取外部处理；
本地启发式模式减少这种暴露，也会失去模型分类能力。

📜 **[完整设计理念 -> PHILOSOPHY.md](PHILOSOPHY.md)**

---

## 它是什么(不是什么)

- **是:**收件箱分诊回路：按 UID 水位线只读收取新邮件，默认用 agent 分类，也可选择启发式规则，
  重要邮件推送提醒，噪音邮件按配置归档。可选的 schedule-reminder 基座提供事务池和每日摘要。
  调用会话可以按配置中的签名和语言起草回复，交给你审阅。
- **不是:**自动发信器(只起草)、第二个事务数据库(用 schedule-reminder)、批量收件箱清理器
  (那个直接用 `gmail-imap-label.py`)。

## 安装

```
/plugin install github:DaizeDong/email-monitor
```

或手动克隆:

```bash
git clone --recurse-submodules https://github.com/DaizeDong/email-monitor.git ~/.claude/plugins/email-monitor
```

使用 Python 3.11 或更新版本，在心跳任务所选的解释器中安装依赖：

```bash
python -m pip install -r requirements.txt
```

llmcall 的 Git 来源需要通过主机已有的凭据或 SSH 配置获得访问权限。匿名访问可能返回
“Repository not found”。PyPI 上的 `llmcall` 属于另一个项目，不能用它替代这里的依赖。

如果主机提供了已批准的 llmcall wheel，可以改用下面的安装方式，不再运行上面的 requirements
命令。请把示例路径换成主机实际提供的文件：

```bash
python -m pip install --no-index "/path/to/llmcall-0.2.0-py3-none-any.whl"
python -m pip install "tzdata; sys_platform == 'win32'"
python -c "from llmcall import Result, active_chain, call; print('llmcall API imports successfully')"
```

即使已经装好 wheel，requirements 命令仍会访问其中声明的 Git 来源。配置 doctor 会检查所选解释器。
路由、凭据、超时和回退仍由已安装的主机包负责，安装 Email Monitor 不会替你配置这些内容。

还需一个经过 PRIVATE 验证的 Git 伴生仓 `email-monitor-config`，存放账户、规则、模板、
纳入版本管理的运行 DATA 和 DPAPI 指针，凭据单独保存。
详见[摘要与部署说明](skills/email-monitor/reference/summary-and-deploy.md)。

## 快速开始

先确认上面的模型路由方式，并配置好私有伴生仓。dry tick 只规划操作，不执行通知、归档等写入，
但仍会读取邮件并按所选策略分类。先用可控的测试邮箱验证，再注册无人值守任务。

```bash
python skills/email-monitor/scripts/em_tick.py --config <路径>/registry.json --dry
pwsh skills/email-monitor/scripts/register-task.ps1 -Config <路径>/registry.json
```

## 配置

`email-monitor` 是**带 config 的 skill**, 它从一个**独立、私有**的伴随 config 仓
(`email-monitor-config`)读取每用户/每机状态(账户拓扑、分类规则、草稿模板、DPAPI 口令指针)。
完整规范见 **[CONFIG.md](CONFIG.md)**。

- **挂载(发现顺序):** `$EMAIL_MONITOR_CONFIG` → `$EMAIL_MONITOR_CONFIG_DIR` →
  Guards 的统一伴生仓解析（包括同级配置仓和旧用户目录），命中后读 `<dir>/registry.json`。
  显式 `--config <registry.json>` 优先。找不到配置时，程序输出结构化错误并以非零状态退出，不发提醒。
- **首次配置:**先创建或克隆经过 PRIVATE 验证的 Git 伴生仓，并让 `EMAIL_MONITOR_CONFIG` 指向它，
  再运行初始化。运行 DATA 留在这个私有仓内，纳入版本管理；无法验证的存储位置会被拒绝。
  ```bash
  python scripts/init_config.py    # 生成符合规范的骨架(确定性)
  export EMAIL_MONITOR_CONFIG=<private-companion>    # 或给 init 传 --out <dir>
  # 编辑 registry.json、把 app 口令录入 DPAPI(Mode B)、填 _personal_layer.json
  python scripts/verify_config.py   # doctor:逐项 PASS/FAIL,明确报缺什么
  ```
- **切换 config(即插即用):** 把环境变量指向另一个 config 目录即可, config 自包含
  (`cred_path` 用 `~`),无需任何别的改动:
  `export EMAIL_MONITOR_CONFIG=~/configs/work` ↔ `~/configs/personal`。
- **密钥:** Mode B, `secrets/*` 已 gitignore,永不入库;真实 app 口令留在 DPAPI
  (`~/.local/secrets/gmail-<slug>.cred`),仓内只存指针。请用库外备份。

## 主题打标(默认关闭,只加标签)

email-monitor 还可以给新邮件附加主题标签 -- 判断邮件"是关于什么的",而不只是重要程度。这是一个
独立的、默认关闭的能力:`registry.json` 里的 `topic_labeling.enabled`(默认 `false`),规范见
**[CONFIG.md](CONFIG.md)**。每条标签判定都必须从发件人或主题里逐字引用一段证据;证据核对不通过的
候选标签会被丢弃而不是硬猜,证据不充分的邮件干脆不打标签。标签只会被加到邮件上;写入路径永远不会
把邮件移出收件箱 -- 加标签和隐藏邮件是两个不同的决定。标签体系本身(有哪些标签、哪些发件人对应哪个
标签)属于 DATA 而非代码,只存在于私有配套 config 里,绝不进这个公开仓库。

## 如何触发

"监控我的邮箱"、"分诊收件箱"、"帮我起草回复"、"有什么重要邮件"、"每日邮件摘要"。注册心跳后无人值守运行。

## 示例输出

合成邮件和草稿示例由 `tools/make_fixtures.py` 生成，保存在
`skills/email-monitor/tests/reliability.json`。提醒使用脱敏摘要，草稿的签名和语言由私有配置决定，
发送前仍需你审阅。

## 局限

离线测试使用合成邮件和被拦截的外部操作，只验证相应的程序行为，不能证明真实邮件分类质量、
通知送达效果或无人值守任务已经可用。

默认的重要性分类通过 llmcall 执行；启发式结果里的 `needs_l2` 不会触发额外的心跳模型调用。
回复文案由调用会话起草，须单独确认该会话的模型路由。仅支持 Gmail IMAP，
状态变更监控（已读、改标签、删除）仍在 roadmap v0.4 中。

## 语言

中文 (`README_CN.md`) · English (`README.md`, 权威版)

## Roadmap · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE)(MIT)。
