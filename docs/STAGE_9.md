# Stage 9：受约束 Agent Core

## 目标

Stage 9 将 Stage 8 的固定七阶段编排升级为目标驱动的受约束 Agent。核心不是增加一个 `agent` 目录，而是建立可验证的：

```text
Goal -> Plan -> Act -> Observe -> Reflect -> Ask User / Deliver
```

## 已实现

- `AgentRequest`：目标、模式、交付物、工具预算和重规划预算；
- Qwen Planner：根据自然语言目标选择工具和交付物；
- 规则 Planner：无模型情况下的确定性计划；
- Plan Compiler：本地补齐依赖、固定拓扑顺序和步骤 ID；
- Plan Validator：校验目标、白名单、依赖、参数、重复调用和预算；
- Tool Registry 元数据：输入/输出 Schema、风险、GPU 和缓存属性；
- Tool Executor：复用现有 Stage Builder，记录每次调用；
- Observation：保存缓存命中、Evidence、Insight、Audit 和报告摘要；
- Reflection：记录 `deliver`、`ask_user` 或 `fail` 及原因；
- Agent Session：原子持久化请求、计划、调用、Observation、Reflection 和确认；
- 失败恢复和人工确认。

当前 Demo 的 Reflection 记录为 `ask_user`，原因是 Critic 发现部分高风险主张只有单模态支持；批准操作只更新确认状态，不会重新调用任何工具。

## 双层规划

不让 Qwen 直接控制完整状态机。Qwen 只输出：

```json
{
  "goal": "分析广告风险并生成报告",
  "selected_tools": ["report"],
  "deliverables": ["risk_audit", "report"],
  "requires_confirmation": []
}
```

本地 Plan Compiler 自动补齐：

```text
ingest -> timeline -> speech + ocr -> ledger -> insights -> report
```

这样保留模型的目标理解和工具选择，同时避免模型生成非法依赖、Shell 命令或任意参数。

## 命令

只生成并验证计划：

```bash
.venv/bin/advista-agent agent-plan \
  --goal "只提取字幕和语音证据" \
  --deliverable evidence \
  --qwen
```

执行 Agent：

```bash
.venv/bin/advista-agent agent /path/to/ad.mp4 \
  --goal "分析广告卖点、受众和风险并生成报告" \
  --qwen-planner
```

人工确认或失败恢复：

```bash
.venv/bin/advista-agent agent-resume ingest_<hash> --approve
.venv/bin/advista-agent agent-resume ingest_<hash>
```

## 状态产物

```text
outputs/runs/<run-id>/agent/session.json
```

其中记录：

- 原始用户目标；
- 编译并验证后的执行计划；
- 每次工具调用和耗时；
- 每个 Observation；
- Reflection 决策与证据缺口；
- 人工确认；
- 最终交付物路径；
- 失败原因和恢复状态。

## Demo 验证

同一 Demo 已验证两种目标产生不同计划：

- “只提取字幕和语音证据”止于 `ledger`；
- “分析卖点、目标受众和风险并生成报告”执行到 `report`。

完整目标执行后，Critic 返回 `review`，Agent Reflection 进入 `ask_user`，会话停在 `waiting_confirmation`，没有自行批准高风险广告主张。

## 当前限制

Stage 9 是第一版受约束 Agent Core，不是完整自主 Agent：

- Reflection 已结构化，但现有工具不足以自动补采特定证据，因此风险缺口优先询问用户；
- 尚无 SQLite 多轮消息和长期记忆；
- 尚无局部时间段重分析工具；
- 尚无 Hook、脚本、分镜和 A/B 交付工具；
- 尚无 Web 对话界面。

下一阶段应实现 Stage 10：多轮会话、SQLite 短期记忆和基于已有 Artifact 的局部追问，不重新解析整个视频。
