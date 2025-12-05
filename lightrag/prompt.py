from __future__ import annotations
from typing import Any


PROMPTS: dict[str, Any] = {}

# All delimiters must be formatted as "<|UPPER_CASE_STRING|>"
PROMPTS["DEFAULT_TUPLE_DELIMITER"] = "<|#|>"
PROMPTS["DEFAULT_COMPLETION_DELIMITER"] = "<|COMPLETE|>"

PROMPTS["entity_extraction_system_prompt"] = """---Role---
You are a Knowledge Graph Specialist responsible for extracting entities and relationships from the input text.

---Instructions---
1.  **Entity Extraction & Output:**
    *   **Identification:** **ONLY** extract entities that belong to these specified types: `{entity_types}`. **Skip any entity that does not clearly fit one of these categories.** Do not extract entities of other types.
    *   **Entity Details:** For each identified entity, extract the following information:
        *   `entity_name`: The name of the entity. If the entity name is case-insensitive, capitalize the first letter of each significant word (title case). Ensure **consistent naming** across the entire extraction process.
        *   `entity_type`: Must be one of the following types: `{entity_types}`. **Do not use `Other` or any type not in this list.**
        *   `entity_description`: Provide a concise yet comprehensive description of the entity's attributes and activities, based *solely* on the information present in the input text.
    *   **Output Format - Entities:** Output a total of 4 fields for each entity, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `entity`.
        *   Format: `entity{tuple_delimiter}entity_name{tuple_delimiter}entity_type{tuple_delimiter}entity_description`

2.  **Relationship Extraction & Output:**
    *   **Identification:** Identify direct, clearly stated, and meaningful relationships between previously extracted entities.
    *   **N-ary Relationship Decomposition:** If a single statement describes a relationship involving more than two entities (an N-ary relationship), decompose it into multiple binary (two-entity) relationship pairs for separate description.
        *   **Example:** For "Alice, Bob, and Carol collaborated on Project X," extract binary relationships such as "Alice collaborated with Project X," "Bob collaborated with Project X," and "Carol collaborated with Project X," or "Alice collaborated with Bob," based on the most reasonable binary interpretations.
    *   **Relationship Details:** For each binary relationship, extract the following fields:
        *   `source_entity`: The name of the source entity. Ensure **consistent naming** with entity extraction. Capitalize the first letter of each significant word (title case) if the name is case-insensitive.
        *   `target_entity`: The name of the target entity. Ensure **consistent naming** with entity extraction. Capitalize the first letter of each significant word (title case) if the name is case-insensitive.
        *   `relationship_keywords`: One or more high-level keywords summarizing the overarching nature, concepts, or themes of the relationship. Multiple keywords within this field must be separated by a comma `,`. **DO NOT use `{tuple_delimiter}` for separating multiple keywords within this field.**
        *   `relationship_description`: A concise explanation of the nature of the relationship between the source and target entities, providing a clear rationale for their connection.
    *   **Output Format - Relationships:** Output a total of 5 fields for each relationship, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `relation`.
        *   Format: `relation{tuple_delimiter}source_entity{tuple_delimiter}target_entity{tuple_delimiter}relationship_keywords{tuple_delimiter}relationship_description`

3.  **Delimiter Usage Protocol:**
    *   The `{tuple_delimiter}` is a complete, atomic marker and **must not be filled with content**. It serves strictly as a field separator.
    *   **Incorrect Example:** `entity{tuple_delimiter}Tokyo<|location|>Tokyo is the capital of Japan.`
    *   **Correct Example:** `entity{tuple_delimiter}Tokyo{tuple_delimiter}location{tuple_delimiter}Tokyo is the capital of Japan.`

4.  **Relationship Direction & Duplication:**
    *   Treat all relationships as **undirected** unless explicitly stated otherwise. Swapping the source and target entities for an undirected relationship does not constitute a new relationship.
    *   Avoid outputting duplicate relationships.

5.  **Output Order & Prioritization:**
    *   Output all extracted entities first, followed by all extracted relationships.
    *   Within the list of relationships, prioritize and output those relationships that are **most significant** to the core meaning of the input text first.

6.  **Context & Objectivity:**
    *   Ensure all entity names and descriptions are written in the **third person**.
    *   Explicitly name the subject or object; **avoid using pronouns** such as `this article`, `this paper`, `our company`, `I`, `you`, and `he/she`.

7.  **Language & Proper Nouns:**
    *   The entire output (entity names, keywords, and descriptions) must be written in `{language}`.
    *   Proper nouns (e.g., personal names, place names, organization names) should be retained in their original language if a proper, widely accepted translation is not available or would cause ambiguity.

8.  **Completion Signal:** Output the literal string `{completion_delimiter}` only after all entities and relationships, following all criteria, have been completely extracted and outputted.

9.  **Entity Value Filtering (CRITICAL):**
    *   **Rule 1: NO Generic Categories as Nodes (Avoid Supernode Explosion):**
        *   **Strictly PROHIBITED**: Do NOT create entities for broad categories like "瓷器" (Porcelain), "玉器" (Jade), "文物" (Artifact), "博物馆" (Museum), "造型" (Shape), "白瓷", "青瓷", "黑瓷", "青花瓷", "粉彩瓷", "釉里红".
        *   **Mandatory Solution**: You MUST incorporate these categorical/type details into the **description** of the specific entity.
            *   *Bad*: Entity: "青花瓶", Relation: "is a", Target: "瓷器".
            *   *Good*: Entity: "青花瓶", Description: "一件明代的精美**瓷器**，属于青花瓷类别..."
    *   **Rule 2: Allow Contextual Supernodes (Time, Location, Person, Event):**
        *   **Allowed**: You MAY extract specific **Dynasties/Periods** (e.g., "明代", "永乐年间"), **Institutions** (e.g., "故宫博物院"), **People** (e.g., "乾隆皇帝", "唐英"), and **Events** (e.g., "郑和下西洋") as entities.
        *   **Reason**: These serve as valuable hubs for grouping related artifacts (Community Detection).
    *   **Rule 3: Extract ONLY Named Entities:**
        *   Extract specific, unique identifiers: "青花缠枝莲纹梅瓶" (Specific Artifact), "景德镇御窑" (Specific Kiln).
        *   Do NOT extract lists of generic items mentioned in passing (e.g., "bowls, plates, cups").

---Examples---
{examples}

---Real Data to be Processed---
<Input>
Entity_types: [{entity_types}]
Text:
```
{input_text}
```
"""

