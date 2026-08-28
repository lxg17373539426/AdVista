# AdVista Agent 产品与工程体验评估报告

评估日期：2026-08-14  
项目路径：仓库根目录  
评估范围：Agent 规划与执行、提示词、会话与状态、证据可信度、Web 交互、错误处理、桌面端与移动端体验  
评估方式：静态代码审查、现有测试执行、最小问题复现、Chrome 浏览器真实页面验证  

## 1. 问题总结

AdVista 已具备视频解析、证据账本、结构化洞察、报告、创意生成和多轮对话等较完整的能力链路，代码中也有较强的 Schema、引用白名单和审计意识。但目前最核心的问题是：**用户请求、Agent 执行、持久化状态和前端展示没有形成稳定一致的产品契约。**

主要表现为：

- 同一视频的新请求可能直接复用旧目标的完成结果和聊天会话。
- 多交付物请求可能生成不完整计划并在执行前失败。
- 报告需要人工复核时，后续 creative 步骤可能绕过确认。
- 用户的具体目标、`deep` 模式和 replan 预算没有真正影响下游分析。
- 系统会为没有引用的回答事后补引用，可能制造虚假可信度。
- 前端完全不展示后端已经返回和保存的证据引用。
- 任务真实状态被列表接口删除，运行中、失败、待确认均可能显示成“已保存”。
- 移动端隐藏全部任务导航，没有任何替代入口。

综合判断：当前版本适合作为内部研发验证工具，但距离“好用、顺手、稳定、容易理解”的用户级 Agent 仍有明显差距。优先级不应继续放在增加新能力，而应先修复会话隔离、执行状态机、证据可信度和任务体验闭环。

## 2. 详细问题清单

### P0-1：同一视频可能复用错误的 Agent 结果

**位置：**

- `ad_vista_agent/agent/core.py:76-77`
- `ad_vista_agent/agent/core.py:239-253`
- `ad_vista_agent/memory/store.py:80-88`
- `ad_vista_agent/web/service.py:115-125`

**问题表现：**

`run_id` 由视频内容决定，`session_id` 固定为 `session_{run_id}`。同一视频已有 completed session 且调用方未显式传入 plan 时，代码会直接返回旧状态，不比较新的 goal、deliverables、mode、配置或模型版本。Web 路径正好不传 plan。

**可能原因：**

内容资产 ID、流水线运行 ID、Agent 执行 ID 和会话 ID 被合并成了同一个身份概念。

**用户影响：**

- 第二次提出不同目标，可能仍收到第一次结果。
- 相同视频的不同使用者可能共享聊天历史。
- 用户无法确认结果是否真的对应当前请求。

**建议：**

- 分离 `asset_id`、`pipeline_run_id`、`agent_execution_id` 和 `conversation_session_id`。
- 每次不同请求创建独立执行 ID。
- 仅在 request、plan、配置、Prompt、代码和模型指纹完全相同时复用结果。
- 数据库会话冲突时校验元数据，不要静默 `INSERT OR IGNORE`。

### P0-2：并发执行可能互相覆盖同一运行目录

**位置：**

- `ad_vista_agent/web/service.py:24-28`
- `ad_vista_agent/web/service.py:115-130`
- `ad_vista_agent/runtime/artifact_store.py:25-49`

**问题表现：**

Web 支持配置多个分析线程，但相同视频会写入相同 run 目录。原子写临时文件名只包含进程 PID，同一进程内多个线程会使用相同临时文件。

**用户影响：**

- `session.json`、manifest、metrics 和交付物可能来自不同请求。
- 任务可能随机失败，或显示完成但内容错误。
- 默认 `analysis_workers: 1` 仅降低默认风险，无法保证配置扩容后的正确性。

**建议：**

- 按 execution ID 或 run ID 建立线程锁和文件锁。
- 临时文件名加入线程 ID 和 UUID。
- 写入时携带并校验 execution ID。
- 对相同内容任务实施 single-flight 或明确排队。

