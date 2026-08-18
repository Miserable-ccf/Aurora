# Aurora 岗位监控详细设计

## 1. 目标与边界

岗位监控负责发现公开招聘公告、识别与岗位相关的变化，并把经过来源和质量校验的结果交给 Aurora。它不负责自动报名，也不绕过登录、验证码、访问频率限制或反爬措施。

当前目标范围：江苏省人力资源/人事考试官网、江苏省事业单位招聘官方入口，以及南京、无锡、徐州、常州、苏州、南通、连云港、淮安、盐城、扬州、镇江、泰州、宿迁 13 个地级市的人社和事业单位招聘官方入口。每个入口可独立启停、独立设置检查周期和解析器。

MVP 目标：先登记江苏省级入口和 13 个地级市入口中的可稳定访问来源，定时发现公告，过滤无关通知，解析 PDF/XLSX/HTML，保存版本和变化，并向匹配用户发送站内通知。具体 URL 以实际核验后的来源登记表为准，不在代码中猜测或硬编码未经验证的地址。

本设计参考 `harvey503/job-find-agent` 的 `SiteConfig`、通用抓取器/专用 adapter、关键词过滤、增量状态和通知通道设计；Aurora 额外要求证据版本、字段级 diff、三态资格结果和人工复核。

## 2. 端到端流程

```text
来源登记 -> 调度任务 -> 获取列表 -> 标题关键词过滤
    -> 详情抓取 -> 内容指纹 -> 公告分类 -> 文档解析
    -> Schema 校验 -> 证据入库 -> 字段级 diff
    -> 岗位入库/更新 -> 用户订阅匹配 -> 通知去重发送
```

所有环节使用 `trace_id` 串联。抓取、解析和通知均为幂等任务，可以安全重试。

## 3. 来源模型

### 3.0 江苏来源分组

来源按行政层级和业务类型分组，调度器可以按组暂停、提频或查看健康度：

| `source_group` | 范围 | 默认优先级 |
| --- | --- | --- |
| `jiangsu_province_hrss` | 江苏省人社厅/人事考试官方入口 | P0 |
| `jiangsu_province_recruitment` | 江苏省事业单位公开招聘官方入口 | P0 |
| `jiangsu_city_hrss` | 13 个地级市人社/人事考试入口 | P1 |
| `jiangsu_city_recruitment` | 13 个地级市事业单位招聘入口 | P1 |
| `jiangsu_public_college` | 13 个地级市公办大专/高职院校官网招聘栏目 | P1 |
| `jiangsu_school` | 其他江苏高校就业/招聘信息栏目 | P2，仅作补充 |

地级市枚举固定为：`南京`、`无锡`、`徐州`、`常州`、`苏州`、`南通`、`连云港`、`淮安`、`盐城`、`扬州`、`镇江`、`泰州`、`宿迁`。县区来源作为对应地级市的子来源，不新增地区枚举。

公办大专采用独立的 `InstitutionRegistry`，不从网页标题推断学校性质：

```sql
institution (
  id              text primary key,
  name            text not null,
  city            text not null,
  ownership       text not null, -- public / private / unknown
  school_level    text not null, -- junior_college / vocational / other
  official_domain text not null,
  hr_entry_url    text,
  enabled         boolean not null default false,
  verified_at     timestamptz,
  verified_by     text
)
```

只有 `ownership=public`、`school_level` 为 `junior_college` 或经人工确认的高职院校、且 `enabled=true` 的记录才会产生监控任务。一个院校可登记多个栏目（人事处、组织部、就业网、通知公告），但仍归属于同一个 `institution_id`。

建议采用“配置优先、适配器兜底”的实现：

```python
SourceConfig(
    id="js_suzhou_recruitment_001",
    source_group="jiangsu_city_recruitment",
    region="苏州",
    publisher="苏州市人社/事业单位官方发布机关",
    discovery_type="html_list",
    adapter="generic_html_v1",
    keyword_policy="gov_recruitment_jiangsu_v1",
    check_interval_sec=21600,
)
```

大多数静态列表页使用 `generic_html_v1`；只有存在公开 API、特殊分页、JSON 接口或明显不同页面结构的来源，才增加专用 adapter，例如 `jiangsu_exam_api_v1`。专用 adapter 必须实现相同的 `discover() -> CandidateNotice[]` 接口，不能绕过来源白名单和关键词策略。

