# Aurora 来源与院校数据库模块设计

## 1. 模块目标

本模块负责维护江苏省级来源、13 个地级市来源和由用户提供白名单的公办大专/高职院校来源，并为监控 worker 提供经过审核的抓取任务。它不保存用户岗位偏好，也不负责解析公告正文；职责边界如下：

```text
来源注册模块
  -> 提供可抓取来源和策略
监控模块
  -> 抓取来源并创建证据版本
解析模块
  -> 从证据中生成公告和岗位
用户模块
  -> 订阅地区、院校和岗位变化
```

数据库建议采用“SQLite MVP、PostgreSQL 可平滑升级”的轻量方案。单机或单 worker 部署使用 SQLite WAL 即可；当来源数量、并发 worker 或多用户管理需求超过单机能力时，再迁移 PostgreSQL。YAML 只作为初始化导入文件，数据库是运行时唯一事实来源。

SQL 示例使用 PostgreSQL 类型表示；SQLite migration 做以下映射：`jsonb -> text`（由 Repository 统一 JSON 序列化）、`timestamptz -> text`（统一保存 ISO-8601 UTC）、`uuid -> text`。业务代码不直接依赖数据库方言，所有查询通过 Repository 封装。

## 2. 分层架构

```text
Admin API / CLI
      |
Source Registry Service       # 校验、审核、启停、版本
      |
Repositories                  # 事务、查询、乐观锁
      |
SQLite/PostgreSQL              # 来源、院校、策略、任务、审计
      |
Scheduler / Fetch Worker      # 只读取 enabled 且 verified 的来源
```

代码目录建议：

```text
app/source_registry/
  domain.py                  # Institution、Source、Policy、状态枚举
  schemas.py                 # API 输入输出 DTO
  repositories.py            # InstitutionRepository、SourceRepository
  services.py                # 注册、审核、启停、导入、健康状态
  validators.py              # 域名、地区、来源级别和策略校验
  importers.py               # YAML/CSV 导入，dry-run 和幂等
  admin_api.py               # 管理端接口
  events.py                  # source.enabled、source.disabled 等事件
  migrations/                # Alembic migration
tests/source_registry/
```

监控 worker 只能调用 `list_ready_sources()` 和 `claim_due_source()`，不能直接拼接 SQL，也不能使用用户提供的 URL 绕过来源注册。

## 3. 数据模型

### 3.0 轻量化原则

首版只保留 6 组核心数据：

```text
region                 行政区
institution             公办大专白名单（可选）
source                  官网/栏目来源
keyword_policy          过滤策略
source_check            抓取健康和增量状态
monitor_profile         用户监控配置
```

公告、岗位和原始证据属于后续监控模块；不为“公务员”“事业编”“学校”各复制一套表，而是通过 `source_group` 和 `scope_type` 区分。健康记录和审计记录可以在 MVP 中合并为轻量 JSONL 日志，规模增长后再拆成数据库表。

所有表使用 UTC 时间、不可变主键和 `created_at/updated_at`。需要追溯的变更不做覆盖，而是追加版本或审计记录。

### 3.1 行政区域

```sql
create table region (
  code text primary key,
  name text not null,
  level text not null check (level in ('province', 'city', 'county')),
  parent_code text references region(code),
  enabled boolean not null default true,
  unique (parent_code, name)
);
```

初始化江苏省和 13 个地级市：南京、无锡、徐州、常州、苏州、南通、连云港、淮安、盐城、扬州、镇江、泰州、宿迁。区县是可选的三级数据，不参与首版城市枚举。

### 3.2 院校名录

```sql
create table institution (
  id text primary key,
  name text not null,
  short_name text,
  region_code text not null references region(code),
  ownership text not null check (ownership in ('public', 'private', 'unknown')),
  school_level text not null check (
    school_level in ('junior_college', 'vocational', 'undergraduate', 'other')
  ),
  official_domain text not null,
  official_site_url text not null,
  whitelist_batch_id text,
  whitelist_row_hash text,
  whitelist_provider text,
  status text not null default 'draft' check (
    status in ('draft', 'pending_review', 'verified', 'suspended', 'retired')
  ),
  verification_note text,
  verified_by text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (official_domain),
  check ((status = 'verified') = (verified_at is not null and verified_by is not null))
);
```