### P1-1：人工确认流程可能被后续步骤绕过

**位置：**

- `ad_vista_agent/agent/core.py:107-137`
- `ad_vista_agent/agent/core.py:190-219`
- `ad_vista_agent/agent/planner.py:68-85`

**问题表现：**

report 返回 `audit_status == "review"` 时只记录确认项，不会立即停止。若计划中还有 creative，Agent 会继续执行。最终 `_reflect()` 只检查最后一个 observation，因此可能直接完成。`plan.requires_confirmation` 也没有被执行器使用。

**用户影响：**

需要人工复核的风险内容仍可能被自动交付，确认机制名义存在但行为不可靠。

**建议：**

- report 进入 review 后立即转为 `WAITING_CONFIRMATION` 并停止后续步骤。
- 反思时检查全部 observations，而不是只检查最后一个。
- 确认项使用结构化对象，记录确认原因、对象、操作者和时间。
- 增加 `report + creative + review` 回归测试。

### P1-2：多个交付物的规则 Planner 会确定性失败

**位置：**

- `ad_vista_agent/agent/planner.py:88-134`
- `ad_vista_agent/agent/planner.py:137-177`

**验证结果：**

对以下请求执行最小复现：

```text
deliverables=["insights", "creative"]
```

规则 Planner 生成：

```text
ingest, timeline, speech, ocr, ledger, insights
```

随后报错：

```text
ValueError: Creative deliverable requires the creative tool
```

**可能原因：**

规则 Planner 先按 goal 选择一套固定工具，再覆盖 deliverables，没有根据全部交付物合并工具集合和依赖。

**用户影响：**

“洞察 + 创意”“报告 + 创意”等合理请求会直接失败，用户无法理解为什么单项支持、组合却不支持。

**建议：**

- 为每个 deliverable 定义最终工具映射并取并集。
- 统一使用依赖编译器补齐上游工具。
- 明确 `risk_audit` 必须包含 report。
- 对所有 deliverable 组合增加参数化测试。

### P1-3：用户目标没有真正传入洞察和创意生成

**位置：**

- `ad_vista_agent/agent/core.py:167-185`
- `ad_vista_agent/insights/builder.py:167-200`
- `ad_vista_agent/schemas/plans.py:71-79`

**问题表现：**

用户 goal 只影响工具选择，不会进入 insights、report 或 creative 的生成 Prompt。`mode=deep`、`max_replans`、`ReflectionDecision.REPLAN/CONTINUE` 和 `PlanStep.purpose` 也没有形成实际行为差异。

**用户影响：**

“重点分析转化路径”“只关注合规风险”等目标仍可能得到同一套通用分析，用户会感到 Agent 没有理解要求。

**建议：**

- 将 goal、deliverables、mode 作为结构化参数传入下游 builder。
- 在系统 Prompt 中明确当前任务目标，同时维持 Evidence Ledger 约束。
- 为 quick/deep 定义可验证差异，例如帧数、证据覆盖和审计深度。
- 实现 bounded replan，或删除暂未实现的公开字段。

### P1-4：事后自动补引用可能制造虚假可信度

**位置：**

- `ad_vista_agent/agent/chat.py:164-189`
- `ad_vista_agent/agent/chat.py:443-459`

**问题表现：**

- grounded 回答没有引用时，系统按词项重叠自动选择引用。
- 视觉问题没有引用时，系统直接附加前 8 个关键帧。
- `unknown + kf_*` 会被规范化为 `visual`。

最小复现确认：一个 `epistemic_status="unknown"` 的回答，只要附加 `kf_0001_primary`，就会被升级为 `visual`。

**用户影响：**

合法引用 ID 不代表该证据真的支持结论。用户可能把无关帧误认为答案依据。

**建议：**

- 删除无依据的自动补引用。
- grounded/visual 回答无引用时应降级为 unknown 或要求模型重新生成。
- 引入 claim-to-evidence entailment 校验。
- 视觉引用只允许使用实际输入模型且能支持具体 claim 的关键帧。