公办大专来源示例：

```python
SourceConfig(
    id="js_nanjing_college_001_hr",
    source_group="jiangsu_public_college",
    institution_id="college_nanjing_001",
    region="南京",
    publisher="某公办高等专科学校",
    discovery_type="html_list",
    adapter="generic_html_v1",
    keyword_policy="public_college_recruitment_v1",
    check_interval_sec=86400,
)
```

### 3.1 Source

```sql
source (
  id                  text primary key,
  source_group        text not null,
  institution_id      text,
  publisher           text not null,
  source_level        text not null, -- official / school / user
  region              text not null,
  exam_types          jsonb not null,
  entry_url           text not null,
  allowed_domains     jsonb not null,
  discovery_type      text not null, -- rss / api / sitemap / html_list / file
  parser_profile      text not null,
  keyword_policy      text not null,
  check_interval_sec  integer not null,
  enabled             boolean not null default true,
  last_success_at     timestamptz,
  consecutive_failures integer not null default 0,
  created_at          timestamptz not null,
  updated_at          timestamptz not null
)
```

来源必须由管理员登记。`allowed_domains` 防止页面跳转到不受信任域名；学校转载来源要关联 `related_official_source_id`，不能覆盖官方证据。登记完成前需要人工确认：域名归属、公告栏目、是否需要登录、robots/公开接口规则、页面编码、附件类型和最近一次成功抓取时间。公办大专来源还必须核对学校官网域名与 `InstitutionRegistry` 的官方域名一致。

### 3.2 Source health

每次检查写入健康记录：HTTP 状态、耗时、响应大小、重定向链、内容类型、ETag、Last-Modified、SHA-256、解析结果数量和错误原因。连续失败 3 次进入 `degraded`，连续 10 次进入 `disabled_pending_review`，由管理员确认后恢复。

## 4. 调度器设计

调度器每分钟扫描到期来源，使用数据库锁或 Redis lease 防止多 worker 重复执行。

```text
每分钟 scheduler_tick
  -> select enabled sources where next_check_at <= now()
  -> 按 source_id 获取 lease（租约 10 分钟）
  -> 创建 source_check task
  -> worker 执行并计算 next_check_at
```

默认频率：

| 来源或状态 | 默认频率 | 说明 |
| --- | --- | --- |
| 普通官方公告列表 | 6 小时 | 使用条件请求和缓存 |
| 报名截止前 72 小时 | 1 小时 | 只对相关公告来源提频 |
| 公办大专招聘页面 | 12 小时 | 招聘季可提至 4 小时 |
| 其他学校招聘页面 | 24 小时 | 连续失败自动降频 |
| 详情页 | 列表发现变化后立即 | 不全量高频轮询 |
| 失败重试 | 5m、30m、2h、12h | 指数退避并加随机抖动 |

调度器不根据模型判断频率。频率由来源配置、公告阶段和管理员策略决定。

## 5. 抓取器

### 5.1 访问约束

- 只访问 `Source.allowed_domains` 和登记的入口/详情 URL；
- 遵守 robots、站点公开接口规则和 `min_delay_ms`；
- 连接超时 15 秒，响应体默认上限 20 MB，单任务最多跟随 3 次重定向；
- 仅允许 `text/html`、`application/pdf`、公开表格类型；拒绝脚本、宏和可执行附件；
- 识别登录页、验证码页和疑似反爬响应，转为失败状态，不尝试绕过；
- 保存原始响应到对象存储，数据库只保存元数据和内容哈希。

### 5.2 条件请求与缓存

请求优先携带上次响应的 `If-None-Match` 和 `If-Modified-Since`。收到 `304` 时只记录检查成功，不创建新证据版本。收到 `200` 时先计算 SHA-256；哈希未变化则不重新解析。

### 5.3 列表发现结果

列表解析器只产生轻量候选项：

```json
{
  "source_id": "src_001",
  "title_raw": "2026年某市事业单位公开招聘公告",
  "url": "https://example.gov.cn/a/123",
  "published_at": "2026-08-15",
  "column": "人事考试",
  "discovered_at": "2026-08-15T02:00:00Z"
}
```

候选项先经过关键词策略，再决定是否抓详情。

## 6. 关键词过滤

