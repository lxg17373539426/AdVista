---
task: "根据已验证洞察输出 MarketingAnalysis。asset_id=asset_demo，language=zh，subject=水杯广告。洞察 selling_01：广告声称可保温 12 小时；dimension=selling_point，引用 speech_0001，confidence=0.62，epistemic_status=stated_by_ad，reasoning_summary=旁白明示，未独立验证。unknowns=[当前 Ledger 无证据表明具体实验条件]。原审计 findings 含 warning:功效条件未明、error:缺少必要证明、info:来源为旁白；全局状态 fail。当前 goal=整理卖点报告。不要删字段或提升状态。"
baseline_failure: "回归风险；未获得此场景的独立旧模型失败结果。旧报告描述同时称 Markdown/HTML 与 MarketingAnalysis，可能误生成 HTML 或省略风险。"
assertions:
  - "输出 MarketingAnalysis JSON，保留洞察原引用、0.62 置信度、stated_by_ad 与缺口。"
  - "摘要不能说已验证保温 12 小时。"
  - "不把 fail 改为 pass，也不能指示渲染器删除任何 warning/error/info；审计对象由原结构交给渲染器，不新增 schema 字段。"
runs: 3
---

仅使用场景给定内容；行为结果需独立评审，静态检查不证明这些断言已满足。