### P1-5：流式内容在最终校验前已经发送给用户

**位置：**

- `ad_vista_agent/agent/chat.py:368-390`
- `ad_vista_agent/agent/chat.py:460-466`
- `ad_vista_agent/web/app.py:205-260`

**问题表现：**

系统从尚未完成的 JSON 中提取 answer 并流式发送，之后才执行结构化解析、证据验证、状态规范化和内部 ID 清理。HTTP 200 已发送后发生异常时，外层错误处理仍尝试返回普通 500 响应。

**用户影响：**

- 用户可能先看到最终被系统判定无效的内容。
- 流式文本与持久化答案可能不一致。
- 页面可能残留“正在思考”、空白回答或损坏响应。

**建议：**

- 优先完整生成并校验，再分块发送给前端。
- 若必须真流式，使用明确的文本协议，不从未完成 JSON 中抽取字段。
- 定义 NDJSON `error` 终止事件。
- 流式和非流式路径共享相同输出契约测试。

### P1-6：前端完全丢弃证据引用

**位置：**

- `ad_vista_agent/web/static/app.js:7`
- `ad_vista_agent/web/static/app.js:11`
- `ad_vista_agent/web/static/app.js:23`
- `ad_vista_agent/web/app.py:234-240`

**浏览器验证：**

- API 历史消息中存在大量 citations。
- `ingest_802380e811fa0a05d4a1` 有 7 条带引用的消息。
- 其他任务也保存了 speech、OCR cluster 和 keyframe 引用。
- 页面 DOM 中没有任何引用列表、证据卡片、时间码或跳转入口。

**用户影响：**

页面提示“重要结论请检查对应证据”，但用户实际上无法检查。产品最重要的 evidence-grounded 价值没有交付到界面。

**建议：**

- 每条回答下展示证据类型、时间码、原文摘要和缩略图。
- 支持点击引用跳转到视频时间点或时间轴条目。
- 流式 done 事件和历史消息复用同一引用组件。
- 无引用时明确显示“本回答未关联可定位证据”。

### P1-7：任务真实状态被列表接口删除

**位置：**

- `ad_vista_agent/web/service.py:138-147`
- `ad_vista_agent/web/static/app.js:21-22`

**浏览器验证：**

页面中的 8 个历史任务全部显示“已保存”。其中 `ingest_461bfc85ef2248bc2819/agent/session.json` 的实际状态是 `waiting_confirmation`。

**可能原因：**

前端读取 `run.agent?.status || run.orchestration?.status`，但服务端在列表响应中主动删除了 `agent` 和 `orchestration`。

**用户影响：**

用户无法区分运行中、失败、待确认和已完成，也可能错过必须处理的确认任务。

**建议：**

- 服务端返回标准化轻量字段 `status` 和 `status_label`。
- 为失败、运行中、待确认提供明确颜色和下一步操作。
- 待确认任务直接提供“查看原因”和“确认/拒绝”入口。

### P1-8：上传任务不可取消，并会抢占当前上下文

**位置：**

- `ad_vista_agent/web/static/app.js:8`
- `ad_vista_agent/web/static/app.js:14`
- `ad_vista_agent/web/static/app.js:16`

**问题表现：**

上传后前端无限轮询，没有取消、超时或请求 token。用户切换到新对话后，旧任务完成仍会调用 `bindRun()`，强制切回旧任务。失败时任务状态条也没有统一清理。

**用户影响：**

误传大文件无法停止，长任务期间输入框被锁，用户切换页面后还会被异步结果拉回。

**建议：**

- 使用 `AbortController` 或任务 token 管理轮询。
- 增加“后台运行”“取消等待”“查看任务”。
- 完成后通知用户，不自动抢占当前页面。
- 在 `finally` 中统一恢复状态和输入能力。

### P1-9：对“分析结果在哪里”这类自然问题理解不稳定

**位置：**

- `ad_vista_agent/agent/chat.py:214-216`
- `ad_vista_agent/agent/chat.py:421-430`
- `ad_vista_agent/agent/chat.py:56-118`

