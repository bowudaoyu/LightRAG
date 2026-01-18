# Bubble 接口设计逻辑说明

本文描述 LightRAG 中 Bubble 相关接口的设计逻辑、提示词命中逻辑与兜底策略，面向维护者与联调人员。

## 覆盖范围
- 接口：`POST /bubble`、`POST /bubble/detail`
- 代码位置：`lightrag/api/routers/query_routes.py`
- 话题池与提示词：`lightrag/prompt.py`

## 核心目标
- 为文物生成可点击的“气泡标题”（title），用户点击后再生成详情（detail）。
- 降低一次性 LLM 成本：默认只生成标题，详情按需生成。
- 结合知识库上下文提升准确性，无上下文时用 LLM 通用知识兜底。

## 数据与字段

### /bubble
- 输入关键字段：
  - `query`: 文物名称
  - `artifact_type`: 文物类型，可不传
  - `mode`/`top_k`: 知识库检索参数
  - `include_detail`: 是否一次性返回 detail（默认 false）
  - `bubble_count`: 气泡数量（默认 3，最大 10）
  - `include_references`: 是否返回参考来源
- 输出核心字段：
  - `artifact_name`、`artifact_type`、`bubbles`、`references`

### /bubble/detail
- 输入关键字段：
  - `artifact_name`、`artifact_type`、`topic_type`、`bubble_title`
  - `mode`/`top_k`
- 输出核心字段：
  - `detail`（50-100 字文本）

## 处理流程（/bubble）

### 1) 文物类型推断
- 若请求未传 `artifact_type`，从 `query` 名称关键词粗匹配：
  - 兵器类：`剑/刀/戟/矛/弓/弩/戈`
  - 瓷器类：`瓶/碗/盘/壶/罐/杯/盏/窑`
  - 青铜器类：`鼎/簋/尊/彝/觥/铜`
  - 书画类：`画/帖/卷/图/书法`
  - 玉器类：`玉/璧/琮/佩`
  - 金银器类：`金/银`
  - 佛像类：`佛/菩萨/罗汉/观音`
  - 印章类：`印/玺`
  - 漆器类：`漆`
  - 钱币类：`钱/币/通宝`
  - 乐器类：`琴/瑟/笛/箫/钟/磬`
- 未命中则使用 `default`，最终输出时会回写为“通用文物”。

### 2) 话题选择（命中逻辑）
- 话题池定义在 `lightrag/prompt.py`：`BUBBLE_TOPICS`（含 `type/emoji/weight/desc`）。
- 文物类型对应规则：`ARTIFACT_TOPIC_RULES`（`must`/`prefer`/`avoid`）。
- 当前配置清单见下文“话题池配置”和“类型规则配置”。 
- 选择顺序与细节（来自 `select_bubble_topics`）：\n+
  1. **规则取值**\n+
     - 使用 `artifact_type` 查 `ARTIFACT_TOPIC_RULES`，未命中则落到 `default`。\n+
  2. **必选阶段（must）**\n+
     - 遍历 `rules.must`（保持顺序）。\n+
     - 条件：`len(selected) < num_topics` 且 `topic_type` 存在于 `BUBBLE_TOPICS`。\n+
     - 命中后追加 `{type, emoji, desc}`，并加入 `used_types`。\n+
  3. **优选阶段（prefer + weight）**\n+
     - 构建 `avoid_set = set(rules.avoid)`。\n+
     - `prefer_pool = [t for t in rules.prefer if t not in used_types and t not in avoid_set]`。\n+
     - while `len(selected) < num_topics` 且 `prefer_pool` 非空：\n+
       - `weights = [BUBBLE_TOPICS[t]["weight"] for t in prefer_pool]`。\n+
       - 用 `random.choices(prefer_pool, weights=weights, k=1)` 选中 `chosen`。\n+
       - 追加对应 `type/emoji/desc`，加入 `used_types`。\n+
       - 从 `prefer_pool` 移除 `chosen`（避免重复）。\n+
  4. **兜底阶段（全量池补齐）**\n+
     - 若仍不足：\n+
       - `all_topics = [t for t in BUBBLE_TOPICS if t not in used_types and t not in avoid_set]`。\n+
       - `remaining_count = min(num_topics - len(selected), len(all_topics))`。\n+
       - `random.sample(all_topics, remaining_count)` 随机补齐。\n+
  5. **返回**\n+
     - 返回 `selected`，不会包含 `avoid`，也不会重复。\n+
