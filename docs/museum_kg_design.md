# 博物馆知识图谱设计

## 一、静态图谱（骨架层）

### 节点类型

| 节点类型 | 数量 | 说明 |
|---------|:----:|------|
| Museum | 1 | 博物馆根节点 |
| Floor | 4 | 楼层（B1/F1/F2/F3） |
| Exhibition_Hall | 8 | 展厅（北区/南区） |
| Facility | 5 | 设施（食堂/咖啡厅/文创店/服务台/寄存处） |
| Artifact | 30 | 文物 |
| Concept | 11 | 文物分类 |
| Exhibition | 8 | 展览（含 type: permanent/temporary） |
| Theme | 7 | 跨区域主题线索 |
| Route | 3 | 推荐路线模板 |

### 边类型

| 边 | 方向 | 说明 |
|---|------|------|
| BELONGS_TO | Floor→Museum | 楼层从属博物馆 |
| LOCATED_ON | Zone→Floor | 展区位于哪一层 |
| DISPLAYED_IN | Artifact→Zone | 文物陈列于哪个展区 |
| HAS_CATEGORY | Artifact→Concept | 文物的分类归属 |
| ADJACENT_TO | Zone↔Zone(水平) | 同层相邻，带步行分钟数权重 |
| ACCESSIBLE_BY_STAIRS | Zone↔Zone(垂直) | 楼梯/电梯连通，带分钟数权重 |
| HOSTED_IN | Exhibition→Zone | 展览在哪个展厅 |
| INCLUDES | Exhibition→Artifact | 展览包含哪些文物 |
| HAS_THEME | Artifact/Exhibition→Theme | 主题归属 |
| RELATED_TO | Artifact↔Artifact | 历史/艺术关联 |
| ROUTE_STOP | Route→Zone/Artifact | 路线途经点，带 stop_order 和 duration_min |

### 向量化 Chunk 类型

| 来源 | 数量 | 内容示例 |
|------|:----:|---------|
| Artifact 描述 | 30 | "后母戊鼎：商代晚期青铜器…镇国之宝" |
| Exhibition 描述 | 8 | "展览「古代中国」：以考古发现…参观提示：建议40-60分钟" |
| Theme 叙事 | 7 | "主题「商代青铜文明」：商代是青铜时代巅峰…文物分布在B1北区和F2南区" |
| Route 描述 | 3 | "推荐路线「2小时精华」：第1站B1北区（35分钟）…" |
| Artifact 关联 | 9 | "后母戊鼎与四羊方尊的关联：同为商代晚期…" |

---

## 二、动态图谱（情报层）

### 节点类型（3 类）

按**对用户行为的影响方式**划分，而非按数据来源分类：

| 节点类型 | 覆盖的 JSON category | 对路径规划的影响 |
|---------|---------------------|----------------|
| **Event** | activity, exhibition_update(opening) | **吸引** → 引导用户前往 |
| **Notice** | operation, ticket, exhibition_update(closure/closing_soon/extended) | **警告** → 引导用户避开/调整 |
| **Story** | content, merchandise, service | **丰富** → 兴趣匹配推送 |

#### Event (活动事件)
- 有明确发生时间，用户可以"参加"的事
- 覆盖: guided_tour, workshop, lecture, performance, immersive, stamp_rally, exhibition opening
- 关键属性: valid_from, valid_to, recurrence
- 过期策略: valid_to 之后可清理

#### Notice (运营公告)
- 影响用户决策或动线的状态变更/警告
- 覆盖: closure, hours_change, crowd_alert, queue_status, facility_status, ticket availability, closing_soon, extended, hall_closure, artifact_rotation
- 关键属性: severity(info/warning/critical), valid_from, valid_to
- 过期策略: 状态恢复后即清理，或 valid_to 过期后清理

#### Story (内容情报)
- 丰富体验的知识/商业/服务信息，用户可以"消费"但不强时效
- 覆盖: curator_insight, trivia, ugc, media_tie_in, research, new_release, restock, promotion, cafe_special, audio_guide, rental, photo_rule
- 关键属性: content_type/merch_type/service_type, source
- 过期策略: 内容类长期保留；商品类售罄/下架后清理

### 边类型（3 类）

| 边 | 方向 | 语义 | 场景作用 |
|---|------|------|---------|
| **HAPPENS_AT** | Event/Story→Zone | 事件/内容发生在/可获取于这个区域 | 正向吸引：可以引导用户前往 |
| **AFFECTS** | Notice→Zone/Facility/Exhibition | 公告影响了某个区域/设施/展览的正常状态 | 警告信号：可能需要避开或调整 |
| **ABOUT** | Event/Notice/Story→Artifact/Exhibition/Theme | 语义关联 | 兴趣匹配：用户对A感兴趣→推送关于A的动态 |