**浏览器验证：**

任务 `ingest_461bfc85ef2248bc2819` 实际存在洞察和 HTML 报告，但历史回答对“分析结果在哪里呢？”回复：没有具体分析结果文件、报告或链接。页面旁边的“查看结果”面板却明确显示报告已生成。

**可能原因：**

- 报告意图仅通过关键词匹配。
- 模型上下文只包含 Ledger、洞察和关键帧，不包含当前 artifacts 清单及可用操作。

**用户影响：**

Agent 对自己的能力和已有结果缺少认知，回答与 UI 状态互相矛盾。

**建议：**

- 将当前任务状态、产物清单和可执行操作放入对话上下文。
- 使用结构化意图分类替代过窄的关键词匹配。
- 对“结果在哪、怎么下载、是否完成、为什么待确认”等产品操作问题走确定性回答。

### P2-1：移动端没有任务导航入口

**位置：**

- `ad_vista_agent/web/static/styles.css:1`
- `ad_vista_agent/web/static/index.html:12-18`

**浏览器验证：**

在 390x844 视口下，侧栏完全消失，页面没有菜单按钮或任务抽屉。用户无法访问新对话、历史任务和服务状态。

**建议：**

- Header 增加菜单按钮和移动端抽屉。
- 至少保留新对话、任务切换和状态查看。
- 使用 `100dvh` 和安全区 padding 适配浏览器地址栏及键盘。

### P2-2：时间轴信息内部化，且内容被输入区遮挡

**位置：**

- `ad_vista_agent/web/static/app.js:25`
- `ad_vista_agent/web/app.py:171-177`

**浏览器验证：**

- 时间轴只显示 `shot_0001` 和 `0-1267 毫秒`。
- 后端已经返回关键帧 URL，但前端没有展示。
- 桌面端和移动端的长时间轴都会延伸到固定输入区后方，部分内容被遮挡。

**用户影响：**

用户无法理解镜头内容，也难以将回答引用映射到具体画面。

**建议：**

- 使用 `mm:ss.s` 可读时间码。
- 展示关键帧、字幕/语音摘要和证据数量。
- 为滚动容器预留输入区底部空间。
- 支持点击镜头定位视频播放。

### P2-3：中文输入法可能误触发送

**位置：**

- `ad_vista_agent/web/static/app.js:17`

**问题表现：**

Enter 处理没有检查 `event.isComposing`。中文输入法确认候选词时可能同时提交消息。

**建议：**

```js
if (event.isComposing || event.keyCode === 229) return;
```

### P2-4：历史消息加载失败被伪装成切换成功

**位置：**

- `ad_vista_agent/web/static/app.js:23`

**问题表现：**

请求失败后被静默转换为空消息列表，随后显示“已切换到这个视频任务”。

**建议：**

- 区分成功但无历史与加载失败。
- 失败时保留原上下文，并显示错误和重试入口。

### P2-5：上传前置校验不足，服务端读取整个文件到内存

**位置：**

- `ad_vista_agent/web/static/index.html:47`
- `ad_vista_agent/web/app.py:262-295`
- `configs/default.yaml:84-88`

**问题表现：**

前端允许任意 `video/*`，不显示支持格式和 1 GiB 限制。服务端会先把完整 multipart 请求读入内存，再复制视频内容。

**用户影响：**

用户可能上传完成后才发现格式不支持；大文件会造成显著内存峰值，严重时影响全部任务。

**建议：**

- 前端明确列出支持格式并立即校验大小。
- 使用流式 multipart parser，边读边写临时文件。
- 校验磁盘空间并清理中断上传。

### P2-6：无障碍和状态表达不足

**位置：**

- `ad_vista_agent/web/static/index.html:23-49`
- `ad_vista_agent/web/static/styles.css:1`

**问题表现：**

