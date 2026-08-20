# LLM 对话式岗位推荐设计方案

状态：已实现（一期）
日期：2026-08-20

## 1. 需求

1. 对话框引导用户填写用户画像（专业、学历、学位、偏好地区等）。
2. 收到画像后，用户请求推荐岗位（公务员 / 事业编 / 大专老师）。
3. LLM 调用现有 aurora_monitor 能力检索符合画像的岗位，按匹配度推荐 3 个最合适的岗位。

## 2. 可复用的现有能力（不重复造轮子）

| 能力 | 位置 | 说明 |
|---|---|---|
| 用户画像模型 | `aurora_web/models.py` `UserProfile` | 已含 exam_types（civil_service/public_institution/public_college）、education、degree、major、region_codes、证书、应届状态等全部所需字段 |
| 岗位级资格核验 | `aurora_monitor/eligibility.py` `evaluate_position_row` | 学历/学位/专业/应届/政治面貌/基层年限/证书七类硬核验 + 年龄/性别/户籍提示，输出 eligible / needs_review / not_eligible |
| 公告检索+打分+岗位核验流水线 | `aurora_web/recommendation.py` `RecommendationService.recommend` | 规则打分（类型+18、地区+10、专业+12、年份+8、岗位初符合+25），流程公告过滤，排除原因留痕 |
| LLM 客户端 | `aurora_web/llm.py` `LLMClient` | OpenAI 兼容 chat/completions（urllib，无 SDK 依赖），环境变量配置（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL_NAME），JSON 模式 + 失败降级 + 防注入系统提示 |
| REST 接口 | `aurora_web/main.py` | /options（地区/考试类型字典）、/profile、/recommendations、/positions/{id}、/evidence/{id}/file（来源原文）均已存在 |
| 专业目录 | `aurora_monitor/major_catalog.py` + config/jiangsu-major-catalog.json | 专业大类→细分专业映射，用于专业匹配与画像引导 |

结论：检索、核验、打分全部走现有确定性代码；LLM 只做三件事——**从对话中提取画像、调度工具、用自然语言解释推荐结果**。

## 3. 框架选型：ReAct vs PlanAndSolver vs Reflection

| 维度 | ReAct | PlanAndSolver | Reflection |
|---|---|---|---|
| 核心机制 | 思考→行动→观察循环，模型自主决定下一步 | 先生成/确定步骤计划，再逐步执行 | 生成→自我批判→修正（质量增强层，非编排框架） |
| 与本任务匹配度 | 中。推荐流程固定，自由探索带来多余轮次与漂移 | 高。任务图固定：补全画像→确认→检索→排序→解释 | 单独不适用；作为终检层价值高 |
| 延迟/成本 | 高（多轮循环，步数不可控） | 低（计划固定，LLM 轮次可数） | 增加 1 轮校验 |
| 可靠性 | 步序不确定，可能跳过画像确认直接检索 | 阶段门禁可控（画像不完整不进检索） | 拦截"编造岗位/误判资格"类幻觉 |
| 多轮追问 | 天然适合 | 需要重新规划 | 不涉及 |

**结论：PlanAndSolver 为主干 + Reflection 作终检层 + 受限 ReAct 用于追问模式。**

理由：
1. 推荐主流程是固定任务图（画像→检索→排序→Top3→解释），ReAct 的自由循环只增加延迟、token 与漂移风险，不带来收益；PlanAndSolver 的计划可固化为代码级阶段门禁。
2. 用户依据推荐做报考决策，准确性优先：Reflection 终检逐条核对"每个推荐岗位的学历/学位/专业/地区断言是否有工具返回数据支撑"，无支撑即剔除或降级标注。
3. 追问场景（"只看苏州"、"为什么过滤我"）用步数上限 3 的受限工具循环处理，不进入主流程。

## 4. 总体架构

```
浏览器对话框
   │ POST /api/v1/chat {session_id, message}
   ▼
ChatOrchestrator（新增，aurora_web/chat.py）
   ├─ 阶段状态机：slot_filling → confirm → recommending → done（会话表持久化）
   ├─ LLMClient.chat_with_tools()（扩展：OpenAI tools 协议）
   │      工具层（全部包装现有能力，模型不可绕过）：
   │        submit_profile / search_positions / get_position_detail /
   │        check_eligibility / list_options
   └─ Reflection 校验器（规则代码，非 LLM 亦可先跑规则版）
   ▼
RecommendationService / WebRepository / eligibility（现有，不动）
```

## 5. 对话与画像收集（slot_filling 阶段）

