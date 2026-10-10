---
task: "应用已同时加载 selling-points 与 marketing-strategy。当前交付物是营销报告，请根据以下已验证依据输出最终 JSON：ocr_cluster_0001 明确写容量 500 mL，speech_0001 声称保温 12 小时，无受众或渠道数据。不要返回内部洞察对象，按最终任务完成。"
baseline_failure: "fresh 代码与 schema 审查确证旧组合包含两个互斥的仅输出指令；未运行该场景的旧模型行为。"
assertions:
  - "最终对象只符合 MarketingStrategy；没有 MarketingAnalysis 的 asset_id、insights、unknowns 等多余字段。"
  - "广告声称保温 12 小时保持主张性质；受众渠道方案属于待验证假设。"
  - "引用只取给定的相关文本 ID。"
runs: 3
---

使用组合加载的两个技能正文，检查最终交付 schema。
