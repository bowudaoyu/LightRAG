# Agent 编排层设计：并行检索 + 全局编排

> 本文档定义 Agent 编排层的架构设计，重点是检索层与编排层如何协作。

## 一、为什么不能串行检索

原方案中 Agent 做 4 轮串行检索（R1 骨架 → R2 事件 → R3 风险 → R4 丰富），存在两个根本问题：

### 问题 1：延迟不可接受

4 次串行 LightRAG 检索 + 最终 LLM 生成，总延迟约 `4R + P`。假设单次检索 2s、生成 3s，总计 ~11s。用户在手机上等 11 秒会失去耐心。

### 问题 2：骨架先行假设是错误的

原方案假设"先定骨架再叠加细节"，但动态事件可能**推翻骨架本身**。

**反例**：假设 F3 南区 9:00 有庞贝策展人导览，B1 北区 10:00 有青铜器讲解。

- 如果 R1 先定骨架 → "B1 起步逐层上升"
- R2 发现 F3 的 9:00 讲解 → 但骨架已经安排 B1 先行，来不及
- 最优路线其实是 **F3→…→B1**（从顶层往下走），但串行方案无法回溯修改骨架

**本质**：事件不是"叠加在骨架上的装饰"，而是**重塑骨架的硬约束**。

### 根因：检索与编排混为一体

4 轮 R1→R2→R3→R4 既是检索顺序又暗含编排顺序，但实际上：

- **检索之间没有数据依赖** —— "3小时精华路线"和"今天有什么活动"可以同时查
- **编排必须看到全貌才能决策** —— 路线方向、时间分配、站点取舍必须在所有候选数据到齐后一次性规划

---

## 二、正确的架构：两阶段分离

```
Phase 1: 并行检索（拿原材料）   →   Phase 2: 全局编排（做方案）
    LightRAG 负责                      Agent 负责
```

### Phase 1：并行检索（Parallel Retrieval）

4 个查询同时发出，互不依赖：

```
用户意图："我第一次来国博，有3小时，帮我规划"
   │
   │  Agent 解析意图后，同时发起 4 个查询：
   │
   ├──→ Q1: "3小时 首次 国宝 精华路线"
   │    filter: file_path LIKE 'museum:static:%'
   │    期望命中: Route, Artifact, Exhibition, Zone, Theme
   │
   ├──→ Q2: "2026-04-05 活动 讲解 导览 体验"
   │    filter: file_path LIKE 'museum:dynamic:event:%'
   │    期望命中: Event 节点 + HAPPENS_AT/ABOUT 边
   │
   ├──→ Q3: "2026-04-05 关闭 维修 限流 排队"
   │    filter: file_path LIKE 'museum:dynamic:notice:%'
   │    期望命中: Notice 节点 + AFFECTS/ABOUT 边
   │
   └──→ Q4: "咖啡 文创 冷知识 打卡 拍照"
        filter: file_path LIKE 'museum:dynamic:story:%'
        期望命中: Story 节点 + HAPPENS_AT/ABOUT 边

总延迟 = max(Q1, Q2, Q3, Q4) ≈ 单次检索延迟
```

**为什么可以并行**：4 个查询搜索的是不同语义空间（路线 vs 活动 vs 风险 vs 趣味），通过 `file_path` 前缀各自过滤，返回结果互不依赖。

### Phase 2：全局编排（Holistic Planning）

所有候选数据到齐后，Agent 做一次性的全局规划。

---

## 三、全局编排的详细设计

### 3.1 统一规划语言

Phase 1 返回的 4 类数据，在编排层转化为统一的规划角色：

| 数据类型 | 规划角色 | 特征 | 比喻 |
|---------|---------|------|------|
| Event（有时间窗口的活动） | **时间锚点** | 必须在特定时间到特定地点 | 日程表上的硬约会 |
| Notice（闭馆/维修/排队） | **负约束** | 某时某地不可达或代价高 | 日程表上的禁区 |
| Route + Zone + Artifact | **候选素材** | 可选的景点和路径模板 | 自助餐的菜品 |
| Story（冷知识/文创/咖啡） | **增味剂** | 不改变路线结构，锦上添花 | 菜品的调料 |

### 3.2 编排算法：4 步走

#### Step 1：提取锚点和约束

```python
# 从 Event 中筛出时间窗口落在用户可用时段内的
anchors = []
for event in events:
    if event.valid_from <= user_end and event.valid_to >= user_start:
        # 筛掉受众不匹配的（如亲子工作坊 vs 成人用户）
        if matches_audience(event, user_profile):
            anchors.append({
                "node": event,
                "zone": event → HAPPENS_AT → Zone,
                "time_windows": event.recurrence.times,  # 可能有多个场次
                "duration": event.duration_minutes,
                "priority": event.priority,
                "artifacts": event → ABOUT → Artifact[]  # 关联文物
            })

# 从 Notice 中筛出影响可达性的
constraints = []
for notice in notices:
    if notice.affects_accessibility:  # hall_closure, facility_status 等
        constraints.append({
            "node": notice,
            "affected_zones": notice → AFFECTS → Zone[],
            "time_range": (notice.valid_from, notice.valid_to),
            "severity": notice.severity
        })
    elif notice.is_entrance_info:  # queue_status
        entrance_hints.append(notice)

# 按 priority 排序，高优先级锚点优先保障
anchors.sort(key=lambda a: a["priority"], reverse=True)
```

