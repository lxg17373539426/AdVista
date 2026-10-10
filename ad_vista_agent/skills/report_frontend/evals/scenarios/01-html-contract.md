---
task: "在 /tmp 内设计两类报告渲染检查方案，不能修改项目实现。卖点模型为 MarketingAnalysis，营销模型为 MarketingStrategy，营销 evidence_refs=[] 且含待验证假设。标题与来源文本包含 <script>alert(1)</script> 和 <img src=x onerror=alert(1)>。审计含 warning/error/info。指出已有确定性 renderer 必须如何呈现，并给可观测断言。"
baseline_failure: "回归风险；未获得此场景的独立旧模型失败结果。旧契约只明确分析报告，营销报告没有共享 HTML 交付说明。"
assertions:
  - "两类报告都使用确定性自包含 renderer，不要求模型生成 raw HTML。"
  - "恶意标题和来源转义成文本，无 script 或 onerror 可执行注入。"
  - "空引用显示无相关文本依据，假设显示待验证；所有来源和缺口保留。"
  - "warning/error 保持可见，info 记录保留；原生 details/summary、语义章节、移动端和打印完整。"
  - "两类交付保留 frontend_skill_sha256 指纹。"
runs: 3
---

仅使用场景给定内容；行为结果需独立评审，静态检查不证明这些断言已满足。
