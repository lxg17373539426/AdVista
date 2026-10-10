---
name: marketing-strategy
description: 当用户要求基于广告视频 OCR/ASR 与已验证卖点生成营销报告、营销策略、传播方案、投放建议或定位建议，或要求区分营销事实与待验证受众渠道假设时使用。仅做卖点解析用 analysis-report；仅改 HTML 排版用 report-frontend。
metadata:
  version: "3.1.0"
---

# 广告营销报告技能（Marketing Strategy Skill）

依据已验证洞察和 Evidence Ledger 生成 `MarketingStrategy`，中文表达。组合 `selling-points`；最终 HTML 使用现有 `report-frontend` 与确定性渲染器。

## 输入和证据门槛

当前请求的目标优先于上传时的原始目标。相关且可用的 OCR/ASR 是产品身份、卖点和广告主张的首要依据；先读证据内容，再判断 ID 是否适合引用。

占位符、识别失败提示、重复编号、上下文不明的数字和无关背景歌词不能支持产品营销结论。画面只补充可见的形状、颜色和动作；不能由米色绒面外观确认羊羔毛材质，也不能由手持手机确认年轻女性、社媒活跃度或使用场景适配性。

## 三类内容分别写

- 视频事实：证据直接支持的内容，标明事实性质和来源。
- 广告主张：以“广告声称”标记；不升级成独立验证的功效或性能事实。
- 策略建议：以“建议”或“待验证假设”表达。受众、渠道、内容方向或转化建议需要验证的前提写入 `hypotheses`，不得说视频已经证明这些建议有效。

`hypotheses` 中记录各建议依赖的可测试前提；例如受众兴趣、渠道适配、不同内容是否改善点击或转化。不得编造人口画像、市场规模、价格、成分、功效或转化率。

## Process（组织报告）

1. `positioning` 仅说明当前证据支持的广告定位；无法确认产品则明确“无法从当前产品文本确认定位”。
2. `target_audience` 区分已有线索与待验证人群假设；没有依据时说明未确认，不用外观推断年龄性别。
3. `core_message` 使用相关文本支持的广告主张；缺少依据时说明暂不能形成已验证核心卖点。
4. `communication_strategy`、`content_directions`、`channel_recommendations`、`conversion_recommendations` 写可执行建议，并显式保留建议性质。
5. `unsupported_points` 列出产品身份、材质、功效、受众、价格等当前缺口；无证据时建议先补充产品字幕、旁白、规格或用户测试数据。

## Output contract（输出契约）

只返回符合 `MarketingStrategy` 的完整 JSON，不返回原始 HTML、Markdown 报告或多余说明。

- 必填：`positioning`、`target_audience`、`core_message`。
- 四个建议列表各 1–8 项；证据不足也可写一项“建议先补充……再验证……”，不得为填列表编造卖点。
- `hypotheses` 最多 8 项，`unsupported_points` 最多 12 项。
- `evidence_refs` 为 0–12 个，仅复制输入中真实存在且支持营销结论的 `speech_*` / `ocr_cluster_*` ID；不引用洞察 ID，不构造引用，不用关键帧代替缺失产品文本。
- 无相关且可用的 OCR/ASR 时返回 `evidence_refs=[]`，并明确说明哪些营销信息无法确认。不能为了 schema 数量要求引用歌词、噪声或占位符。
- 画面观察可以作为明确标注的补充线索，但不能据此确认产品卖点；引用限制保持上述文本证据契约。

JSON 必须包含全部以下键，尤其不能省略 `evidence_refs`。下面仅展示缺证时的完整形状；有有效产品文字时按当前证据替换内容和引用。

```json
{"positioning":"无法从当前产品文本确认定位","target_audience":"受众未确认","core_message":"暂不能形成已验证核心卖点","communication_strategy":["建议先补充产品说明"],"content_directions":["建议制作清晰产品字幕"],"channel_recommendations":["建议补证后测试渠道"],"conversion_recommendations":["建议核实购买入口"],"hypotheses":[],"evidence_refs":[],"unsupported_points":["当前缺少相关产品文本"]}
```

## Verification（返回前核对）

## Self-check（逐条确认）

- [ ] 引用存在且相关；无依据时引用为空；缺口完整。
- [ ] 事实、广告主张和建议已区分，受众渠道假设在 `hypotheses` 中。
- [ ] JSON 完整且没有 schema 之外字段。

## Anti-patterns（避免）

不为 schema 凑无关引用，不把建议包装成视频已证实的效果，不输出截断字符串。