- 多个图标按钮缺少 `aria-label`。
- 动态任务和错误状态没有 `aria-live`。
- 标签页没有标准 tab 语义。
- 服务离线时状态圆点仍保持绿色。
- 浏览器控制台出现 `favicon.ico` 404，虽不影响功能，但反映页面完成度不足。

**建议：**

- 完善 aria、焦点样式和标签页语义。
- 服务状态同步切换颜色及文本。
- 增加 favicon，保持控制台干净。

## 3. 可执行优化方案

### 第一阶段：修复正确性和执行契约

1. 分离资产、执行和会话身份。
2. 修复多交付物 Planner。
3. 修复人工确认状态机。
4. 将 goal、deliverables、mode 传入下游工具。
5. 将配置、Prompt、代码和模型版本纳入执行指纹。

**预期提升：** 用户请求与最终结果稳定对应，重复分析、组合请求和人工复核行为可预期。

### 第二阶段：修复证据可信度

1. 删除无依据的自动补引用。
2. 统一流式与非流式答案验证。
3. 增加 claim-to-evidence 支持校验。
4. 在前端展示证据卡片和可定位时间码。

**预期提升：** 用户能够判断结论为什么成立，避免“有引用 ID 就等于有证据”的错误信任。

### 第三阶段：重做任务状态和异步交互

1. 统一任务状态字段。
2. 展示真实阶段、失败原因和待确认原因。
3. 增加取消、重试、超时和后台运行。
4. 避免异步任务完成后抢占当前上下文。
5. 为流式接口定义稳定的 error/done 协议。

**预期提升：** 长时间分析过程中用户不会卡住，也能明确知道任务当前处于什么状态、下一步该做什么。

### 第四阶段：完善 Web 使用体验

1. 增加移动端导航抽屉。
2. 时间轴展示关键帧、摘要和可读时间码。
3. 修复 IME 输入、加载态、空状态和局部错误。
4. 上传前进行格式和大小校验。
5. 完善无障碍和服务状态反馈。

**预期提升：** 降低理解成本和操作摩擦，使 Web 从内部调试界面提升为可交付产品界面。

## 4. 推荐的优化顺序

1. 修复同视频旧结果复用和会话污染。
2. 修复 Planner 多交付物和人工确认状态机。
3. 让用户 goal、deep 和 deliverables 真正影响执行。
4. 删除事后补引用并统一回答验证。
5. 前端展示证据和真实任务状态。
6. 完善取消、重试、超时和流式错误处理。
7. 修复移动端导航和时间轴展示。
8. 建立覆盖真实用户路径的 Agent E2E 测试集。

建议新增的关键测试场景：

- 同一视频连续提交两个不同目标。
- 同一视频并发提交两个任务。
- 所有 deliverable 组合。
- report review 后计划仍包含 creative。
- 模型输出无引用的 grounded/visual 回答。
- 流式输出最终验证失败。
- 历史任务处于 failed、running、waiting_confirmation。
- 移动端新对话和任务切换。
- 后端存在 citations 时前端必须展示证据。

## 5. 结论

如果只做 3 件事，最值得优先处理：

1. **分离视频资产与 Agent 执行会话，禁止不同请求复用旧结果。**
2. **修复 Planner 和人工确认状态机，确保请求完整执行且风险流程不可绕过。**
3. **把证据引用和真实任务状态交付到前端，并统一流式答案验证。**

这三项完成后，AdVista 的提升不会只是“少几个 Bug”，而是会从一个能力丰富但行为不稳定的内部原型，转变为一个用户能够理解、信任和持续使用的 Agent 产品。

## 验证记录

- 安装并验证浏览器：Google Chrome `151.0.7922.137`。
- 成功启动本地 Web：`http://127.0.0.1:8080`。
- 桌面端真实页面验证：完成。
- 移动端 390x844 视口验证：完成。
- JavaScript 语法检查：通过。
- Python 单元测试：82 项全部通过。
- 最小复现：多交付物 Planner 失败、unknown 引用升级、任务状态丢失均已确认。
- 现有测试未覆盖上述核心产品级风险。
