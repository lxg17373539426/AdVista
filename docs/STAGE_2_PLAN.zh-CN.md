# 第二阶段规划：视频时间轴、镜头切分与关键帧

> 实施状态：已完成。最终实现与验收记录见 [`STAGE_2.md`](STAGE_2.md)。

## 1. 阶段目标

第二阶段只解决一个问题：

> 将第一阶段登记的 `AdAsset` 转换为稳定、可复现、可缓存的视频镜头时间轴，并为每个镜头生成具有精确时间定位的代表性关键帧。

本阶段完成后，系统仍然不理解广告卖点，也不生成营销结论。它只建立后续 ASR、OCR、视觉理解和证据追踪共同依赖的视觉时间轴底座。

## 2. 输入与输出边界

### 2.1 输入

本阶段只接受第一阶段的标准产物：

- `asset.json`；
- `manifest.json`；
- `asset.json` 中指向的原始视频；
- 第二阶段镜头检测和关键帧配置。

不允许绕过 `AdAsset` 直接按比赛视频 ID 工作。

### 2.2 输出

每个视频生成：

```text
outputs/runs/<run_id>/
├── asset.json
├── manifest.json
├── timeline/
│   ├── shots.json
│   ├── keyframes.jsonl
│   └── frames/
│       ├── shot_0001_primary.jpg
│       ├── shot_0002_primary.jpg
│       └── ...
└── stage_2_metrics.json
```

其中：

- `shots.json`：完整镜头边界和检测信息；
- `keyframes.jsonl`：关键帧索引、时间戳、图片路径和选择原因；
- `frames/`：实际图片文件；
- `stage_2_metrics.json`：耗时、帧数、镜头数、缓存等指标；
- `manifest.json`：追加第二阶段工具、配置和产物记录。

## 3. 处理链路

```text
Stage 1 AdAsset
  ↓
校验源文件 SHA-256 和媒体元数据
  ↓
建立统一毫秒时间轴
  ↓
PySceneDetect 检测镜头边界
  ↓
镜头边界归一化与合法性校验
  ↓
为每个镜头计算关键帧候选时间
  ↓
FFmpeg 精确抽取代表帧
  ↓
校验图片、时间戳和镜头覆盖关系
  ↓
写入 shots.json + keyframes.jsonl + frames/
  ↓
重复执行时按配置和版本命中缓存
```

## 4. 本阶段包含的功能

### 4.1 统一视频时间轴

内部时间单位统一使用整数毫秒：

- `start_ms`：包含镜头开始位置；
- `end_ms`：镜头结束位置；
- `timestamp_ms`：关键帧对应位置；
- 时间范围必须满足 `0 <= start_ms < end_ms <= asset.duration_ms`；
- 禁止业务层混用秒、帧序号和毫秒。

帧序号仅作为辅助元数据保存，不能作为跨工具的主时间坐标。

### 4.2 镜头切分

首选工具：PySceneDetect。

第一版使用内容变化检测，配置至少包括：

- 检测算法；
- 场景变化阈值；
- 最短镜头时长；
- 是否启用淡入淡出检测；
- 工具版本。

镜头切分必须处理：

- 正常硬切；
- 视频全程无明显切换；
- 极短快速切镜；
- 首尾不足一个完整采样间隔；
- 检测器未返回任何边界。

如果检测器没有返回镜头，必须回退为覆盖整个视频的单镜头，不能输出空时间轴。

### 4.3 边界归一化

对原始检测结果执行确定性清洗：

- 第一个镜头从 `0 ms` 开始；
- 最后一个镜头结束于视频总时长；
- 镜头按时间升序排列；
- 镜头之间不允许重叠；
- 小于最短时长的镜头按明确规则合并；
- 相邻镜头之间不应存在未解释的时间空洞；
- 所有修正记录到元数据中。

### 4.4 关键帧候选

MVP 每个镜头只生成一张主关键帧，默认选择镜头中点附近的可解码帧。