### 为什么不需要动态→动态的边？

两个动态节点的关联通过**共享的静态实体**隐式推导：

```
Event(青铜器讲座) —ABOUT→ Artifact(后母戊鼎) ←ABOUT— Event(志愿者讲解)
```

Agent 通过图遍历发现两个 Event 都 ABOUT 同一个 Artifact，无需显式维护动态-动态边。

### category → node_type 映射规则

```python
def resolve_node_type(item: dict) -> str:
    cat = item["category"]
    if cat == "activity":
        return "Event"
    if cat == "exhibition_update":
        sub = item["payload"]["update_type"]
        if sub == "opening":
            return "Event"
        return "Notice"
    if cat in ("ticket", "operation"):
        return "Notice"
    # content, merchandise, service
    return "Story"
```

### 动态 Chunk 生成模板

每个动态节点生成 1 个 chunk，前缀嵌入时间+地点+类型：

```
Event:  【2026-04-05 10:00/14:00/16:00｜B1北区｜活动·guided_tour】
        B1北区青铜器志愿者讲解：……
        相关文物：后母戊鼎、四羊方尊、大盂鼎
        标签：志愿者讲解、青铜器、免费

Notice: 【2026-04-05 15:00-16:00｜B1北区｜注意】
        B1北区临时关闭：因展品维护……
        影响展品：后母戊鼎、四羊方尊……

Story:  【2026-04-03发布｜B1北区｜curator_insight】
        策展人手记：为什么叫后母戊鼎……
        相关文物：后母戊鼎
        标签：命名争议、冷知识
```

### file_path 编码规则

用于检索后按类型/时间后过滤：

```
静态数据: museum:static:{type}         例: museum:static:artifact
动态数据: museum:dynamic:{node_type}:{date}  例: museum:dynamic:event:2026-04-05
```

### 边 description 和 keywords 设计

```python
# HAPPENS_AT 边
description = f"【活动】{title}于{time_str}在{zone_name}举行"
keywords = "活动,发生地,位置"

# AFFECTS 边
description = f"【通知·{severity}】{title}影响{zone_name}"
keywords = "影响,通知,状态"

# ABOUT 边 → Artifact
description = f"{title}涉及文物{artifact_name}"
keywords = "相关文物,涉及"

# ABOUT 边 → Exhibition
description = f"{title}关联展览{exhibition_name}"
keywords = "相关展览,配套"

# ABOUT 边 → Theme
description = f"{title}与主题「{theme_name}」相关"
keywords = "相关主题"
```

### 边权重设计

```python
weight = priority / 5.0  # priority 1-5 → weight 0.2-1.0
```

---

## 三、端到端检索逻辑

### 场景: 大众游客3小时逛馆攻略

Agent 收到"我有3小时，帮我规划"后，发起 4 轮检索：

| 轮次 | 查询意图 | 命中节点 | 关键边 |
|:----:|---------|---------|--------|
| R1 路线骨架 | "3小时精华路线 首次 必看国宝" | Route, Artifact, Exhibition, Zone | ROUTE_STOP, DISPLAYED_IN, ADJACENT_TO, HOSTED_IN |
| R2 事件叠加 | "今天的活动 讲解 导览" | Event | HAPPENS_AT→Zone, ABOUT→Artifact |
| R3 风险规避 | "今天闭馆 关闭 维修 限流" | Notice | AFFECTS→Zone/Exhibition |
| R4 体验丰富 | "咖啡 文创 冷知识 打卡" | Story | HAPPENS_AT→Zone, ABOUT→Artifact/Theme |

### "活"的关键：4 轮数据交叉编织

- R1 给出骨架 → "9:05-9:50 B1北区看后母戊鼎"
- R2 叠加事件 → "9:30有讲解，刚好能蹭上！"
- R3 规避风险 → "15:00关闭，但你9点去没问题"
- R4 丰富体验 → "冷知识：它以前叫司母戊鼎" + "咖啡厅有文物拉花"

### 检索与 Agent 的责任边界

| LightRAG 知识层 | Agent 编排层 |
|----------------|-------------|
| 存储静态+动态图谱 | 接收用户意图 |
| 向量检索+图遍历 | 发起4轮检索 |
| 返回候选数据 | 后过滤（时效/受众/已推过） |
| 空间邻域查询 | 时间预算分配 |
| | 路线优化（避免回头路） |
| | 动态事件插入 |
| | 风险规避调整 |
| | 自然语言生成 |