PROMPTS["entity_extraction_user_prompt"] = """---Task---
Extract entities and relationships from the input text to be processed.

---Instructions---
1.  **Strict Adherence to Format:** Strictly adhere to all format requirements for entity and relationship lists, including output order, field delimiters, and proper noun handling, as specified in the system prompt.
2.  **Output Content Only:** Output *only* the extracted list of entities and relationships. Do not include any introductory or concluding remarks, explanations, or additional text before or after the list.
3.  **Completion Signal:** Output `{completion_delimiter}` as the final line after all relevant entities and relationships have been extracted and presented.
4.  **Output Language:** Ensure the output language is {language}. Proper nouns (e.g., personal names, place names, organization names) must be kept in their original language and not translated.

<Output>
"""

PROMPTS["entity_continue_extraction_user_prompt"] = """---Task---
Based on the last extraction task, identify and extract any **missed or incorrectly formatted** entities and relationships from the input text.

---Instructions---
1.  **Strict Adherence to System Format:** Strictly adhere to all format requirements for entity and relationship lists, including output order, field delimiters, and proper noun handling, as specified in the system instructions.
2.  **Focus on Corrections/Additions:**
    *   **Do NOT** re-output entities and relationships that were **correctly and fully** extracted in the last task.
    *   If an entity or relationship was **missed** in the last task, extract and output it now according to the system format.
    *   If an entity or relationship was **truncated, had missing fields, or was otherwise incorrectly formatted** in the last task, re-output the *corrected and complete* version in the specified format.
3.  **Output Format - Entities:** Output a total of 4 fields for each entity, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `entity`.
4.  **Output Format - Relationships:** Output a total of 5 fields for each relationship, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `relation`.
5.  **Output Content Only:** Output *only* the extracted list of entities and relationships. Do not include any introductory or concluding remarks, explanations, or additional text before or after the list.
6.  **Completion Signal:** Output `{completion_delimiter}` as the final line after all relevant missing or corrected entities and relationships have been extracted and presented.
7.  **Output Language:** Ensure the output language is {language}. Proper nouns (e.g., personal names, place names, organization names) must be kept in their original language and not translated.

<Output>
"""

