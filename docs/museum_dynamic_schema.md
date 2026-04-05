# 博物馆动态数据 JSON Schema 设计

## 设计原则

1. **统一信封格式** — 所有动态消息共享顶层结构（id、时间、类型、优先级），具体内容按 `category` 分类放在 `payload` 中
2. **时效可过滤** — 每条消息有 `valid_from` / `valid_to`，支持过期清理和时效检索
3. **空间可关联** — 通过 `zone_ids` / `artifact_ids` / `exhibition_ids` 关联到静态图谱
4. **用户画像可匹配** — 通过 `tags` + `target_audience` 支持个性化推荐过滤

## 顶层结构

```json
{
  "version": "1.0",
  "museum_id": "CN_NMC",
  "generated_at": "2026-04-05T10:00:00+08:00",
  "items": [
    { /* 每一条动态消息 */ }
  ]
}
```

## 单条消息结构

```jsonc
{
  // ====== 信封（所有类型共享） ======
  "id": "DYN_NMC_20260405_001",          // 全局唯一 ID
  "category": "activity",                 // 类型，见下方枚举
  "title": "庞贝特展策展人导览",           // 一句话标题
  "description": "由策展人张教授亲自...",   // 详细描述（会被 chunk 化存入向量库）
  "priority": 5,                          // 1-5，5最高（影响主动推送排序）
  "status": "active",                     // active / cancelled / completed / draft

  // ====== 时间 ======
  "valid_from": "2026-04-05T14:00:00+08:00",  // 生效时间
  "valid_to": "2026-04-05T15:00:00+08:00",    // 失效时间
  "recurrence": null,                          // 循环规则（见下方说明）
  "publish_time": "2026-04-03T09:00:00+08:00", // 发布时间（什么时候可以开始推送）

  // ====== 空间关联（连接到静态图谱）======
  "zone_ids": ["CN_NMC_ZONE_F3_SOUTH"],       // 关联展区
  "artifact_ids": ["ART_NMC_028","ART_NMC_029","ART_NMC_030"], // 关联文物
  "exhibition_ids": ["EXH_NMC_008"],           // 关联展览
  "theme_ids": ["THEME_NMC_007"],              // 关联主题

  // ====== 受众与标签 ======
  "tags": ["策展人导览","庞贝","古罗马","深度讲解"],
  "target_audience": ["culture_enthusiast","student"], // 画像标签（可空=全部）

  // ====== 扩展字段（按 category 不同） ======
  "payload": {
    // 不同 category 的专属字段，见下方各类型定义
  }
}
```

## category 枚举与 payload 定义

### 1. `activity` — 活动场次

涵盖：志愿者讲解、策展人导览、工作坊、讲座、沉浸式体验、演出

```jsonc
{
  "activity_type": "guided_tour",
  // 枚举: guided_tour(讲解导览) | workshop(工作坊) | lecture(讲座)
  //       performance(演出) | immersive(沉浸体验) | stamp_rally(打卡集章)
  "host": "张教授（庞贝特展策展人）",
  "capacity": 30,
  "remaining_slots": 12,
  "registration_required": true,
  "registration_url": "https://ticket.chnmuseum.cn/activity/20260405-pompeii",
  "fee": 0,
  "language": "zh-CN",
  "suitable_for_children": false,
  "min_age": 12
}
```

### 2. `exhibition_update` — 展览动态

涵盖：新展开幕、展览延期、展品轮换、展厅关闭维护

```jsonc
{
  "update_type": "opening",
  // 枚举: opening(开幕) | closing_soon(即将闭展) | extended(延期)
  //       artifact_rotation(展品轮换) | hall_closure(展厅关闭)
  "original_end_date": "2026-08-31",     // 用于延期场景
  "new_end_date": "2026-10-31",          // 用于延期场景
  "affected_artifacts": ["ART_NMC_028"], // 用于展品轮换
  "reason": "应观众要求"                  // 用于延期/关闭场景
}
```

### 3. `ticket` — 票务信息

涵盖：门票预约余量、特展购票、免费开放日

```jsonc
{
  "ticket_type": "availability",
  // 枚举: availability(余量) | special_ticket(特展票) | free_day(免费日)
  "date": "2026-04-06",
  "time_slot": "morning",               // morning / afternoon / evening / all_day
  "remaining": 327,
  "total": 5000,
  "price": 0,
  "booking_url": "https://ticket.chnmuseum.cn/booking"
}
```

### 4. `operation` — 运营公告

涵盖：临时闭馆、开放时间调整、限流、入馆排队、交通停车

