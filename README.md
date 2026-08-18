# Aurora

助君出重围：公务员与事业单位岗位决策助手。

当前仓库提供需求规格和 agent 指令，作为后续实现的数据与行为契约：

- [需求规格](docs/requirements.md)：MVP 范围、数据模型、筛选/分析规则、输出契约和验收标准。
- [系统架构](docs/architecture.md)：组件边界、工具调用、记忆化、检索、上下文管理和分阶段实施。
- [架构图](docs/architecture-diagram.md)：主架构图和“找岗位”请求时序图，可用 Mermaid 直接渲染。
- [岗位监控详细设计](docs/job-monitoring-design.md)：来源、调度、抓取、关键词过滤、解析、变更 diff、订阅通知和验收指标。
- [江苏来源登记模板](config/jiangsu-sources.example.yaml)：省级和 13 个地级市来源的配置槽位，需核验 URL 后启用。
- [来源与院校数据库模块设计](docs/source-registry-module-design.md)：数据库表、审核状态、来源健康、worker 读取契约和管理接口。
- [Aurora agent 指令](.agents/aurora.md)：可直接作为 agent 的 system prompt 使用。

## MVP 使用方式

启动 Aurora 后，使用 `找岗位`、`看岗位 <职位代码或链接>`、`做计划` 或 `更新来源` 之一开始。首次对话请提供考试类型、年份、省份、学历学位、专业、应届身份和目标地区；不确定项由 Aurora 标记为待核实。

MVP 只处理用户指定的一个考试类型和一个省级范围，来源必须是公开且可回链的官方信息，不支持自动报名或绕过访问控制。

公告监控会先通过可配置关键词过滤无关政务噪音（例如“成绩公示”“工伤送达”），再抓取和解析候选招聘公告；过滤结果保留规则版本，支持误删后的复核和重放。

当前监控范围聚焦江苏省级来源、南京、无锡、徐州、常州、苏州、南通、连云港、淮安、盐城、扬州、镇江、泰州、宿迁 13 个地级市来源，以及这些城市中经过名录核验的公办大专/高职院校招聘栏目。各来源需先完成白名单登记和可访问性核验，普通学校转载不覆盖人社或人事考试官网结论。

用户可以分别配置公务员、事业编和公办大专监控范围；首版数据库建议使用 SQLite WAL，后续按并发量平滑迁移 PostgreSQL。

## 监控模块试运行

当前已实现 SQLite 数据层、白名单导入、关键词过滤、HTML/JSON/RSS adapter 列表抓取、详情证据版本、HTML/PDF/XLSX 统一解析接口、公告增量去重和用户通知队列。初始化数据库：

数据库采用轻量化证据存储：`aurora.db` 保存公告、哈希、解析文本、状态和通知等结构化数据；新抓取的 HTML/PDF/XLSX/DOC 原文件按 SHA-256 保存到同目录的 `aurora_objects/`（数据库文件名变化时目录名随之变化）。这样可以保留可回链的原始证据，同时避免 SQLite 文件被大附件持续膨胀。

HTML 来源可设置 `max_pages`（1-20）限制分页深度；系统只跟随明确标记为“下一页/Next”的同域链接，并使用多页候选组合指纹判断增量变化。

来源首次成功抓取后会保存 `ETag` 和 `Last-Modified`，后续请求自动使用条件请求；服务端返回 304 时记为 `unchanged`，不会重复解析或下载正文。

可直接使用标准库运行，也可以安装命令行入口：

```bash
python3 -m pip install -e .

# 按需安装 YAML、XLSX 和 PDF 支持
python3 -m pip install -e '.[all]'

aurora-monitor --db aurora.db init
```

```bash
python3 -m aurora_monitor --db aurora.db init
```

导入你提供的公办大专白名单（CSV 字段见数据库设计）：

```bash
python3 -m aurora_monitor --db aurora.db validate-institutions --file institutions.csv

python3 -m aurora_monitor --db aurora.db import-institutions \
  --file institutions.csv --batch-id whitelist-20260816 --provider user
```

导入关键词策略、来源和用户监控配置后执行一次检查：