- 槽位表（必填/可选）：
  - 必填：exam_types（公务员/事业编/大专老师，可多选）、education、degree、major、region_codes
  - 可选：graduate_status、political_status、certificates、preferred_roles、grassroots_years、include/exclude_keywords
- 每轮处理（服务端确定性 + LLM 抽取）：
  1. LLM 以结构化 JSON 从用户消息抽取槽位增量（schema 固定，temperature 0）；
  2. 服务端合并进会话 profile_draft（pydantic 校验，非法值要求重填）；
  3. 缺必填 → LLM 生成追问话术（附可选项，地区/考试类型取自 /options 字典，专业用 major_catalog 提示大类）；
  4. 必填齐全 → 展示画像摘要请用户确认，确认前不进入检索。
- 画像抽取只信"用户消息"，公告内容属不可信数据（沿用现有防注入原则）。

## 6. 工具定义（function calling schema）

| 工具 | 入参 | 返回 | 包装的现有能力 |
|---|---|---|---|
| `list_options` | kind（regions/exam_types/major_categories） | 字典列表 | /options + major_catalog |
| `submit_profile` | UserProfile 全量 JSON | 校验结果+缺项 | pydantic 校验 |
| `search_positions` | profile（已确认）+ limit（默认 30） | 候选岗位列表：岗位字段 + verdict + 打分 + 公告标题/URL | RecommendationService 内部流水线（新增按岗位展开的查询入口） |
| `check_eligibility` | position_id, profile | 逐条件核验明细（conditions/questions） | evaluate_position_row |
| `get_position_detail` | position_id | 岗位行 + 公告 + 来源链接 + 证据摘录 | repository positions/notices/evidence |

约束：工具入参中的 profile 必须等于会话中已确认的 profile（服务端强制注入，防止模型编造画像）；search_positions 每会话限频（如 5 次/10 分钟）。

## 7. 推荐编排（recommending 阶段，PlanAndSolver 固化计划）

```
P1 画像门禁：profile 已确认且必填完整，否则回到 slot_filling
P2 search_positions(profile)            ← 工具调用（唯一检索入口）
P3 规则排序（服务端，非 LLM）：
   eligible 岗位 > needs_review > 其他；同级按打分降序；
   每个公告最多保留 2 个岗位避免同公告霸榜
P4 LLM 从 P3 前 8 名中选 3 个并生成推荐理由（理由必须引用工具返回字段）
P5 Reflection 终检（规则代码执行）：
   - 3 个岗位 ID 均出现在 P2 返回中（防编造）
   - 理由中的学历/学位/专业/地区断言与岗位字段一致
   - verdict=not_eligible 的岗位不得入选
   校验失败 → 剔除该岗位用候补替换（最多重试 1 次），仍失败则降级输出规则版结果
P6 输出：3 张推荐卡片（岗位、单位、地区、招录人数、匹配理由、待核对项、公告来源链接）
```

## 8. 匹配度口径（沿用现有，不新造）

- 硬条件：eligibility 七类核验决定 eligible / needs_review / not_eligible；
- 软打分：现有规则分（类型/地区/专业/年份/关键词）+ 岗位初符合 +25；
- Top3 语义 = "硬条件通过前提下软分最高的 3 个岗位"，全部 not_eligible 时明确告知"暂无可报岗位"，可给出最接近的 needs_review 岗位并说明待核实点。

## 9. 防幻觉与引用

- 推荐卡片所有字段必须来自工具返回；提示词要求逐条标注来源字段；
- 每张卡片强制携带公告原文 URL（来源佐证，用户此前明确要求）；
- 禁止"一定可以报考"类断言，沿用"初步符合，报名前需核对原文"话术；
- 公告正文作为不可信数据，其中的指令一律忽略（现有系统提示已含）。

## 10. API 与会话

- 新增 `POST /api/v1/chat`：入参 {session_id?, message}；出参 {session_id, reply, stage, profile_draft, profile_missing, recommendations?}
- 新增 SQLite 表 `chat_session(session_id, profile_draft_json, stage, created_at, updated_at)` 与 `chat_message(id, session_id, role, content, tool_calls_json, created_at)`（消息留痕便于审计与调试）
- 会话过期：24h 未活动清理；profile_draft 确认后写入 user_profile（复用现有 save_profile）

## 11. LLMClient 扩展

