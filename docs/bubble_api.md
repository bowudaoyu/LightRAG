# /bubble 接口规范

本分支包含两个 Bubble 相关接口，定义在 `lightrag/api/routers/query_routes.py`。

## 1) POST /bubble
根据文物名称生成气泡标题，可选返回详情与参考来源。

### 请求体
字段 | 类型 | 必填 | 说明
--- | --- | --- | ---
query | string | 是 | 文物名称（min_length=1）
artifact_type | string | 否 | 文物类型（如瓷器、青铜器、书画等）。不提供时会根据名称关键词自动推断
mode | string | 否 | 知识库检索模式，默认 `mix`，可选 `local`/`global`/`hybrid`/`naive`/`mix`
top_k | integer | 否 | 检索的实体/关系数量，默认 10，最小 1
include_references | boolean | 否 | 是否返回参考来源，默认 `true`
include_detail | boolean | 否 | 是否返回详情内容，默认 `false`（true 会增加响应耗时）
bubble_count | integer | 否 | 气泡数量，默认 3，最大 10

### 响应体
字段 | 类型 | 说明
--- | --- | ---
artifact_name | string | 文物名称
artifact_type | string | 文物类型（推断为 default 时会回写为 `通用文物`）
bubbles | array | 气泡列表（最多 `bubble_count` 个，默认 3）
references | array | 参考来源列表（当 include_references=true 时返回）

### BubbleItem
字段 | 类型 | 说明
--- | --- | ---
type | string | 话题类型
emoji | string | 表情符号
title | string | 气泡标题（10-15 字）
detail | string/null | 详情内容（仅 include_detail=true 时返回）

### 示例响应
```json
{
  "artifact_name": "越王勾践剑",
  "artifact_type": "兵器",
  "bubbles": [
    {
      "type": "实战能力",
      "emoji": "⚔️",
      "title": "削20层纸不卷刃",
      "detail": null
    },
    {
      "type": "黑科技",
      "emoji": "🔬",
      "title": "防锈配方至今成谜",
      "detail": null
    },
    {
      "type": "名人八卦",
      "emoji": "🎭",
      "title": "卧薪尝胆那位的剑",
      "detail": null
    }
  ],
  "references": [
    {"reference_id": "1", "file_path": "/docs/越王勾践剑.pdf"}
  ]
}
```

### 逻辑要点
- 若未提供 `artifact_type`，会基于名称关键词推断（如“剑/刀/戟”->“兵器”）。
- 使用 `mode/top_k` 从知识库检索上下文；若检索失败则使用 LLM 通用知识。
- `include_detail=false` 时返回标题；详情通过 `/bubble/detail` 按需获取。

## 2) POST /bubble/detail
用户点击气泡后获取对应详情（50-100 字）。

### 请求体
字段 | 类型 | 必填 | 说明
--- | --- | --- | ---
artifact_name | string | 是 | 文物名称（min_length=1）
artifact_type | string | 否 | 文物类型（如瓷器、青铜器、书画等）
topic_type | string | 是 | 话题类型（如“值多少钱”“黑科技”等）
bubble_title | string | 是 | 用户点击的气泡标题
mode | string | 否 | 知识库检索模式，默认 `mix`，可选 `local`/`global`/`hybrid`/`naive`/`mix`
top_k | integer | 否 | 检索的实体/关系数量，默认 10，最小 1

### 响应体
字段 | 类型 | 说明
--- | --- | ---
artifact_name | string | 文物名称
topic_type | string | 话题类型
bubble_title | string | 气泡标题
detail | string | 详情内容（50-100 字）

### 示例响应
```json
{
  "artifact_name": "越王勾践剑",
  "topic_type": "实战能力",
  "bubble_title": "削20层纸不卷刃",
  "detail": "1965年出土时，考古人员用它轻松划破20多层纸，2500年前的剑至今锋利无比。剑身的菱形暗纹不是装饰，是古人独创的复合金属工艺，让剑既硬又韧。"
}
```

### 逻辑要点
- 使用 `mode/top_k` 检索上下文，失败则用通用知识兜底。
- `artifact_type` 未传时默认 `通用文物`。

## 认证与依赖
两个接口均挂载了 `Depends(combined_auth)`，是否要求认证取决于环境配置与认证实现。

## 3) POST /bubble/detail/stream
气泡详情流式返回（NDJSON），用于实时展示详情内容生成过程。

### 请求体
字段 | 类型 | 必填 | 说明
--- | --- | --- | ---
artifact_name | string | 是 | 文物名称（min_length=1）
artifact_type | string | 否 | 文物类型（如瓷器、青铜器、书画等）
topic_type | string | 是 | 话题类型（如“值多少钱”“黑科技”等）
bubble_title | string | 是 | 用户点击的气泡标题
mode | string | 否 | 知识库检索模式，默认 `mix`，可选 `local`/`global`/`hybrid`/`naive`/`mix`
top_k | integer | 否 | 检索的实体/关系数量，默认 10，最小 1

### 响应格式（NDJSON）
每行一个 JSON 对象：
- 首行：包含 `artifact_name` / `topic_type` / `bubble_title`
- 后续：`{"detail": "..."}` 内容片段，或 `{"error": "..."}`

### 示例响应
```json
{"artifact_name":"越王勾践剑","topic_type":"实战能力","bubble_title":"削20层纸不卷刃"}
{"detail":"1965年出土时，考古人员用它轻松划破20多层纸，"}
{"detail":"剑身的菱形暗纹不是装饰，是古人独创的复合金属工艺。"}
```

### 逻辑要点
- 同 `/bubble/detail` 使用 `mode/top_k` 检索上下文，失败则用通用知识兜底。
- 返回 `Content-Type: application/x-ndjson`，适合逐行解析。
