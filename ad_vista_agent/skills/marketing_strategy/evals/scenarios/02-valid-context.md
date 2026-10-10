---
task: "根据以下输入输出 MarketingStrategy。当前请求 goal=“提炼视频卖点”。asset_id=asset_demo，language=zh，subject=水杯广告。ocr_cluster_0001：容量 500 mL，售价 99 元；speech_0001：本广告声称可保温 12 小时。kf_0001_primary：圆柱形蓝色水杯。广告没有提供材质证明、实验条件或人口属性。额外 ocr_cluster_0002：[Your input text]；speech_0002：无关背景歌词。"
baseline_failure: "回归风险；未获得此场景的独立旧模型失败结果。旧 evidence_refs 要求 1–12 个，未明确引用内容必须相关。"
assertions:
  - "evidence_refs 只引用 ocr_cluster_0001 和/或 speech_0001，不含占位符、歌词或关键帧。"
  - "规格与价格为文本支持，保温 12 小时保持广告声称性质。"
  - "受众、渠道建议作为待验证假设写进 hypotheses；不虚构人口画像或转化率。"
runs: 3
---

仅使用场景给定内容；行为结果需独立评审，静态检查不证明这些断言已满足。