```jsonc
{
  "operation_type": "closure",
  // 枚举: closure(闭馆) | hours_change(时间调整) | crowd_alert(限流)
  //       queue_status(排队) | traffic(交通) | facility_status(设施状态)
  "severity": "warning",                 // info / warning / critical
  "affected_entrance": "西门",           // 用于排队场景
  "queue_minutes": 15,                   // 用于排队场景
  "new_hours": "08:30-18:00",           // 用于时间调整
  "reason": "极端天气"
}
```

### 5. `merchandise` — 文创与餐饮

涵盖：新品上市、促销折扣、补货、咖啡厅优惠、食堂时间调整

```jsonc
{
  "merch_type": "new_release",
  // 枚举: new_release(上新) | restock(补货) | promotion(促销)
  //       cafe_special(咖啡厅) | canteen_update(食堂)
  "product_name": "国博×敦煌联名丝巾",
  "price": 298,
  "limited_quantity": 500,
  "remaining_quantity": 320,
  "image_url": "https://oss.yourdomain.com/mock/dunhuang_scarf.jpg",
  "sale_location_zone_id": "CN_NMC_FAC_GIFT_SHOP"
}
```

### 6. `content` — 内容型情报

涵盖：策展人解读、文物冷知识、UGC精选、媒体联动、修复进展

```jsonc
{
  "content_type": "curator_insight",
  // 枚举: curator_insight(策展人解读) | trivia(冷知识) | ugc(观众UGC)
  //       media_tie_in(媒体联动) | research(研究/修复进展)
  "author": "李明（国博青铜器研究室主任）",
  "source": "国博官方公众号",
  "source_url": "https://mp.weixin.qq.com/s/xxxxx",
  "media_type": "article",              // article / video / podcast / gallery
  "cover_image_url": "https://oss.yourdomain.com/mock/houmuwu_story.jpg"
}
```

### 7. `service` — 服务与设施

涵盖：语音导览、寄存租借、拍照规则、会员活动

```jsonc
{
  "service_type": "audio_guide",
  // 枚举: audio_guide(语音导览) | rental(租借) | photo_rule(拍照规则)
  //       membership(会员) | accessibility(无障碍)
  "details": "新增AI语音导览，支持中英日韩四语",
  "fee": 20,
  "location_zone_id": "CN_NMC_FAC_VISITOR_CENTER"
}
```

## recurrence 循环规则

用于重复性活动（如每日定时讲解、每周五夜场）：

```jsonc
{
  "pattern": "weekly",           // daily / weekly / monthly / custom
  "days_of_week": [5],           // 周几（1=周一，7=周日）
  "times": ["14:00","16:00"],    // 每次的开始时间
  "duration_minutes": 60,        // 每次持续时长
  "exceptions": ["2026-05-01"]   // 排除的日期（如节假日暂停）
}
```

## 字段级约束汇总

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| id | string | ✅ | 格式: `DYN_{museum}_{date}_{seq}` |
| category | enum | ✅ | activity / exhibition_update / ticket / operation / merchandise / content / service |
| title | string | ✅ | ≤50字 |
| description | string | ✅ | 会被向量化，写入知识图谱 |
| priority | int(1-5) | ✅ | 5=最高（闭馆通知），1=最低（冷知识） |
| status | enum | ✅ | active / cancelled / completed / draft |
| valid_from | ISO8601 | ✅ | 生效时间 |
| valid_to | ISO8601 | ✅ | 失效时间 |
| recurrence | object | ❌ | 仅循环事件需要 |
| publish_time | ISO8601 | ❌ | 默认=valid_from |
| zone_ids | string[] | ❌ | 关联展区 code |
| artifact_ids | string[] | ❌ | 关联文物 code |
| exhibition_ids | string[] | ❌ | 关联展览 code |
| theme_ids | string[] | ❌ | 关联主题 code |
| tags | string[] | ✅ | 用于语义检索和画像匹配 |
| target_audience | string[] | ❌ | 画像标签，空=面向全部 |
| payload | object | ✅ | 按 category 不同，结构不同 |

## target_audience 画像标签枚举

| 标签 | 说明 |
|------|------|
| `tourist` | 外地游客/旅行者 |
| `culture_enthusiast` | 文化爱好者/回头客 |
| `family` | 亲子家庭 |
| `student` | 学生/研学团 |
| `senior` | 银发族 |
| `social_media` | 文创/社交媒体型 |
| `all` | 全部（等同于字段为空） |

## priority 建议基准

| 级别 | 典型场景 |
|------|---------|
| 5 | 临时闭馆、安全通知、严重限流 |
| 4 | 展厅关闭、讲解即将开始（<30min）、门票即将售罄 |
| 3 | 活动预告、文创上新、促销 |
| 2 | 策展人文章、文物冷知识、UGC |
| 1 | 长期有效的服务信息、背景知识 |