### 6.1 规则结构

```yaml
policy_id: gov_recruitment_v1
normalize: [unicode_nfkc, fullwidth_to_halfwidth, whitespace_fold, punctuation_fold]
include_any: [招聘, 招考, 录用, 公务员, 事业单位, 公开选聘, 公开遴选, 报名]
exclude_any: [成绩公示, 工伤送达, 工伤认定, 行政处罚, 政策解读, 执法公告,
              预算公开, 财政决算, 人事任免, 评审结果, 社保缴费]
workflow_terms: [笔试, 面试, 体检, 考察, 拟录用, 递补, 资格复审]
```

公办大专使用单独策略，避免把学校招聘流程通知误删：

```yaml
policy_id: public_college_recruitment_v1
include_any: [招聘, 招聘公告, 招聘启事, 招聘信息, 人才引进, 专任教师, 教师招聘,
              辅导员, 实验员, 实训教师, 管理岗位, 公开招聘]
exclude_any: [采购, 招标, 财务公开, 学术讲座, 会议通知, 学生获奖, 成绩公示,
              工伤送达, 行政处罚]
workflow_terms: [报名, 资格审查, 笔试, 面试, 体检, 考察, 拟聘用, 递补, 录用]
```

“拟聘用”“录用”在公办大专策略中默认保留，因为它们可能是已订阅招聘公告的后续状态；只有在正文分类确认与招聘无关时才转为 `noise`。

### 6.2 判定顺序

1. 对标题、栏目和列表摘要做规范化；
2. 命中强排除词且未命中招聘流程上下文，标记 `noise`，不抓取详情；
3. 命中保留词且未命中强排除词，标记 `candidate`；
4. 排除词和保留词同时命中，或只命中流程词，标记 `needs_review`，抓取详情后复核；
5. 标题未命中但栏目属于已登记招聘栏目时，保守地进入 `needs_review`，避免漏收；
6. 详情分类仍需检查发布机关、公告类型和正文证据，标题过滤不是最终结论。

过滤结果必须保存 `matched_terms`、`policy_id`、`decision`、`title_normalized` 和规则时间。规则更新生成新版本，不覆盖旧判断。

## 7. 详情解析与公告分类

### 7.1 文档处理

```text
下载响应 -> MIME/文件签名识别 -> 病毒/宏检查
  -> HTML 清理或 PDF/XLSX 文本提取
  -> 页面/行/表格定位
  -> 公告分类 -> 字段抽取 -> Schema 校验
```

确定性解析优先提取：标题、发布机关、发布日期、报名起止时间、考试日期、附件 URL、职位代码和招录人数。模型只处理版式变化、专业表述归一化和公告类型判断，并必须返回原文引用。

### 7.2 公告分类

分类枚举建议：

```text
recruitment_notice       招聘/招考公告
job_table                职位表
exam_outline             考试大纲
registration_update      报名/缴费/资格审查通知
exam_result              成绩/进面/面试通知
medical_review           体检/考察/资格复审
employment_result        拟录用/录用/递补
college_faculty          公办大专教师/专任教师招聘
college_counselor        公办大专辅导员招聘
college_lab_staff        公办大专实验员/实训教师招聘
college_admin            公办大专行政/管理岗位招聘
noise                    无关政务信息
unknown                  无法判断
```

`exam_result` 不应被简单归入噪音，因为它可能是用户已订阅岗位的后续状态；它需要通过关联公告、职位代码或招聘上下文判断是否保留。

## 8. 证据、版本和变化 diff

### 8.1 EvidenceVersion

```sql
evidence_version (
  id                text primary key,
  source_id         text not null,
  canonical_url     text not null,
  content_sha256    text not null,
  etag              text,
  last_modified     timestamptz,
  retrieved_at      timestamptz not null,
  parser_version    text not null,
  classification    text not null,
  quality_status    text not null, -- accepted / review / rejected
  object_key        text not null
)
```

### 8.2 字段级 diff

新版本与上一接受版本比较：

```json
{
  "announcement_id": "ann_123",
  "from_version": 4,
  "to_version": 5,
  "changes": [
    {
      "field": "registration_end_at",
      "old": "2026-08-20T18:00:00+08:00",
      "new": "2026-08-22T18:00:00+08:00",
      "severity": "high",
      "evidence_ref": "ev_5#p2"
    }
  ]
}
```

