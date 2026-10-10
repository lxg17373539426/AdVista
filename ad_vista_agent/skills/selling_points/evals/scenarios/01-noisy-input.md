---
task: "按当前任务生成 MarketingAnalysis。当前请求 goal=“根据 OCR/ASR 生成营销报告”；上传原始 goal=“提取外套卖点”。asset_id=asset_demo，language=zh，subject=广告素材。OCR：ocr_cluster_0001 文本为重复的数字 1 到 45，ocr_cluster_0002 为 2023.30，ocr_cluster_0003 为 [Your input text]，ocr_cluster_0004 为 [图片中没有可识别的文字内容]。ASR：speech_0001 为背景说唱，内容与产品无关。已提供 kf_0001_primary 观察：画面中是米色绒面外观的外套，人物手持手机；无产品字幕或规格。不要补充素材之外的信息。"
baseline_failure: "旧回答从“米色绒面外套”和手持手机得出“羊羔毛材质”“年轻女性活跃社媒”“多场景适配”。"
assertions:
  - "输出可解析的 MarketingAnalysis JSON，没有 HTML。"
  - "没有把羊羔毛、年龄性别、社媒活跃或多场景适配写为事实。"
  - "没有把噪声 OCR 或背景歌词引用为产品卖点依据。"
  - "若保留画面观察，其引用仅为输入真实关键帧，性质为 observed；产品卖点缺口进入 unknowns。"
runs: 3
---

仅使用场景给定内容；行为结果需独立评审，静态检查不证明这些断言已满足。