选择策略：

1. 首选镜头中点；
2. 如果中点解码失败，在镜头范围内前后搜索；
3. 避开镜头边界附近可能存在的过渡帧；
4. 极短镜头选择可稳定解码的中心位置；
5. 保存选择原因 `selection_reason`。

本阶段不实现 OCR 密集帧、商品出现帧、运动峰值帧等语义采样。这些属于后续多模态感知阶段。

### 4.5 关键帧抽取

使用 FFmpeg 抽取图片，要求：

- 默认输出 JPEG；
- 不拉伸原始宽高比；
- 可配置最大长边，MVP 默认不改变原始分辨率；
- 图片文件必须可读取且大小大于零；
- 保存图片 SHA-256；
- 记录 FFmpeg 版本与抽帧参数。

### 4.6 内容寻址缓存

第二阶段缓存键不能只使用视频哈希，应包含：

```text
asset.sha256
+ stage_name
+ scene_detector_name
+ scene_detector_version
+ normalized_scene_config
+ frame_extractor_version
+ normalized_frame_config
+ schema_version
```

配置或工具版本变化后必须重新执行，避免复用不兼容的镜头结果。

## 5. 明确不做的内容

第二阶段不实现：

- Faster-Whisper ASR；
- DeepSeek-OCR 或 PaddleOCR；
- Qwen3.5-9B 视频理解；
- Logo、商品、人物或目标检测；
- 音乐 BPM、节拍和情绪分析；
- 卖点、痛点、受众、Hook、Proof、CTA 分析；
- Agent Planner 和动态工具循环；
- 多轮问答；
- Web 界面；
- 营销报告生成；
- 批量 3108 条视频全量处理。

阶段验收只使用少量代表性视频，不在本阶段解决全量任务调度。

## 6. 数据结构规划

### 6.1 Shot

建议新增 `schemas/timeline.py`：

```json
{
  "shot_id": "shot_0001",
  "asset_id": "asset_xxx",
  "index": 0,
  "start_ms": 0,
  "end_ms": 1840,
  "duration_ms": 1840,
  "start_frame": 0,
  "end_frame": 45,
  "detector": "pyscenedetect_content",
  "detector_score": 31.2,
  "normalization_actions": []
}
```

约束：

- `shot_id` 在单个 Asset 内唯一；
- `index` 从 0 连续递增；
- `duration_ms == end_ms - start_ms`；
- 时间范围位于视频总时长内；
- 不允许相邻镜头重叠。

### 6.2 Keyframe

```json
{
  "keyframe_id": "kf_0001_primary",
  "asset_id": "asset_xxx",
  "shot_id": "shot_0001",
  "timestamp_ms": 920,
  "frame_number": 23,
  "role": "primary",
  "selection_reason": "shot_midpoint",
  "artifact_path": "timeline/frames/shot_0001_primary.jpg",
  "artifact_sha256": "...",
  "width": 720,
  "height": 1280
}
```

约束：

- `timestamp_ms` 必须落在所属镜头内；
- `artifact_path` 必须存在；
- 图片哈希必须符合 SHA-256；
- 同一镜头 MVP 只能有一个 `primary` 关键帧。

### 6.3 Timeline

```json
{
  "schema_version": "0.1",
  "asset_id": "asset_xxx",
  "duration_ms": 57049,
  "shots": [],
  "coverage": {
    "start_ms": 0,
    "end_ms": 57049,
    "gap_ms": 0,
    "overlap_ms": 0
  }
}
```

## 7. 工具模块规划

预计新增以下模块，但本轮不实现：

```text
ad_vista_agent/
├── schemas/
│   └── timeline.py
├── tools/
│   ├── scene_detect.py
│   └── frame_extract.py
├── timeline/
│   ├── builder.py
│   ├── normalize.py
│   └── validate.py
└── runtime/
    └── stage_cache.py
```

职责边界：

