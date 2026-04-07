# Museum Agent API 设计方案

## 背景

将 `museum_agent.run` 封装为 API，提供攻略生成和问答的 chatbot 服务。核心挑战：如何在多轮对话中高效传递上下文。

## 方案选型：混合方案（方案 C）

### 为什么不用纯 messages 数组（方案 A）

我们的"上下文"包含两种性质不同的数据：

| 数据 | 体量 | 特点 |
|------|------|------|
| 4 路检索结果 | ~32KB | 生产成本高（4 次 LightRAG 查询），同一次参观内不变 |
| plan JSON | ~2KB | 追问时经常引用 |
| plan_text 自然语言攻略 | ~3KB | 追问时 LLM 需要看到才能连贯回答 |
| UserIntent | ~0.2KB | 时间预算、受众等 |
| 对话历史 | 小，线性增长 | 只需最近几轮 |

如果走纯 messages 数组，这 ~40KB 的产物要么每次来回传输（浪费带宽和 token），要么丢掉（追问就断了上下文）。

### 方案 C：服务端缓存贵的产物，客户端维护轻的对话历史

- **服务端缓存**：retrieval 结果、plan、plan_text、intent — 大、贵、稳定
- **客户端携带**：最近 2-3 轮对话 — 小、轻、线性增长

## API 接口设计

```
POST /chat
{
  "session_id": "可选，首次为空",
  "message": "用户输入",
  "recent_history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}

Response:
{
  "session_id": "uuid",
  "answer": "回答文本",
  "plan": { ... }  // 仅 tour 模式首次/重新规划时返回
}
```

## 服务端处理流程

```
1. 有 session_id → 加载缓存 (plan, retrieval, intent)
2. 判断请求类型:
   - 无 session / 新规划请求 → 完整 tour 或 QA 流程
   - 调整类 → re-plan (新 intent + 缓存 retrieval)
   - 追问类 → QA (轻量检索 + 缓存 plan_text + recent_history)
3. 更新 session 缓存
4. 返回 { session_id, answer, plan?(如有) }
```

### 请求类型判断

类似 `detect_mode` 的逻辑，通过关键词正则或轻量 LLM 分类：

- **追问类**："青铜器展厅在几楼" → QA 流程（轻量检索 + 缓存 plan）
- **调整类**："时间不够了帮我调整" → Re-plan 流程（新 intent + 缓存 retrieval）
- **新规划类**："帮我重新规划一个2小时的" → 完整 tour 流程

## 追问时的检索策略

**每次追问都做一次 QA 单路检索 + 叠加缓存的 plan 上下文**，不做"是否需要检索"的判断。

```
追问 prompt = cached plan_text（攻略上下文）
             + 新的 QA 检索结果（针对追问内容）
             + 最近 2-3 轮对话
             + 用户新问题
```

理由：
- 问攻略内已有内容 → plan_text 里有，QA 检索可能补充，LLM 能回答
- 问攻略外内容 → QA 检索会检索到新信息，两者互补
- 代价仅是一次 QA 单路检索（`top_k=40, chunk_top_k=15`），比 tour 的 4 路并行轻量得多
- 不需要复杂的"判断是否检索"逻辑，简单可靠

## 重新规划能力

用户逛到一半说"剩下时间不够了，帮我调整"时：

```
服务端处理:
  ├─ 重新解析 intent（新的 time_budget, start_time=now, 排除已逛区域）
  ├─ 跳过检索（复用 session 中缓存的 4 路检索结果）
  └─ 重新走 planner（用新 intent + 旧 retrieval）
```

复用缓存的检索结果，只重新走 intent 解析 + planner，节省检索开销。

## Session 管理

### 存储

- 当前单实例部署：内存 dict 即可
- 多实例扩展时：迁移到 Redis

### 容量估算（万级 DAU）

```
每个 session ≈ 40KB
峰值并发（博物馆 9:00-16:00）≈ 500-1000 人
内存占用 ≈ 1000 × 40KB = 40MB
```

session 存储不是瓶颈。

### 过期策略

按天过期，博物馆场景天然以"一天的参观"为边界。

## 扩展性考虑

万级 DAU 下真正的瓶颈不在 session，而在：

| 瓶颈 | 原因 | 应对 |
|------|------|------|
| LLM API 调用 | tour 模式一次 3 次 LLM 调用 | 相似意图的 plan 做缓存 |
| LightRAG 查询 | 4 路并行查 PG + 向量检索 | 连接池、读副本 |
| 并发规划请求 | 高峰期多人同时生成攻略 | 队列 + 预生成热门方案 |

优化手段：每天早上预生成高频模板（"3小时通用游"、"亲子半日游"、"青铜器专题"），命中时直接返回，可消化 60-70% 的首次请求。