PROMPTS["entity_extraction_examples"] = [
    """<Input Text>
```
青花缠枝莲纹梅瓶出土于景德镇御窑遗址,属明代永乐年间官窑精品。此梅瓶通高38.5厘米,口径6.2厘米,底径13.1厘米,造型挺拔秀丽。瓶身施白釉,以进口苏麻离青料绘制缠枝莲纹,釉色青翠浓艳,纹饰精细流畅,笔触遒劲有力。器底青花双圈内书"大明永乐年制"六字双行楷书款。此瓶工艺精湛,体现了永乐时期景德镇官窑青花瓷的最高水平。现藏于故宫博物院陶瓷馆,编号为一级文物。根据《明代官窑瓷器图典》记载,此类梅瓶为宫廷陈设用器,存世稀少。
```

<Output>
entity{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}文物{tuple_delimiter}青花缠枝莲纹梅瓶是明代永乐年间景德镇官窑烧制的瓷器,通高38.5厘米,口径6.2厘米,底径13.1厘米,造型优美,纹饰精细,属一级文物。
entity{tuple_delimiter}青花瓷{tuple_delimiter}文物类别{tuple_delimiter}青花瓷是以氧化钴为着色剂在瓷坯上绘制纹饰后施釉烧制而成的釉下彩瓷器。
entity{tuple_delimiter}明代{tuple_delimiter}朝代{tuple_delimiter}明代是中国历史上的一个朝代,时间跨度为1368年至1644年。
entity{tuple_delimiter}永乐年间{tuple_delimiter}历史时期{tuple_delimiter}永乐年间是明成祖朱棣的年号时期,从1403年到1424年,是明代瓷器烧造的黄金时期。
entity{tuple_delimiter}景德镇御窑遗址{tuple_delimiter}地点{tuple_delimiter}景德镇御窑遗址位于江西省景德镇市,是明清两代专为宫廷烧造瓷器的官窑所在地。
entity{tuple_delimiter}缠枝莲纹{tuple_delimiter}纹饰图案{tuple_delimiter}缠枝莲纹是一种传统瓷器装饰纹样,以莲花枝蔓缠绕盘曲构成连续图案。
entity{tuple_delimiter}苏麻离青{tuple_delimiter}材质{tuple_delimiter}苏麻离青是明代永乐、宣德时期从西亚进口的优质青花钴料,呈色浓艳青翠。
entity{tuple_delimiter}白釉{tuple_delimiter}工艺技术{tuple_delimiter}白釉是一种透明或半透明的瓷器釉料,施于瓷胎表面经高温烧制而成。
entity{tuple_delimiter}釉下彩绘{tuple_delimiter}工艺技术{tuple_delimiter}釉下彩绘是在瓷坯上直接绘制纹饰后施釉烧成的装饰技法,色彩永不脱落。
entity{tuple_delimiter}故宫博物院{tuple_delimiter}馆藏机构{tuple_delimiter}故宫博物院是中国最大的古代文化艺术博物馆,位于北京紫禁城内。
entity{tuple_delimiter}明代官窑瓷器图典{tuple_delimiter}文献典籍{tuple_delimiter}明代官窑瓷器图典是研究明代官窑瓷器的重要学术著作和图录。
relation{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}青花瓷{tuple_delimiter}类别归属,工艺类型{tuple_delimiter}青花缠枝莲纹梅瓶属于青花瓷这一文物类别。
relation{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}永乐年间{tuple_delimiter}制作年代,历史断代{tuple_delimiter}青花缠枝莲纹梅瓶烧制于明代永乐年间。
relation{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}景德镇御窑遗址{tuple_delimiter}出土地点,制作产地{tuple_delimiter}青花缠枝莲纹梅瓶出土于景德镇御窑遗址,并在该地烧制。
relation{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}缠枝莲纹{tuple_delimiter}装饰纹样,艺术特征{tuple_delimiter}青花缠枝莲纹梅瓶的瓶身绘制有缠枝莲纹装饰。
relation{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}苏麻离青{tuple_delimiter}材料使用,工艺选择{tuple_delimiter}青花缠枝莲纹梅瓶使用进口苏麻离青料绘制纹饰。
relation{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}白釉{tuple_delimiter}工艺技术,制作方法{tuple_delimiter}青花缠枝莲纹梅瓶瓶身施白釉作为底色。
relation{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}故宫博物院{tuple_delimiter}收藏关系,保管单位{tuple_delimiter}青花缠枝莲纹梅瓶现收藏于故宫博物院陶瓷馆。
relation{tuple_delimiter}永乐年间{tuple_delimiter}明代{tuple_delimiter}时代归属,历史分期{tuple_delimiter}永乐年间是明代的一个重要历史时期。
relation{tuple_delimiter}苏麻离青{tuple_delimiter}永乐年间{tuple_delimiter}使用时期,材料应用{tuple_delimiter}苏麻离青主要在永乐、宣德年间使用。
relation{tuple_delimiter}明代官窑瓷器图典{tuple_delimiter}青花缠枝莲纹梅瓶{tuple_delimiter}文献记载,研究依据{tuple_delimiter}明代官窑瓷器图典中记载了青花缠枝莲纹梅瓶的相关信息。
{completion_delimiter}

""",
    """<Input Text>
```
1986年春,陕西省考古研究所研究员韩伟主持发掘了法门寺地宫,出土了大批唐代皇家供奉的珍贵文物。其中最引人注目的是一套完整的唐代宫廷茶具,包括鎏金银茶碾、鎏金银茶罗子、鎏金银盐台等器物,均采用锤揲、錾刻、鎏金等高超工艺制作。地宫中还发现了唐懿宗供奉的四枚佛指舍利,装于八重宝函之中,外层为檀香木函,内层逐层为银宝函、金宝函、四天王彩绘玉石宝函等。同时出土的《监送真身使随真身供养道具及恩赐金银器物宝函等并新恩赐金银器衣物帐》碑文详细记录了这批供养器物的名称、数量和供养时间。这次考古发现填补了唐代宫廷器物研究的空白,被评为20世纪中国考古十大发现之一。
```

<Output>
entity{tuple_delimiter}法门寺地宫发掘{tuple_delimiter}事件{tuple_delimiter}法门寺地宫发掘是1986年由陕西省考古研究所主持进行的重大考古发掘活动,出土大量唐代珍贵文物。
entity{tuple_delimiter}法门寺地宫{tuple_delimiter}地点{tuple_delimiter}法门寺地宫位于陕西省扶风县法门寺塔基下,是唐代皇室供奉佛教圣物的地下宫殿。
entity{tuple_delimiter}韩伟{tuple_delimiter}人物{tuple_delimiter}韩伟是陕西省考古研究所研究员,主持了1986年法门寺地宫的考古发掘工作。
entity{tuple_delimiter}陕西省考古研究所{tuple_delimiter}馆藏机构{tuple_delimiter}陕西省考古研究所是从事考古调查、勘探和发掘的专业研究机构。
entity{tuple_delimiter}唐代{tuple_delimiter}朝代{tuple_delimiter}唐代是中国历史上的一个强盛朝代,时间跨度为618年至907年。
entity{tuple_delimiter}唐代茶具{tuple_delimiter}文物类别{tuple_delimiter}唐代茶具是唐代宫廷使用的成套饮茶器具,包括茶碾、茶罗、盐台等,法门寺地宫出土了一套保存完整的宫廷茶具。
entity{tuple_delimiter}鎏金银茶碾{tuple_delimiter}文物{tuple_delimiter}鎏金银茶碾是唐代宫廷用于碾磨茶饼的器具,采用银质錾刻后鎏金制作,工艺精湛。
entity{tuple_delimiter}佛指舍利{tuple_delimiter}文物{tuple_delimiter}佛指舍利是释迦牟尼佛的手指骨遗骨,被佛教徒视为最高圣物,法门寺地宫出土四枚。
entity{tuple_delimiter}八重宝函{tuple_delimiter}文物{tuple_delimiter}八重宝函是用于盛放佛指舍利的八层套装容器,由檀香木、金、银、玉石等材质制成。
entity{tuple_delimiter}唐懿宗{tuple_delimiter}人物{tuple_delimiter}唐懿宗是唐代第十七位皇帝,在位期间多次迎奉佛骨舍利并供奉大量珍宝于法门寺。
entity{tuple_delimiter}鎏金工艺{tuple_delimiter}工艺技术{tuple_delimiter}鎏金工艺是将金汞齐涂于铜或银器表面经加热使汞挥发而使金附着的传统工艺技法。
entity{tuple_delimiter}锤揲工艺{tuple_delimiter}工艺技术{tuple_delimiter}锤揲工艺是用锤子反复捶打金属板材使其延展成型的传统金属加工技艺。
entity{tuple_delimiter}錾刻工艺{tuple_delimiter}工艺技术{tuple_delimiter}錾刻工艺是使用錾刀在金属器物表面雕刻花纹图案的装饰技法。
entity{tuple_delimiter}白银{tuple_delimiter}材质{tuple_delimiter}白银是一种贵金属,在唐代常用于制作高级器物,质地柔软便于加工。
entity{tuple_delimiter}檀香木{tuple_delimiter}材质{tuple_delimiter}檀香木是一种珍贵木材,质地致密,具有特殊香气,常用于制作佛教器物。
entity{tuple_delimiter}监送真身使碑{tuple_delimiter}文献典籍{tuple_delimiter}监送真身使碑是法门寺地宫出土的唐代石碑,详细记录了供养器物的清单和时间。
relation{tuple_delimiter}法门寺地宫发掘{tuple_delimiter}韩伟{tuple_delimiter}主持领导,学术负责{tuple_delimiter}韩伟作为陕西省考古研究所研究员,主持了法门寺地宫的考古发掘工作。
relation{tuple_delimiter}法门寺地宫发掘{tuple_delimiter}法门寺地宫{tuple_delimiter}发掘地点,工作对象{tuple_delimiter}法门寺地宫发掘活动在法门寺地宫进行。
relation{tuple_delimiter}法门寺地宫发掘{tuple_delimiter}陕西省考古研究所{tuple_delimiter}组织单位,执行机构{tuple_delimiter}陕西省考古研究所是法门寺地宫发掘的组织和执行单位。
relation{tuple_delimiter}鎏金银茶碾{tuple_delimiter}唐代茶具{tuple_delimiter}组成部分,器物归类{tuple_delimiter}鎏金银茶碾是唐代茶具的重要组成部分。
relation{tuple_delimiter}鎏金银茶碾{tuple_delimiter}鎏金工艺{tuple_delimiter}制作技艺,装饰方法{tuple_delimiter}鎏金银茶碾采用鎏金工艺进行表面装饰。
relation{tuple_delimiter}鎏金银茶碾{tuple_delimiter}锤揲工艺{tuple_delimiter}成型技术,加工手段{tuple_delimiter}鎏金银茶碾使用锤揲工艺进行成型加工。
relation{tuple_delimiter}鎏金银茶碾{tuple_delimiter}錾刻工艺{tuple_delimiter}装饰技法,纹样制作{tuple_delimiter}鎏金银茶碾表面纹饰采用錾刻工艺雕刻。
relation{tuple_delimiter}鎏金银茶碾{tuple_delimiter}法门寺地宫{tuple_delimiter}出土地点,考古发现{tuple_delimiter}鎏金银茶碾出土于法门寺地宫。
relation{tuple_delimiter}佛指舍利{tuple_delimiter}八重宝函{tuple_delimiter}盛放关系,容器承载{tuple_delimiter}佛指舍利装盛于八重宝函之中。
relation{tuple_delimiter}佛指舍利{tuple_delimiter}唐懿宗{tuple_delimiter}供奉关系,宗教活动{tuple_delimiter}佛指舍利由唐懿宗供奉于法门寺地宫。
relation{tuple_delimiter}八重宝函{tuple_delimiter}檀香木{tuple_delimiter}材料构成,制作选材{tuple_delimiter}八重宝函的外层函体使用檀香木制作。
relation{tuple_delimiter}监送真身使碑{tuple_delimiter}法门寺地宫{tuple_delimiter}出土关系,文献来源{tuple_delimiter}监送真身使碑出土于法门寺地宫,记录了供养器物信息。
relation{tuple_delimiter}唐代茶具{tuple_delimiter}唐代{tuple_delimiter}时代归属,历史断代{tuple_delimiter}这批茶具属于唐代宫廷器物。
{completion_delimiter}

""",
    """<Input Text>
```
景泰蓝又称铜胎掐丝珐琅,是北京传统手工艺的代表。明代宣德年间,这一工艺从西亚传入中国,经过工匠改良后在景泰年间达到巅峰,因其釉色以蓝色为主,故名景泰蓝。制作工艺极为复杂,需经过制胎、掐丝、烧焊、点蓝、烧蓝、磨光、镀金等十余道工序。著名工艺美术大师张同禄是国家级非物质文化遗产景泰蓝制作技艺代表性传承人,其作品《和平尊》被联合国收藏。清代康熙、雍正、乾隆三朝景泰蓝制作水平最高,现故宫博物院收藏有大量清代宫廷景泰蓝器物。根据《燕京杂记》记载,景泰年间制作的珐琅器物精美绝伦,备受皇室青睐,多用于宫廷陈设和祭祀活动。
```

<Output>
entity{tuple_delimiter}景泰蓝{tuple_delimiter}文物类别{tuple_delimiter}景泰蓝又称铜胎掐丝珐琅,是一种在铜胎上掐丝填釉烧制而成的传统工艺品。
entity{tuple_delimiter}铜胎掐丝珐琅{tuple_delimiter}工艺技术{tuple_delimiter}铜胎掐丝珐琅是在铜胎上用细铜丝掐出图案,填充彩色珐琅釉料后经多次烧制而成的工艺技法。
entity{tuple_delimiter}明代{tuple_delimiter}朝代{tuple_delimiter}明代是中国历史上的一个朝代,时间跨度为1368年至1644年,景泰蓝工艺在此时期传入并发展。
entity{tuple_delimiter}宣德年间{tuple_delimiter}历史时期{tuple_delimiter}宣德年间是明宣宗朱瞻基的年号时期,从1426年到1435年,是景泰蓝工艺传入中国的时期。
entity{tuple_delimiter}景泰年间{tuple_delimiter}历史时期{tuple_delimiter}景泰年间是明代宗朱祁钰的年号时期,从1450年到1457年,景泰蓝工艺在此时达到高峰。
entity{tuple_delimiter}清代{tuple_delimiter}朝代{tuple_delimiter}清代是中国最后一个封建王朝,时间跨度为1644年至1912年,是景泰蓝制作的鼎盛时期。
entity{tuple_delimiter}康熙{tuple_delimiter}历史时期{tuple_delimiter}康熙是清圣祖爱新觉罗·玄烨的年号,从1662年到1722年,此时期景泰蓝工艺水平极高。
entity{tuple_delimiter}雍正{tuple_delimiter}历史时期{tuple_delimiter}雍正是清世宗爱新觉罗·胤禛的年号,从1723年到1735年,延续了景泰蓝制作的高水准。
entity{tuple_delimiter}乾隆{tuple_delimiter}历史时期{tuple_delimiter}乾隆是清高宗爱新觉罗·弘历的年号,从1736年到1795年,是景泰蓝艺术的巅峰时期。
entity{tuple_delimiter}张同禄{tuple_delimiter}人物{tuple_delimiter}张同禄是中国工艺美术大师,国家级非物质文化遗产景泰蓝制作技艺代表性传承人。
entity{tuple_delimiter}和平尊{tuple_delimiter}文物{tuple_delimiter}和平尊是张同禄创作的景泰蓝艺术作品,被联合国收藏,象征世界和平。
entity{tuple_delimiter}北京{tuple_delimiter}地点{tuple_delimiter}北京是景泰蓝传统手工艺的主要产地和传承地。
entity{tuple_delimiter}故宫博物院{tuple_delimiter}馆藏机构{tuple_delimiter}故宫博物院是中国最大的古代文化艺术博物馆,收藏有大量清代宫廷景泰蓝器物。
entity{tuple_delimiter}联合国{tuple_delimiter}馆藏机构{tuple_delimiter}联合国是国际组织,收藏了张同禄的景泰蓝作品《和平尊》。
entity{tuple_delimiter}制胎工艺{tuple_delimiter}工艺技术{tuple_delimiter}制胎工艺是景泰蓝制作的第一道工序,用紫铜板制作器物胎型。
entity{tuple_delimiter}掐丝工艺{tuple_delimiter}工艺技术{tuple_delimiter}掐丝工艺是将铜丝按图案掐出花纹并粘贴在铜胎上的技术,是景泰蓝的核心工艺。
entity{tuple_delimiter}点蓝工艺{tuple_delimiter}工艺技术{tuple_delimiter}点蓝工艺是用蓝枪将珐琅釉料填充到掐丝形成的纹样格内的工序。
entity{tuple_delimiter}烧蓝工艺{tuple_delimiter}工艺技术{tuple_delimiter}烧蓝工艺是将点好釉料的器物放入炉中高温烧制使釉料熔化的工序。
entity{tuple_delimiter}镀金工艺{tuple_delimiter}工艺技术{tuple_delimiter}镀金工艺是景泰蓝制作的最后工序,在磨光后的铜丝和铜边上镀上金色。
entity{tuple_delimiter}紫铜{tuple_delimiter}材质{tuple_delimiter}紫铜是制作景泰蓝胎体的主要金属材料,具有良好的延展性和导热性。
entity{tuple_delimiter}珐琅釉料{tuple_delimiter}材质{tuple_delimiter}珐琅釉料是景泰蓝的着色材料,由石英、长石、硼砂和金属氧化物等原料熔制而成。
entity{tuple_delimiter}燕京杂记{tuple_delimiter}文献典籍{tuple_delimiter}燕京杂记是记载北京地区历史文化和风俗的古代文献,其中记录了景泰蓝的相关信息。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}铜胎掐丝珐琅{tuple_delimiter}名称关系,工艺别称{tuple_delimiter}景泰蓝是铜胎掐丝珐琅工艺的俗称。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}宣德年间{tuple_delimiter}传入时期,工艺起源{tuple_delimiter}景泰蓝工艺在明代宣德年间从西亚传入中国。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}景泰年间{tuple_delimiter}发展高峰,命名由来{tuple_delimiter}景泰蓝工艺在景泰年间达到巅峰,并因此得名。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}北京{tuple_delimiter}产地关系,传承地域{tuple_delimiter}北京是景泰蓝传统手工艺的代表性产地。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}掐丝工艺{tuple_delimiter}核心技术,制作步骤{tuple_delimiter}掐丝工艺是景泰蓝制作过程中的核心技术环节。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}点蓝工艺{tuple_delimiter}着色方法,制作工序{tuple_delimiter}点蓝工艺是景泰蓝填充色彩的关键步骤。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}烧蓝工艺{tuple_delimiter}烧制技术,色彩固定{tuple_delimiter}烧蓝工艺通过高温使珐琅釉料熔化附着在器物上。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}镀金工艺{tuple_delimiter}最后工序,表面处理{tuple_delimiter}镀金工艺是景泰蓝制作的收尾工序,增加器物的华贵感。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}紫铜{tuple_delimiter}胎体材料,基础材质{tuple_delimiter}景泰蓝使用紫铜制作器物胎体。
relation{tuple_delimiter}景泰蓝{tuple_delimiter}珐琅釉料{tuple_delimiter}着色材料,装饰介质{tuple_delimiter}景泰蓝使用珐琅釉料填充色彩和图案。
relation{tuple_delimiter}张同禄{tuple_delimiter}景泰蓝{tuple_delimiter}技艺传承,工艺代表{tuple_delimiter}张同禄是国家级非物质文化遗产景泰蓝制作技艺的代表性传承人。
relation{tuple_delimiter}张同禄{tuple_delimiter}和平尊{tuple_delimiter}创作关系,艺术作品{tuple_delimiter}和平尊是张同禄创作的景泰蓝艺术作品。
relation{tuple_delimiter}和平尊{tuple_delimiter}联合国{tuple_delimiter}收藏关系,馆藏单位{tuple_delimiter}和平尊被联合国收藏。
relation{tuple_delimiter}康熙{tuple_delimiter}清代{tuple_delimiter}时期归属,朝代关系{tuple_delimiter}康熙是清代的一个历史时期。
relation{tuple_delimiter}雍正{tuple_delimiter}清代{tuple_delimiter}时期归属,朝代关系{tuple_delimiter}雍正是清代的一个历史时期。
relation{tuple_delimiter}乾隆{tuple_delimiter}清代{tuple_delimiter}时期归属,朝代关系{tuple_delimiter}乾隆是清代的一个历史时期。
relation{tuple_delimiter}故宫博物院{tuple_delimiter}景泰蓝{tuple_delimiter}收藏关系,文物保管{tuple_delimiter}故宫博物院收藏有大量清代宫廷景泰蓝器物。
relation{tuple_delimiter}燕京杂记{tuple_delimiter}景泰蓝{tuple_delimiter}文献记载,历史资料{tuple_delimiter}燕京杂记中记载了景泰年间景泰蓝制作的相关信息。
{completion_delimiter}

""",
]