| 模块 | 职责 |
|---|---|
| `scene_detect.py` | 只调用检测器并返回原始边界 |
| `frame_extract.py` | 只按指定时间戳抽取图片 |
| `normalize.py` | 修正、合并和覆盖时间边界 |
| `validate.py` | 检查时间轴连续性和关键帧归属 |
| `builder.py` | 编排第二阶段，不包含模型逻辑 |
| `stage_cache.py` | 根据视频、配置和版本计算缓存键 |

## 8. CLI 规划

新增命令建议为：

```bash
advista-agent timeline <video-path>
```

它应复用或自动完成第一阶段 ingest，但内部仍保持阶段边界：

```text
ingest cache
  ↓
timeline cache
```

可选参数：

```text
--config
--scene-threshold
--min-shot-ms
--force
```

本阶段不加入模糊的 `analyze` 总入口，避免尚未存在的 Agent 能力与固定 Pipeline 混淆。

## 9. 测试规划

### 9.1 单元测试

- Shot 时间范围合法；
- Shot duration 与边界一致；
- 相邻镜头不得重叠；
- 极短镜头按规则合并；
- 空检测结果回退为单镜头；
- 关键帧必须落在所属镜头内；
- 缓存键对相同配置稳定；
- 修改阈值后缓存键发生变化；
- 无效图片和错误哈希被拒绝。

### 9.2 集成测试

选择至少五类真实视频：

1. 常规多镜头广告；
2. 单镜头口播广告；
3. 高频快速切镜广告；
4. 带淡入淡出的视频；
5. 竖屏且包含音轨的视频。

验证：

- 总时间轴覆盖完整视频；
- 没有负时间、重叠或越界；
- 每个镜头存在主关键帧；
- 所有图片可解码；
- 第二次执行命中缓存；
- 修改检测阈值后不命中旧缓存。

## 10. 验收标准

第二阶段完成必须同时满足：

- 任意已支持视频可生成镜头时间轴；
- 无切镜视频回退为一个完整镜头；
- 时间轴覆盖从 0 到视频总时长；
- `gap_ms == 0`；
- `overlap_ms == 0`；
- 每个镜头至少有一张主关键帧；
- 关键帧时间戳位于镜头范围内；
- 图片可读取并具有 SHA-256；
- 产物通过 Pydantic Schema 校验；
- 同配置重复执行命中缓存；
- 配置或版本变化触发重新计算；
- 所有单元测试和五类真实视频集成测试通过。

## 11. 性能记录

本阶段不做复杂性能优化，但必须记录基线：

- 视频时长；
- 镜头检测耗时；
- 抽帧耗时；
- 总耗时；
- 检测镜头数；
- 输出图片总大小；
- 峰值内存可选；
- 是否命中缓存。

本阶段不使用 GPU，H20 留给后续 ASR、OCR 和多模态模型。镜头切分和抽帧先使用 CPU/FFmpeg，确保行为稳定、可复现。

## 12. 实施顺序

第二阶段执行时按以下顺序推进，每一步通过后再进行下一步：

1. 定义 Shot、Keyframe、Timeline Schema；
2. 实现并测试边界归一化；
3. 封装 PySceneDetect 原始检测工具；
4. 封装 FFmpeg 单帧抽取工具；
5. 实现 Timeline Builder；
6. 实现配置和版本相关缓存键；
7. 增加 `timeline` CLI；
8. 完成单元测试；
9. 使用五类真实视频进行集成测试；
10. 记录基线指标并封板。

## 13. 下一阶段接口承诺

第三阶段的 ASR 和 OCR 只能消费：

- `AdAsset`；
- `Timeline`；
- `Shot`；
- `Keyframe`。

ASR 使用统一毫秒时间轴写入 Speech Evidence；OCR 优先消费第二阶段关键帧并写入带区域的 OCR Evidence。第二阶段不为任何具体模型写特殊分支，从而保持工具可替换性。