只有用户白名单中明确标注 `ownership=public`、`school_level in ('junior_college', 'vocational')` 且 `status=verified` 的院校可以进入公办大专监控。白名单是公办属性的权威来源，系统不根据学校名称自行推断；系统只校验字段完整性、城市枚举和官方域名格式。

白名单导入后保留 `whitelist_batch_id`、原始文件哈希、提供者和导入时间。后续白名单移除院校时，将其标记为 `retired`，不删除历史公告、证据和报告。

### 3.3 来源

一个院校可以有多个来源入口，例如人事处、组织部和通知公告栏目。省市人社来源的 `institution_id` 为空；学校来源必须关联院校。

```sql
create table source (
  id text primary key,
  source_group text not null check (source_group in (
    'jiangsu_province_hrss',
    'jiangsu_province_recruitment',
    'jiangsu_city_hrss',
    'jiangsu_city_recruitment',
    'jiangsu_public_college',
    'jiangsu_school'
  )),
  institution_id text references institution(id),
  region_code text not null references region(code),
  publisher text not null,
  source_level text not null check (source_level in ('official', 'school', 'secondary')),
  entry_url text not null,
  canonical_url text not null,
  allowed_domains jsonb not null default '[]',
  discovery_type text not null check (discovery_type in ('rss', 'api', 'sitemap', 'html_list', 'file')),
  adapter text not null,
  keyword_policy_id text not null,
  status text not null default 'draft' check (
    status in ('draft', 'pending_review', 'verified', 'enabled', 'degraded', 'disabled', 'retired')
  ),
  check_interval_sec integer not null default 21600 check (check_interval_sec >= 300),
  next_check_at timestamptz,
  last_success_at timestamptz,
  consecutive_failures integer not null default 0,
  etag text,
  last_modified text,
  last_content_sha256 text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (canonical_url),
  check (
    (source_group = 'jiangsu_public_college' and institution_id is not null)
    or (source_group <> 'jiangsu_public_college')
  )
);
```

`enabled` 不是布尔字段，而是审核状态的一部分。只有 `status=enabled` 的来源被 scheduler 领取；`verified` 表示白名单和来源字段已通过校验但暂未启动。

### 3.6 用户监控配置

用户需求通过配置记录，不修改来源表。一个用户可以有多个配置，例如“江苏公务员”和“南京公办大专教师”，分别启停。

```sql
create table monitor_profile (
  id text primary key,
  user_id text not null,
  name text not null,
  enabled boolean not null default true,
  scope_types jsonb not null,       -- civil_service / public_institution / public_college
  region_codes jsonb not null,      -- 江苏或指定地级市
  institution_ids jsonb not null default '[]',
  exam_years jsonb not null default '[]',
  include_keywords jsonb not null default '[]',
  exclude_keywords jsonb not null default '[]',
  event_types jsonb not null default '["new_notice", "deadline_change"]',
  channel text not null default 'in_app',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

`scope_types` 是通用配置入口：

```json
{
  "name": "江苏岗位监控",
  "scope_types": ["civil_service", "public_institution", "public_college"],
  "region_codes": ["JS", "JS-NJ", "JS-SZ"],
  "institution_ids": [],
  "include_keywords": ["计算机", "教师", "辅导员"],
  "event_types": ["new_notice", "job_condition_change", "deadline_change"]
}
```

过滤逻辑为：来源的 `source_group` 映射到 `scope_types`，再与用户的地区、院校、关键词和事件订阅求交集。用户关闭某类需求只更新自己的 `monitor_profile`，不影响全局来源。

### 3.4 关键词策略

```sql
create table keyword_policy (
  id text not null,
  version integer not null,
  name text not null,
  include_any jsonb not null default '[]',
  exclude_any jsonb not null default '[]',
  workflow_terms jsonb not null default '[]',
  exception_rules jsonb not null default '[]',
  status text not null default 'draft' check (status in ('draft', 'active', 'retired')),
  created_by text not null,
  created_at timestamptz not null default now(),
  primary key (id, version)
);
```

来源只引用策略 ID，策略内容版本化。更新“成绩公示”“工伤送达”等过滤词时生成新版本，历史候选项仍可按旧策略重放。

### 3.5 抓取任务和检查记录

```sql
create table source_check (
  id uuid primary key,
  source_id text not null references source(id),
  task_id uuid,
  status text not null check (status in (
    'scheduled', 'fetching', 'unchanged', 'changed', 'blocked', 'failed', 'completed'
  )),
  http_status integer,
  content_type text,
  response_bytes bigint,
  response_ms integer,
  redirect_chain jsonb not null default '[]',
  error_code text,
  error_message text,
  content_sha256 text,
  checked_at timestamptz not null default now()
);