#### Step 2：围绕锚点构建路线

核心思想：**不是"先定骨架再插事件"，而是"先排锚点再连路线"**。

```
输入:
  - anchors: 按 priority 排序的锚点列表（每个有 zone + time_windows）
  - constraints: 负约束列表（zone + time_range）
  - spatial_graph: Zone 邻接关系 + 移动时间（来自 ADJACENT_TO / ACCESSIBLE_BY_STAIRS 边）
  - route_templates: 静态 Route 节点（作为参考，不是硬性规定）
  - time_budget: 用户总可用时间

算法:
  1. 选定锚点场次
     - 对每个锚点，从其多个场次中选择一个（如志愿者讲解有 10:00/14:00/16:00 三场）
     - 选择标准：能容纳更多锚点的组合 + 空间移动最少
     - 这是一个小规模组合优化问题（锚点通常 < 5 个）

  2. 锚点排成时间线
     - 锚点按选定场次的时间排序 → 得到时间线上的"钉子"
     - 例: [F3@9:00(60min), B1@10:30(40min)]

  3. 检查约束冲突
     - 锚点的 (zone, time) 是否与 constraints 冲突
     - 如冲突 → 换场次或放弃低优先级锚点

  4. 确定路线方向
     - 根据锚点的空间分布决定路线走向
     - 例: 第一个锚点在 F3 → 路线从高层往低层走
     - 例: 第一个锚点在 B1 → 路线从低层往高层走
     - Route 模板提供每个 Zone 的建议停留时间和必看文物，但路线方向由锚点决定

  5. 填充锚点之间的空隙
     - 用 spatial_graph 找出两个锚点之间经过哪些 Zone
     - 从 route_template 中取这些 Zone 的建议文物和停留时间
     - 如果空隙时间不足以全部覆盖 → 按 Artifact priority 取舍
```

**用之前的反例验证**：

```
锚点: F3南区@9:00(60min, priority=4), B1北区@10:00(40min, priority=3)

Step 1: F3 只有 9:00 一场；B1 有 10:00/14:00/16:00 三场
Step 2: 如果选 F3@9:00 + B1@10:00 → 9:00+60min=10:00, F3到B1移动~5min → 10:05到B1, 迟到
        如果选 F3@9:00 + B1@14:00 → 可行，但中间空隙太大
        → 最佳方案: 放弃 F3@9:00（60min太长且 priority=4 但与 B1 冲突）
           或者: 用 B1@14:00 场次，上午先逛 F3→F2→F1，下午 14:00 回 B1
        → Agent 根据用户时间窗口（9:00-12:00 只有 3h）选择最优组合

Step 4: 假设选定 B1@10:00 → 路线从 B1 起步
        或选定 F3@9:00 + B1@14:00 → 路线从 F3 起步（但用户 12:00 要走，14:00 赶不上）
        → 最终: B1@10:00, 放弃 F3@9:00, 路线从 B1 起步逐层上升
```

#### Step 3：填充空隙

锚点之间的空闲时间，用静态 Artifact/Exhibition 填充：

```
对每段空隙 (gap_start, gap_end, from_zone, to_zone):
    1. 从 spatial_graph 找 from_zone → to_zone 的路径上经过的 Zone 列表
    2. 每个 Zone 从 route_template 取建议停留时间和必看 Artifact
    3. 如果总时间 > 空隙 → 按 Artifact 的主题相关性/popularity 取舍
    4. 如果总时间 < 空隙 → 考虑加入非路线模板中的 Zone（如 F2南区玉器）
```

#### Step 4：附着 Story

Story 不改变路线结构，只添加文案内容：

```
对每个 Story:
    1. 通过 ABOUT 边找到关联的 Artifact/Exhibition
    2. 检查这个 Artifact 是否在最终路线中
    3. 如果在 → 将 Story 附着到对应站点
    4. 通过 HAPPENS_AT 边找到 Zone
    5. 检查这个 Zone 是否在路线动线上
    6. 如果在 → 将 Story 附着到该 Zone 的站点
    
按 priority 排序，每个站点最多附着 2-3 条 Story，避免信息过载
```

### 3.3 编排结果的数据结构

