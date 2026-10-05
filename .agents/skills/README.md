# 本项目技能(.agents/skills/)

两层结构,换业务域时只加领域包;机制层领域无关、跨域直接复用,不随域修改(与参考实现的行为差异以技能内文档为准):

| 技能 | 层 | 用途 |
| --- | --- | --- |
| `semantic-analytics/` | 机制层(领域无关) | 造数→装载→语义建模→口径→双路径验证→仪表盘的完整方法与脚本 |
| `wren-hr/` | 领域包(HR) | 本仓库 HR 交付的内容地图、口径纪律与实际命令 |
| `show-me/` | 通用工具 | 把当前对话主题画成简洁图示(伪代码/调用树/Mermaid) |

- 新业务域:`python3 .agents/skills/semantic-analytics/scripts/scaffold.py --domain <新域>`,
  然后按其 SKILL.md 的 ②~⑥ 阶段填充
- 技能由 Agent 运行时按各自约定发现(ZCode 已验证读取 `.agents/skills/`;
  其他运行时以各自文档为准),会话中通过斜杠命令显式调用
- `show-me/` 来自 [humanlayer/skills](https://github.com/humanlayer/skills)
  @ `ca7c808` (MIT,许可证全文见 `show-me/LICENSE`),按原样收录