create table source_lease (
  source_id text primary key references source(id),
  worker_id text not null,
  lease_until timestamptz not null
);
```

### 3.6 审核和审计

```sql
create table source_review (
  id uuid primary key,
  target_type text not null check (target_type in ('institution', 'source', 'policy')),
  target_id text not null,
  from_status text,
  to_status text not null,
  reviewer_id text not null,
  reason text not null,
  created_at timestamptz not null default now()
);

create table registry_audit (
  id uuid primary key,
  actor_id text not null,
  action text not null,
  target_type text not null,
  target_id text not null,
  before_json jsonb,
  after_json jsonb,
  request_id text,
  created_at timestamptz not null default now()
);
```

## 4. 状态机

### 4.1 院校状态

```text
draft -> pending_review -> verified -> suspended -> verified
                                      \-> retired
```

`pending_review` 必须人工确认公办性质、专科/高职层次和官方域名；`suspended` 用于域名变更、来源失效或学校性质待确认。

### 4.2 来源状态

```text
draft -> pending_review -> verified -> enabled
enabled -> degraded -> enabled
enabled -> disabled -> enabled
enabled -> retired
```

连续失败 3 次进入 `degraded`，连续失败 10 次进入 `disabled`，恢复前必须检查域名、页面和访问政策。抓取器不能自动把 `disabled` 来源重新启用。

## 5. 关键服务

### `InstitutionRegistryService`

- `import_whitelist()`：导入用户提供的白名单，支持 dry-run、重复检测和批次回滚；
- `create_draft()`：创建院校草稿；
- `submit_review()`：提交字段和域名校验；
- `verify()`：确认白名单批次和必填字段有效，不重新判断公办属性；
- `suspend()` / `retire()`：暂停或退役院校；
- `list_verified_public_colleges(region_code)`：供来源管理和监控使用。

### `SourceRegistryService`

- `register_source()`：校验 URL、域名、地区和院校关联；
- `approve_source()`：审核来源并绑定有效关键词策略；
- `enable()` / `disable()`：启停抓取；
- `claim_due_source(worker_id)`：带 lease 的幂等领取；
- `record_check_result()`：更新健康状态、哈希和下一次检查时间；
- `import_yaml(dry_run=True)`：批量导入模板，报告新增、更新、冲突和非法域名。

### `SourceHealthService`

根据检查记录计算成功率、连续失败、解析覆盖率和响应时间，向管理端提供健康面板，不直接修改审核状态以外的来源元数据。

### `MonitorProfileService`

- `create_profile()`：创建用户监控配置；
- `update_scope()`：切换公务员、事业编、公办大专范围；
- `validate_targets()`：检查地区和院校是否存在且已启用；
- `match_event()`：将新公告/岗位变化匹配到用户配置；
- `pause_profile()` / `resume_profile()`：暂停或恢复通知。

## 6. 管理接口

管理 API 必须独立鉴权，普通用户可以写自己的 `monitor_profile`，但不能写全局来源白名单或院校属性。

```text
POST   /admin/institutions                 创建院校草稿
POST   /admin/institutions/import          导入公办大专白名单
POST   /admin/institutions/{id}/submit     提交院校审核
POST   /admin/institutions/{id}/verify     审核通过
GET    /admin/institutions?city=南京       查询院校名录
POST   /admin/sources                      创建来源草稿
POST   /admin/sources/{id}/verify          审核来源
POST   /admin/sources/{id}/enable          启动监控
POST   /admin/sources/{id}/disable         停止监控
GET    /admin/sources/health               来源健康状态
POST   /admin/imports/sources               YAML dry-run/导入
POST   /v1/monitor-profiles                 创建用户监控配置
PATCH  /v1/monitor-profiles/{id}            修改公务员/事业编/学校范围
POST   /v1/monitor-profiles/{id}/pause      暂停配置
POST   /v1/monitor-profiles/{id}/resume     恢复配置
```

启停和审核接口必须要求幂等键，并使用 `updated_at` 或版本号做乐观锁，避免管理员覆盖彼此修改。

## 7. Worker 读取契约

worker 只允许读取以下查询结果：

```sql
select * from source
where status = 'enabled'
  and next_check_at <= now()
  and (institution_id is null or institution_id in (
    select id from institution where status = 'verified'
  ));