- 选出的话题会格式化成 `topic_pool`，写入提示词，要求 LLM 必须按这些方向生成。

## 话题池配置（BUBBLE_TOPICS）

字段说明：`type` 话题类型，`emoji` 表情符号，`weight` 权重，`desc` 说明。

| type | emoji | weight | desc |
| --- | --- | --- | --- |
| 值多少钱 | 💰 | 0.9 | 拍卖价格、估值、收藏价值 |
| 谁用过它 | 👑 | 0.8 | 历史主人、使用者、收藏家 |
| 现代等价物 | 🔄 | 0.85 | 类比现代物品，帮助理解用途 |
| 鉴定秘籍 | 🕵️ | 0.7 | 如何辨别真假、看哪些细节 |
| 震惊冷知识 | 😱 | 0.9 | 意想不到的事实、反常识 |
| 命运多舛 | 💔 | 0.75 | 被盗、流失、回归的故事 |
| 名人八卦 | 🎭 | 0.85 | 与名人相关的趣事、传闻 |
| 黑科技 | 🔬 | 0.8 | 制作工艺之谜、超前技术 |
| 数字震撼 | 📏 | 0.7 | 惊人的数量、尺寸、年代 |
| 世界之最 | 🌍 | 0.8 | 最早、唯一、存世最少 |
| 审美吐槽 | 🎨 | 0.75 | 有趣的审美评价、风格点评 |
| 实战能力 | ⚔️ | 0.8 | 兵器的杀伤力、实用性 |
| 灵异传说 | 👻 | 0.6 | 民间传说、神秘故事 |
| 原来这么用 | 🍽️ | 0.7 | 容易误解的真实用途 |
| 拍照攻略 | 📷 | 0.5 | 最佳拍摄角度和细节 |

## 类型规则配置（ARTIFACT_TOPIC_RULES）

说明：每个类型包含 `must`（必选）、`prefer`（优选）、`avoid`（避免）三类话题。

### 瓷器
- must: 鉴定秘籍
- prefer: 值多少钱、审美吐槽、名人八卦、现代等价物、数字震撼
- avoid: 实战能力、灵异传说

### 青铜器
- must: 黑科技
- prefer: 震惊冷知识、谁用过它、现代等价物、数字震撼
- avoid: 审美吐槽、鉴定秘籍

### 书画
- must: 名人八卦
- prefer: 值多少钱、命运多舛、世界之最、震惊冷知识
- avoid: 实战能力、现代等价物、鉴定秘籍

### 玉器
- must: 鉴定秘籍
- prefer: 谁用过它、灵异传说、值多少钱、名人八卦
- avoid: 实战能力、审美吐槽

### 兵器
- must: 实战能力
- prefer: 谁用过它、黑科技、震惊冷知识、名人八卦
- avoid: 审美吐槽、鉴定秘籍

### 金银器
- must: 值多少钱
- prefer: 谁用过它、名人八卦、黑科技、数字震撼
- avoid: 灵异传说、实战能力

### 织物
- must: 数字震撼
- prefer: 黑科技、谁用过它、命运多舛、名人八卦
- avoid: 实战能力、鉴定秘籍

### 漆器
- must: 黑科技
- prefer: 现代等价物、名人八卦、数字震撼、审美吐槽
- avoid: 实战能力、灵异传说

