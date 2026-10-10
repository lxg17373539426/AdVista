---
name: analysis-report
description: 当用户要求把广告视频卖点洞察整理成卖点解析报告、分析报告、可追溯的报告文档，或核对卖点报告的证据和风险保留情况时使用。营销策略内容用 marketing-strategy；报告 HTML 排版用 report-frontend。
metadata:
  version: "3.0.0"
---

# 卖点解析报告技能（Analysis Report Skill）

将已验证的洞察组织为 `MarketingAnalysis`，供程序渲染中文卖点报告。组合 `selling-points`；HTML 交付使用现有 `report-frontend` 契约与确定性渲染器，不让模型生成 HTML。

## Process（报告内容）

按以下顺序组织：执行摘要、核心卖点、用户痛点、目标受众线索、创意结构、转化路径、品牌露出、风险提示、当前证据缺口。存在有效依据的维度才呈现；核心卖点优先，不能用无依据结论填充空章节。

摘要来自已采纳洞察与缺口，不新增产品材质、功效、人口画像、市场数据或销售结果。当前请求目标决定主题与摘要重点；上传时的原始目标只作为历史上下文。

## 证据与风险保留

- 完整保留每一条 `claim`、`evidence_refs`、`confidence`、`epistemic_status`、`reasoning_summary`、`unknowns` 及审计发现；不能因排版省略低置信度内容、来源或风险。
- 区分画面观察、广告主张、待验证推断与证据不足；摘要不得把推断或广告主张升级为客观事实。
- 广告功效主张保持“广告声称”的性质；审计通过只表示证据检查通过，不代表独立验证功效。
- warning/error 风险在 HTML 中保持可见；全部逐条与全局审计记录由渲染层附加，保持原审计状态 `pass/review/fail` 与严重度，不改成乐观措辞。
- 没有相关产品 OCR/ASR 时，可保留有引用的直接画面观察，但产品卖点与定位应明确证据不足。没有任何可用洞察时保留 `insights=[]` 与缺口，不凑引用。

## Output contract（输出契约）

只返回符合 `MarketingAnalysis` 的完整 JSON。报告模型字段由原结构保留，执行摘要只重新组织已验证信息。审计字段保留在原审计对象，由报告渲染器附加；不要向 `MarketingAnalysis` 塞入 schema 不支持的字段。

营销报告应返回 `MarketingStrategy` 并使用 `marketing-strategy`；不要把营销策略混写成这个报告的洞察。品牌与产品名保留原文。

## Verification（返回前核对）

## Self-check（逐条确认）

- [ ] 核心卖点优先，有效维度、引用、置信度、缺口与审计记录完整。
- [ ] 推断与主张没有变成事实，输出合法 JSON，HTML 交给渲染器。

## Anti-patterns（避免）

不以可读性为由删风险、改变审计状态或新增视频未支持的事实。