```python
@dataclass
class PlanStop:
    zone: Zone                    # 哪个区域
    arrive_time: datetime         # 到达时间
    depart_time: datetime         # 离开时间
    duration_min: int             # 停留分钟
    artifacts: list[Artifact]     # 必看文物
    anchor_event: Event | None    # 该站是否有时间锚点事件
    stories: list[Story]          # 附着的趣味内容
    notices: list[Notice]         # 该站的注意事项
    photo_tips: list[str]         # 拍照建议

@dataclass
class Plan:
    stops: list[PlanStop]         # 有序站点列表
    entrance_hint: str            # 入口建议（如"走东门"）
    total_duration_min: int       # 总时长
    skipped_events: list[Event]   # 时间不够放弃的事件（可推荐"下次来"）
    post_visit_tips: list[str]    # 离馆后建议（如"今晚看国家宝藏"）
```

---

## 四、延迟分析

```
旧方案（串行）:
  Q1(2s) → Q2(2s) → Q3(2s) → Q4(2s) → LLM生成(3s) = ~11s

新方案（并行 + 全局编排）:
  max(Q1, Q2, Q3, Q4)(2s) → LLM编排+生成(4s) = ~6s
```

Phase 2 的 LLM 调用比旧方案的 P 稍长（因为要一次性处理更多数据），但总延迟仍然从 ~11s 降到 ~6s，几乎减半。

进一步优化的可能：
- Phase 2 的 Step 1-3（锚点提取、路线构建、空隙填充）可以用代码逻辑完成，不需要 LLM
- 只有 Step 4（附着 Story）和最终自然语言生成需要 LLM
- 这样 Phase 2 可以拆成：代码规划(~100ms) + LLM 生成(3s) = ~3s
- 总延迟：2s + 3s = ~5s

---

## 五、检索层 与 编排层 的责任边界（修订版）

| | LightRAG 检索层 | Agent 编排层 |
|---|---|---|
| **输入** | 结构化查询 + file_path 过滤 | 用户自然语言意图 |
| **核心能力** | 向量检索 + 图遍历 | 意图解析、约束求解、自然语言生成 |
| **并发** | 接受并处理 4 个并行查询 | 发起并行查询，等待全部返回 |
| **输出** | 候选节点 + 边 + chunk 文本 + 分数 | 完整的 Plan 数据结构 → 自然语言攻略 |
| **不做** | 不做意图理解、不做过滤、不做编排、不做取舍 | 不做存储、不做向量计算、不做图遍历 |
| **关键质量指标** | 召回率（候选数据是否完整）、向量质量 | 编排合理性（锚点利用率、无回头路、时间预算） |

### 与 museum_scenario_walkthrough.md 的关系

`museum_scenario_walkthrough.md` 中描述的 4 轮检索和 15 个"活的"瞬间仍然成立——那些是**最终攻略应该包含的内容**，本文档修订的是**生成那份攻略的架构**：

- 内容不变：最终攻略中的"东门入馆"、"10:00蹭讲解"、"后母戊鼎冷知识"等信息仍然来自同样的节点
- 架构变了：不再是 R1→R2→R3→R4 串行依赖，而是并行检索 + 全局编排
- walkthrough 文档可视为编排层的**输出规范**（攻略应该长什么样），本文档定义**生成过程**（怎么生成那份攻略）

---

## 六、完整流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户输入                                  │
│  "我第一次来国博，有3小时，帮我规划一下怎么逛最值？"                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │   意图解析       │
                   │ • 时间: 3小时    │
                   │ • 画像: 首次     │
                   │ • 日期: 今天     │
                   └────────┬────────┘
                            │
          ┌─────────────────┼─────────────────┐─────────────────┐
          ▼                 ▼                 ▼                 ▼
   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │ Q1 路线素材  │  │ Q2 今日事件  │  │ Q3 今日风险  │  │ Q4 趣味内容  │
   │ LightRAG    │  │ LightRAG    │  │ LightRAG    │  │ LightRAG    │
   │ hybrid 检索  │  │ hybrid 检索  │  │ hybrid 检索  │  │ hybrid 检索  │
   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
          │                │                │                │
          └────────────────┼────────────────┼────────────────┘
                           ▼
                  ┌──────────────────┐
                  │   候选数据池      │
                  │ Route, Artifact,  │
                  │ Event, Notice,    │
                  │ Story, Zone, ...  │
                  └────────┬─────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Step 1: 提取锚点与约束  │  ← 代码逻辑
              │  • 锚点: Event 时间窗口  │
              │  • 约束: Notice 禁区     │
              │  • 入口: 排队信息        │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Step 2: 围绕锚点建路线  │  ← 代码逻辑
              │  • 锚点排时间线          │
              │  • 定路线方向            │
              │  • 检查约束冲突          │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Step 3: 填充空隙       │  ← 代码逻辑
              │  • 用 Artifact 填站点   │
              │  • 按 priority 取舍     │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Step 4: 附着 Story     │  ← 代码逻辑
              │  • ABOUT 边匹配文物     │
              │  • HAPPENS_AT 匹配区域  │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Plan 数据结构          │
              │  (结构化的完整方案)      │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  LLM 自然语言生成       │  ← LLM 调用
              │  Plan → 攻略文案        │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  用户看到的攻略          │
              └────────────────────────┘
```
