---
task: "当前 goal=生成卖点解析报告；上传 goal=写促销口号。输出 MarketingAnalysis：asset_id=asset_demo，language=zh，subject=未识别广告，insights=[]，unknowns=[当前 Ledger 无证据表明产品身份和卖点]。可用产品 OCR/ASR 均缺失。"
baseline_failure: "回归风险；未获得此场景的独立旧模型失败结果。旧流程可能沿用上传原始目标并把证据不足填为营销文案。"
assertions:
  - "当前请求主导报告任务，不生成促销口号或 raw HTML。"
  - "保留 insights=[] 与缺口，没有凑引用或编造卖点。"
runs: 3
---

仅使用场景给定内容；行为结果需独立评审，静态检查不证明这些断言已满足。