### 陶器
- must: 原来这么用
- prefer: 现代等价物、震惊冷知识、数字震撼、世界之最
- avoid: 实战能力、名人八卦

### 佛像
- must: 灵异传说
- prefer: 名人八卦、黑科技、数字震撼、命运多舛
- avoid: 实战能力、审美吐槽

### 钱币
- must: 值多少钱
- prefer: 震惊冷知识、世界之最、现代等价物、数字震撼
- avoid: 实战能力、灵异传说

### 印章
- must: 谁用过它
- prefer: 名人八卦、值多少钱、命运多舛、震惊冷知识
- avoid: 实战能力、现代等价物

### 家具
- must: 现代等价物
- prefer: 值多少钱、名人八卦、黑科技、审美吐槽
- avoid: 实战能力、灵异传说

### 乐器
- must: 原来这么用
- prefer: 名人八卦、黑科技、震惊冷知识、世界之最
- avoid: 实战能力、鉴定秘籍

### default
- must: 无
- prefer: 值多少钱、震惊冷知识、名人八卦、现代等价物、世界之最
- avoid: 无

### 3) 知识库上下文检索
- 调用 `rag.aquery_llm` 仅取上下文（`only_need_context=True`）。
- 若检索失败或无上下文：
  - `context_data` 置为“知识库中暂无相关信息...”
  - `references` 置空
- 若检索成功：
  - 取 `llm_response.content` 作为上下文
  - 允许返回 `references`（受 `include_references` 控制）

### 4) 提示词命中逻辑（关键）
- 通过 `include_detail` 选择提示词：
  - `include_detail=false` → `PROMPTS["artifact_bubble_response"]`
  - `include_detail=true` → `PROMPTS["artifact_bubble_response_with_detail"]`
- 两者共同特点：
  - 强制 JSON 输出
  - `bubbles` 数量必须等于 `bubble_count`
  - 强制使用话题池 `topic_pool`
- 主要差异：
  - `artifact_bubble_response` 只生成 `title`
  - `artifact_bubble_response_with_detail` 同时生成 `title + detail`

### 5) LLM 调用与结果解析
- 使用 `rag.llm_model_func` 调用，设置 `_priority=5`。
- 解析逻辑：
  - 优先匹配 ```json ... ``` 块
  - 否则尝试截取首个 `{` 至最后一个 `}` 解析 JSON
- 解析失败兜底：
  - 直接用所选话题构造标题 `关于{query}的{topic}`
  - 不返回 `detail`

### 6) 返回封装
- 返回 `bubble_count` 条数据（超出则截断）。
- 当 `include_detail=false` 时强制 `detail=None`。
- `artifact_type=default` 最终返回“通用文物”。

## 处理流程（/bubble/detail）

### 1) 文物类型
- `artifact_type` 为空时默认“通用文物”。

### 2) 知识库上下文检索
- 同 `/bubble`：调用 `rag.aquery_llm` 获取 `context_data`。
- 无上下文时使用通用知识提示语兜底。

### 3) 提示词命中逻辑
- 固定使用 `PROMPTS["artifact_bubble_detail"]`。
- 输入包含：文物名称、文物类型、话题类型、气泡标题、上下文。
- 输出要求：直接输出 50-100 字文本（非 JSON）。

### 4) 返回
- 返回 `detail`，不做结构化解析。

## 设计要点与兜底策略
- **成本控制**：默认只生成标题，详情按需生成。
- **可控话题方向**：话题池 + 规则保证风格一致且可配置。
- **可观测性**：日志记录文物类型、话题选择、是否走 KB。
- **鲁棒解析**：JSON 解析失败时用话题池直接兜底生成标题。
- **知识库优先**：有上下文则优先使用 KB，无上下文时用通用知识。

## 关联文件
- `docs/bubble_api.md`
- `lightrag/api/routers/query_routes.py`
- `lightrag/prompt.py`