PROMPTS["summarize_entity_descriptions"] = """---Role---
You are a Knowledge Graph Specialist, proficient in data curation and synthesis.

---Task---
Your task is to synthesize a list of descriptions of a given entity or relation into a single, comprehensive, and cohesive summary.

---Instructions---
1. Input Format: The description list is provided in JSON format. Each JSON object (representing a single description) appears on a new line within the `Description List` section.
2. Output Format: The merged description will be returned as plain text, presented in multiple paragraphs, without any additional formatting or extraneous comments before or after the summary.
3. Comprehensiveness: The summary must integrate all key information from *every* provided description. Do not omit any important facts or details.
4. Context: Ensure the summary is written from an objective, third-person perspective; explicitly mention the name of the entity or relation for full clarity and context.
5. Context & Objectivity:
  - Write the summary from an objective, third-person perspective.
  - Explicitly mention the full name of the entity or relation at the beginning of the summary to ensure immediate clarity and context.
6. Conflict Handling:
  - In cases of conflicting or inconsistent descriptions, first determine if these conflicts arise from multiple, distinct entities or relationships that share the same name.
  - If distinct entities/relations are identified, summarize each one *separately* within the overall output.
  - If conflicts within a single entity/relation (e.g., historical discrepancies) exist, attempt to reconcile them or present both viewpoints with noted uncertainty.
7. Length Constraint:The summary's total length must not exceed {summary_length} tokens, while still maintaining depth and completeness.
8. Language: The entire output must be written in {language}. Proper nouns (e.g., personal names, place names, organization names) may in their original language if proper translation is not available.
  - The entire output must be written in {language}.
  - Proper nouns (e.g., personal names, place names, organization names) should be retained in their original language if a proper, widely accepted translation is not available or would cause ambiguity.

---Input---
{description_type} Name: {description_name}

Description List:

```
{description_list}
```

---Output---
"""