```bash
python3 -m aurora_monitor --db aurora.db import-policy --file config/jiangsu-policies.json
python3 -m aurora_monitor --db aurora.db import-sources --file config/jiangsu-province-sources.json
python3 -m aurora_monitor --db aurora.db import-source-yaml --file config/jiangsu-sources.example.yaml
python3 -m aurora_monitor --db aurora.db import-profile --file profile.json
python3 -m aurora_monitor --db aurora.db run-once

# 完成一次抓取并立即投递通知
python3 -m aurora_monitor --db aurora.db run-cycle

# 查看某个用户配置收到的公告和来源健康状态
python3 -m aurora_monitor --db aurora.db list-notices --profile-id profile_001
python3 -m aurora_monitor --db aurora.db source-health

# 投递 pending 通知；首版支持 console / in_app
python3 -m aurora_monitor --db aurora.db dispatch-notifications

# 常驻轮询，适合交给 systemd 或容器运行
python3 -m aurora_monitor --db aurora.db watch --interval 600 --dispatch
```

`sources.json` 中的 URL 必须来自已核验的来源，且 `allowed_domains` 必须包含其域名；模块不会自动发现或启用未登记网站。PDF 解析需要可选依赖 PyMuPDF，XLSX 解析需要 openpyxl；缺少依赖时会保留证据并标记 `needs_dependency`，不会把解析失败当作成功。

用户配置的 `event_types` 支持 `new_notice` 和 `content_change`：首次发现公告发送新公告通知，详情正文产生新证据版本时发送内容变化通知；变化事件按证据哈希去重。

通知有证据门禁：详情页抓取或解析失败时只保留候选公告和失败状态，不创建用户通知；只有详情证据成功保存后才会发送通知。

详情页中同域的职位表、岗位表、招聘计划、附件以及 `.pdf/.xlsx/.xls/.doc/.docx` 文件会作为同一公告的独立证据版本保存，并记录 `source_url`。

详情抓取失败不会永久丢失：公告记录保存 `detail_status` 和退避时间，在后续列表 unchanged 检查中自动重试，成功保存证据后再创建通知。

来源连续 3 次检查失败会进入 `degraded` 状态，仍保留低频重试；收到成功响应或 304 后自动恢复为 `enabled`。

YAML 模板中的 `<待核验>` URL 不能直接启用；导入前需要替换为真实来源，并先导入对应的关键词策略。来源导入会自动将江苏城市名称映射为 `JS-城市` 区域码。

## 本地网页工作台（第一版）

监控数据可以通过本地网页按用户画像整理。该版本读取已有 `notice` 和 `evidence_version` 数据，先用确定性规则按地区、招考类型、年份和关键词筛选，再由可选的 OpenAI-compatible LLM 生成摘要。未配置 LLM 时仍返回规则整理结果；结果是公告级相关信息，不等同于已经通过职位资格审查。

安装网页依赖：

```bash
python3 -m pip install -e '.[web]'
```

启动：

```bash
python3 -m aurora_web --db aurora.db --host 127.0.0.1 --port 18100
```

浏览器打开 <http://127.0.0.1:18100/>。用户在左侧填写招考类型、地区、年份、学历、专业和岗位偏好，点击“生成整理结果”即可查看公告、来源、证据片段和报名前核对项。画像和每次推荐结果会保存到 SQLite 的 `user_profile`、`user_profile_version` 和 `recommendation_run` 表。

第一版只适合本机使用，默认绑定 `127.0.0.1`，没有生产级登录和多用户隔离；不要直接绑定公网地址。正式部署前需要接入认证、用户数据隔离、HTTPS、限流和审计。

LLM 配置通过环境变量提供，API key 只在后端使用：

```bash
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="你的 API Key"
export LLM_MODEL_NAME="你的模型名称"
```

也可以在项目根目录创建 `.env`，填写同名配置；启动网页时会自动读取。

兼容以 `/chat/completions` 为入口的 OpenAI-compatible 服务。未同时配置这三个变量时，网页右上角显示“规则整理”，不会发起模型请求。