高优先级字段：报名/缴费时间、考试时间、职位代码、招录人数、专业要求、学历要求、应届要求、附件替换。正文格式、访问时间和无关页脚变化不触发用户通知。

## 9. 岗位标准化

公告级字段用于筛选和提醒，岗位级字段用于资格判断。每个标准化字段保留：

```text
value              标准化值
value_raw          原文值
status             known / unknown / conflict
evidence_refs      原文片段、页码或行号
normalizer_version 标准化规则版本
```

专业名称只做候选匹配，最终资格结论仍为 `符合 / 不符合 / 待核实`。同一职位代码在不同版本中发生变化时保留历史版本，不覆盖旧分析。

当前实现：`aurora_monitor/positions.py` 负责行抽取与字段映射（`PARSER_VERSION` 记录规则版本，原始行保存在 `position.raw_row`，支持人工纠错追溯），XLSX 证据在 `_store_evidence` 入库时同步解析，历史证据可用 `parse-positions` 命令回补；`aurora_monitor/eligibility.py` 按硬条件逐项输出 `符合 / 不符合 / 待核实`，画像缺失字段一律待核实，网页工作台据此展示岗位级初核结论。

## 10. 订阅和通知

用户订阅不直接保存完整画像，只保存用于监控匹配的已确认条件：

```json
{
  "subscription_id": "sub_001",
  "user_id": "u_001",
  "regions": ["浙江"],
  "exam_types": ["province_exam", "public_institution"],
  "source_groups": ["jiangsu_city_hrss", "jiangsu_public_college"],
  "institution_ids": [],
  "years": [2026],
  "keywords": ["计算机", "杭州"],
  "events": ["new_notice", "job_condition_change", "deadline_change"],
  "channels": ["in_app"],
  "enabled": true
}
```

通知流水线：

```text
accepted evidence/version
  -> change event
  -> subscription matcher
  -> notification dedup (subscription_id + version + event_type)
  -> in-app message
```

同一公告同一版本同一事件只发送一次。通知必须包含变化摘要、原始链接、发布时间、发现时间、来源级别和风险标签；对 `needs_review` 结果明确显示“待人工核验”。

## 11. 失败、重试和人工复核

| 状态 | 含义 | 处理 |
| --- | --- | --- |
| `scheduled` | 等待调度 | 到期后创建任务 |
| `fetching` | 正在访问 | 租约超时后可重试 |
| `unchanged` | 条件请求确认无变化 | 记录健康状态 |
| `changed` | 内容哈希变化 | 创建证据版本 |
| `parse_review` | 解析低置信度或冲突 | 人工复核 |
| `accepted` | 通过质量门禁 | 可检索和触发通知 |
| `blocked` | 登录、验证码、域名或策略阻断 | 告警，不绕过 |
| `failed` | 网络、格式或服务器错误 | 指数退避重试 |

人工复核需要展示原文、解析字段、命中关键词、前一版本和 diff。修正结果生成新的 `review_version`，不得直接修改原始证据。

## 12. 监控指标和验收

必须监控：来源成功率、平均响应时间、列表候选数、噪音过滤率、详情解析成功率、字段覆盖率、重复率、误删/漏收率、证据缺失率、通知发送成功率和来源连续失败数。

MVP 验收：

- 给定测试来源，能稳定过滤“成绩公示”“工伤送达”等噪音；
- 对“事业单位面试成绩公示”等冲突标题进入复核而非直接删除；
- 同一页面重复抓取不生成重复公告或重复通知；
- 公告报名时间、职位条件变化能生成字段级 diff；
- 解析失败、验证码和登录页不会被标记为成功；
- 每条通知能回到对应的官方 URL、证据版本和原文片段。

## 13. MVP 实施顺序

1. `Source`、`SourceCheck`、`EvidenceVersion`、`Institution` 数据表和单 worker 调度器；
2. 维护江苏 13 城公办大专名录，完成学校官方域名核验；
3. 一个官方列表页适配器 + 关键词策略引擎；
4. HTML/PDF/XLSX 下载和基础解析；
5. 内容哈希、公告去重和字段 diff；
6. 岗位标准化、订阅匹配和站内通知；
7. 人工复核页面、指标面板，再扩展更多院校 adapter。