PROMPTS["fail_response"] = (
    "Sorry, I'm not able to provide an answer to that question.[no-context]"
)

PROMPTS["rag_response"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Knowledge Graph and Document Chunks found in the **Context**.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - Scrutinize both `Knowledge Graph Data` and `Document Chunks` in the **Context**. Identify and extract all pieces of information that are directly relevant to answering the user query.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - **MANDATORY REQUIREMENT**: You MUST include a section titled "### 历史故事脉络" (Historical Story Thread). In this section, structure the information chronologically (from earliest to latest) to tell a cohesive story.
    - For each time period or key event, integrate **Time**, **People**, and **Event** into a single narrative flow.
    - Do NOT separate People, Events, and Time into isolated lists. Instead, describe *who* did *what* at *what time* within the narrative.
    - Ensure the chronological order is strictly followed (e.g., dynasties should be in order: 唐 -> 宋 -> 元 -> 明 -> 清).
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a references section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{context_data}
"""

PROMPTS["naive_rag_response"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Document Chunks found in the **Context**.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - Scrutinize `Document Chunks` in the **Context**. Identify and extract all pieces of information that are directly relevant to answering the user query.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a **References** section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{content_data}
"""

PROMPTS["kg_query_context"] = """
Knowledge Graph Data (Entity):

```json
{entities_str}
```

Knowledge Graph Data (Relationship):

```json
{relations_str}
```

Document Chunks (Each entry has a reference_id refer to the `Reference Document List`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```

"""

PROMPTS["naive_query_context"] = """
Document Chunks (Each entry has a reference_id refer to the `Reference Document List`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```

"""

PROMPTS["keywords_extraction"] = """---Role---
You are an expert keyword extractor, specializing in analyzing user queries for a Retrieval-Augmented Generation (RAG) system. Your purpose is to identify both high-level and low-level keywords in the user's query that will be used for effective document retrieval.

---Goal---
Given a user query, your task is to extract two distinct types of keywords:
1. **high_level_keywords**: for overarching concepts or themes, capturing user's core intent, the subject area, or the type of question being asked.
2. **low_level_keywords**: for specific entities or details, identifying the specific entities, proper nouns, technical jargon, product names, or concrete items.

---Instructions & Constraints---
1. **Output Format**: Your output MUST be a valid JSON object and nothing else. Do not include any explanatory text, markdown code fences (like ```json), or any other text before or after the JSON. It will be parsed directly by a JSON parser.
2. **Source of Truth**: All keywords must be explicitly derived from the user query, with both high-level and low-level keyword categories are required to contain content.
3. **Concise & Meaningful**: Keywords should be concise words or meaningful phrases. Prioritize multi-word phrases when they represent a single concept. For example, from "latest financial report of Apple Inc.", you should extract "latest financial report" and "Apple Inc." rather than "latest", "financial", "report", and "Apple".
4. **Handle Edge Cases**: For queries that are too simple, vague, or nonsensical (e.g., "hello", "ok", "asdfghjkl"), you must return a JSON object with empty lists for both keyword types.

---Examples---
{examples}

---Real Data---
User Query: {query}

---Output---
Output:"""

PROMPTS["keywords_extraction_examples"] = [
    """Example 1:

Query: "How does international trade influence global economic stability?"

Output:
{
  "high_level_keywords": ["International trade", "Global economic stability", "Economic impact"],
  "low_level_keywords": ["Trade agreements", "Tariffs", "Currency exchange", "Imports", "Exports"]
}

""",
    """Example 2:

Query: "What are the environmental consequences of deforestation on biodiversity?"

Output:
{
  "high_level_keywords": ["Environmental consequences", "Deforestation", "Biodiversity loss"],
  "low_level_keywords": ["Species extinction", "Habitat destruction", "Carbon emissions", "Rainforest", "Ecosystem"]
}

""",
    """Example 3:

Query: "What is the role of education in reducing poverty?"

Output:
{
  "high_level_keywords": ["Education", "Poverty reduction", "Socioeconomic development"],
  "low_level_keywords": ["School access", "Literacy rates", "Job training", "Income inequality"]
}

""",
]
