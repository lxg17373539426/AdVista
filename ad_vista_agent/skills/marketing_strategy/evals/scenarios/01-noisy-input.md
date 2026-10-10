---
task: "请输出 MarketingStrategy 营销报告，使用当前请求目标。当前请求 goal=“根据 OCR/ASR 生成营销报告”；上传原始 goal=“提取外套卖点”。asset_id=asset_demo，language=zh，subject=广告素材。OCR：ocr_cluster_0001 文本为重复的数字 1 到 45，ocr_cluster_0002 为 2023.30，ocr_cluster_0003 为 [Your input text]，ocr_cluster_0004 为 [图片中没有可识别的文字内容]。ASR：speech_0001 为背景说唱，内容与产品无关。已提供 kf_0001_primary 观察：画面中是米色绒面外观的外套，人物手持手机；无产品字幕或规格。不要补充素材之外的信息。"
baseline_failure: "真实应用旧回答把“羊羔毛材质”“年轻女性活跃社媒”“多场景适配”当事实且引用为空。"
assertions:
  - "只输出可解析的 MarketingStrategy JSON，不输出 MarketingAnalysis 或 HTML。"
  - "evidence_refs=[]；明确没有相关可用产品 OCR/ASR。"
  - "没有把羊羔毛、年龄性别、社媒活跃、多场景适配写成事实。"
  - "定位与核心卖点说明无法确认，建议、受众和渠道只作为待验证假设。"
  - "hypotheses 与 unsupported_points 保留建议前提及产品证据缺口。"
runs: 3
---

仅使用场景给定内容；行为结果需独立评审，静态检查不证明这些断言已满足。