```

领取成功后写入 `source_lease`，任务完成或失败都必须释放租约。worker 不直接读取模板文件、不访问未登记 URL，也不能修改院校公办属性。

## 8. 索引与约束

```sql
create index source_due_idx on source(status, next_check_at);
create index source_region_group_idx on source(region_code, source_group, status);
create index institution_city_status_idx on institution(region_code, status, ownership, school_level);
create index source_check_source_time_idx on source_check(source_id, checked_at desc);
create unique index source_domain_url_idx on source(canonical_url);
create index monitor_profile_user_idx on monitor_profile(user_id, enabled);
```

关键约束：官方域名唯一、来源规范 URL 唯一、来源城市必须与院校城市一致、非公办院校不能加入 `jiangsu_public_college`、未审核策略不能绑定启用来源。

## 9. 事务和并发

- 来源审核与启用在同一事务中完成，并写入 `source_review` 和 `registry_audit`；
- `claim_due_source` 使用 `select ... for update skip locked` 或 Redis lease；
- 检查结果和下一次调度时间在同一事务提交，避免重复调度；
- YAML 导入先写临时批次表，完成全部校验后再合并，支持回滚；
- 删除采用退役状态，不物理删除来源、院校或审核记录。

## 10. 轻量部署建议

SQLite MVP 配置：

```text
aurora.db                 SQLite 数据库（WAL）
objects/                  原始 PDF/HTML/XLSX，可按 SHA-256 去重
config/                   初始化白名单和关键词策略
logs/monitor.jsonl        抓取日志和审计日志
```

单 worker、定时任务和站内通知不需要 Redis。任务状态直接存 `source_check`，租约使用 SQLite 事务锁。后续需要多 worker 时，将数据库迁移到 PostgreSQL，再引入 Redis lease 和独立队列；领域表和 API 不变。

## 11. 安全和隐私

- 管理接口和 worker 使用不同数据库账号；worker 只有来源读取和检查记录写入权限；
- URL 必须通过域名白名单、协议、端口和重定向链校验，防止 SSRF；
- 审计记录管理员、时间、前后值和原因；
- 院校来源模块不保存用户身份证、联系方式等个人敏感信息；
- 原始网页和附件放对象存储，数据库只保存元数据、哈希和引用。

## 12. 测试和验收

- 院校规则：白名单中标记为私立、未知办学性质或非专科层次的记录不能启用公办大专来源；
- 城市规则：来源关联院校的城市不一致时拒绝保存；
- 域名规则：详情页跳转到未登记域名时阻断；
- 导入规则：重复 URL、重复院校、无效策略和非法状态能在 dry-run 中报告；
- 并发规则：两个 worker 不能同时领取同一来源；
- 审计规则：审核、启停和策略变更都有可追溯记录；
- 恢复规则：来源退役后历史证据和岗位报告仍可查询。