- 新增 `chat_with_tools(messages, tools)`：OpenAI tools/tool_choice 协议；解析 tool_calls→执行→结果回填→续跑，循环上限 6 步；
- 供应商不支持原生 function calling 时降级为"JSON 命令模拟工具调用"（模型输出 {tool, args}，服务端校验执行），协议层对上层无感；
- 模型未配置（enabled=False）：对话层降级为"表单式引导"——按槽位表逐条文字追问，推荐直接走现有 /recommendations 规则结果，功能不中断。

## 12. 实施计划

| 阶段 | 内容 | 产出 |
|---|---|---|
| 1 | ChatOrchestrator 状态机 + 会话表 + /api/v1/chat（无 LLM 的表单引导版） | 可对话补全画像并落库 |
| 2 | 工具层 5 个函数 + repository 按岗位展开查询 | 工具可独立单测 |
| 3 | LLMClient.chat_with_tools + 画像抽取/追问/推荐理由提示词 | 端到端 LLM 对话 |
| 4 | Reflection 终检 + 降级路径 + 前端对话框（static/ 页面） | 需求闭环 |

测试策略：
- 工具层单测（真实 DB fixture）；
- LLM 层沿用 test_llm.py 的 fake response 模式（mock urlopen），覆盖：槽位抽取合并、工具调用解析、Reflection 剔除编造岗位、未配置降级；
- 端到端 golden test：固定用户消息序列 → 断言阶段迁移与最终 3 卡片字段。

## 13. 风险与开放问题

1. 供应商是否支持原生 tools 协议未知 → 已设计 JSON 模拟降级；
2. 岗位表未解析的公告（detail 未抓取/附件加密）无法岗位级核验，只能公告级推荐，卡片需标注"岗位表待解析"；
3. 追问模式（改地区/换类型）：已在二期落地（见第 15 节）；
4. LLM 成本：每次推荐约 2-4 次模型调用（抽取 1 次/轮 + 理由 1 次 + 可选复检 1 次），建议 temperature 0、上下文裁剪（候选只传前 8 名的结构化字段）。

## 14. 决策记录与实现状态

- 供应商候选：gpt5.6 / qwen3.8 / deepseek（均为 OpenAI 兼容接口），由用户在环境变量
  `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME` 中手动填写；未配置时全链路降级为规则模式。
- 追问模式：一期不做（已确认）；二期已实现（2026-08）。
- 一期实现落地：
  - `aurora_web/chat_tools.py`：5 个工具函数 + OpenAI function schema；
  - `aurora_web/chat.py`：ChatOrchestrator 状态机（slot_filling → confirm → done）、
    chat_session/chat_message 会话表、Reflection 终检 `validate_recommendations`；
  - `aurora_web/llm.py`：新增 `chat`（JSON 模式）与 `chat_with_tools`（tools 循环，上限 4 轮）；
  - `aurora_web/recommendation.py`：新增 `recommend_positions` 岗位级检索入口；
  - `POST /api/v1/chat` 端点 + 独立对话页 `/static/chat.html`；
  - tests/test_chat.py：8 个用例覆盖规范化/终检/状态机/LLM 路径（fake client）。

## 15. 追问模式（二期，已实现）

状态机扩展为 `slot_filling → confirm → done ⇄ followup`：推荐完成后用户可继续追问，
会话上下文（已推荐岗位 id、卡片摘要、已展示岗位集合）持久化在 `chat_session.context_json`。

**意图分类**：LLM 优先（输出 `{"intent", "position_ref"}`），失败或未配置时按规则降级
（序数正则"第 N 个/岗位 N/N 号" → 详情；更多/还有 → 翻页；修改类关键词 → 改画像；其余 → 一般问答）。

四类意图处理（检索与核验始终走规则代码，LLM 只做分类/解释）：

| 意图 | 处理 | 输出 |
| --- | --- | --- |
| position_detail | 按序数定位岗位 → `get_position_detail` + `check_eligibility` | 岗位字段 + 逐条核验（√/?/×）+ 来源公告链接 |
| more_positions | 重新检索（确定性排序），排除已展示岗位取下一批 3 个 | 新卡片 + 累计展示计数，候选耗尽时提示放宽条件 |
| modify_profile | LLM 抽取变更字段（changes_only：只输出变更字段的完整新值）→ 合并画像 → 重新推荐 | 新画像摘要 + 新推荐卡片；缺字段则回 slot_filling |
| general | LLM tools 循环（get_position_detail / check_eligibility），输出 `{"answer": ...}` | 文本回答；失败时给出追问引导 |

防幻觉约束不变：详情/核验全部来自工具返回；不出现"一定可以报考"表述；
未配置 LLM 时详情/翻页仍可用（规则路径），改画像与一般问答给出明确引导。
