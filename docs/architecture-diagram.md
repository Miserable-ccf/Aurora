# Aurora 架构图

下面这张图展示江苏省级、13 个地级市和公办大专招聘监控 MVP 的主要组件和数据流。将本文件粘贴到支持 Mermaid 的 Markdown 查看器即可渲染。

```mermaid
flowchart TB
    U[用户 / Web 客户端]
    API[API Gateway\n鉴权 · 限流 · 会话路由]
    PROFILE[Monitor Profile\n公务员 · 事业编 · 公办大专\n地区 · 院校 · 关键词]
    ORCH[Agent Orchestrator\n有限状态机 · 任务预算 · 重试]
    LLM[LLM\n意图识别 · 解释生成 · 备考建议]
    CTX[Context Builder\n上下文裁剪 · 证据注入]
    GUARD[Policy Guard\n隐私 · 来源 · 输出安全校验]

    TG[Tool Gateway\nJSON Schema · 白名单 · 幂等]
    SRC[Source Registry\n江苏省级 · 13 城 · 院校来源]
    INST[Institution Registry\n用户提供的公办大专白名单]
    SCHED[Scheduler\n到期来源 · 调度频率]
    FETCH[Fetcher / Parser\nHTML · PDF · XLSX]
    FILTER[Keyword Filter\n候选 · 噪音 · 待复核]
    QUAL[Qualification Engine\n硬条件三态判断]
    ANALYSIS[Analysis Engine\n竞争比 · 分数 · 风险 · 排序]
    PLAN[Study Planner\n科目 · 日期 · 周计划]

    DB[(SQLite WAL\nregion · source · institution\npolicy · profile · checks)]
    NOTICE[(Notice / Job Store\n公告 · 岗位 · 用户订阅结果)]
    EVID[(Evidence Store\n原文 · 片段 · 版本 · SHA-256)]
    SEARCH[(Search Index\n全文检索 · 过滤 · 重排)]
    MEMORY[(Memory Store\n会话记忆 · 用户确认记忆)]
    AUDIT[(JSONL / Audit\n检查日志 · 规则版本 · 审计)]
    QUEUE[[Worker\n抓取 · 解析 · 变更检测]]

    U --> API
    U --> PROFILE
    API --> ORCH
    PROFILE --> DB
    ORCH --> LLM
    ORCH --> CTX
    ORCH --> GUARD
    ORCH --> TG

    TG --> SRC
    TG --> INST
    SRC --> DB
    INST --> DB
    DB --> SCHED
    SCHED --> QUEUE
    QUEUE --> SRC
    TG --> FETCH
    TG --> FILTER
    TG --> QUAL
    TG --> ANALYSIS
    TG --> PLAN

    SRC --> FETCH
    QUEUE --> FETCH
    FETCH --> FILTER
    FILTER --> FETCH
    FETCH --> EVID
    FETCH --> SEARCH
    FETCH --> NOTICE
    QUAL --> NOTICE
    QUAL --> ANALYSIS
    ANALYSIS --> NOTICE
    PLAN --> NOTICE

    CTX --> MEMORY
    CTX --> SEARCH
    CTX --> EVID
    CTX --> NOTICE
    ORCH --> AUDIT
    TG --> AUDIT

    GUARD -.校验失败.-> ORCH
    EVID -.证据引用.-> CTX
    NOTICE -.结构化结果.-> CTX
    CTX --> LLM
    LLM --> GUARD
    GUARD --> API

    classDef entry fill:#E8F1FB,stroke:#2962A8,color:#102A43;
    classDef control fill:#FFF4D6,stroke:#B7791F,color:#5A3A00;
    classDef tool fill:#E7F6EC,stroke:#2F855A,color:#1C4532;
    classDef store fill:#F1F3F5,stroke:#6B7280,color:#1F2937;
    classDef model fill:#F4E8FF,stroke:#805AD5,color:#44337A;

    class U,API entry;
    class ORCH,CTX,GUARD control;
    class TG,SRC,INST,SCHED,FETCH,FILTER,QUAL,ANALYSIS,PLAN,QUEUE tool;
    class DB,NOTICE,EVID,SEARCH,MEMORY,AUDIT,PROFILE store;
    class LLM model;
```

## 一次监控任务

```mermaid
sequenceDiagram
    participant Scheduler as Scheduler
    participant DB as SQLite WAL
    participant Worker as Monitor Worker
    participant Site as 江苏官方来源
    participant Filter as Keyword Filter
    participant Parser as Parser
    participant Evidence as Evidence Store
    participant Notice as Notice/Job Store
    participant Profile as Monitor Profiles
    participant User as 用户

    Scheduler->>DB: 查询 enabled 且到期的 source
    Scheduler->>DB: 获取 source lease
    Scheduler->>Worker: 创建检查任务
    Worker->>Site: 条件请求 / 抓取列表页
    Site-->>Worker: HTML / PDF / XLSX
    Worker->>Filter: 标题、栏目、摘要过滤
    alt noise
        Filter-->>DB: 记录过滤结果，不抓详情
    else candidate 或 needs_review
        Filter->>Worker: 进入详情抓取
        Worker->>Parser: 解析公告和附件
        Parser->>Evidence: 保存原文版本和哈希
        Parser->>Notice: 写入公告、岗位和字段 diff
        Notice->>Profile: 匹配用户范围/关键词/事件
        Profile-->>User: 发送去重后的站内通知
    end
    Worker->>DB: 写入 source_check 和 next_check_at
```

## 一次“找岗位”请求

```mermaid
sequenceDiagram
    participant User as 用户
    participant API as API Gateway
    participant Agent as Agent Orchestrator
    participant Memory as Memory / Profile
    participant Search as Retrieval
    participant Tools as Tool Gateway
    participant Rules as Qualification + Analysis
    participant LLM as LLM

    User->>API: 找岗位 + 个人条件
    API->>Agent: 创建 TaskRun
    Agent->>Memory: 读取已确认画像
    Agent->>Agent: 检查硬条件缺口
    alt 缺少硬条件
        Agent-->>API: 返回待补充问题
        API-->>User: 请确认学历/专业/应届等条件
    else 条件完整
        Agent->>Tools: search_sources / fetch_source
        Tools->>Search: 检索公告与职位证据
        Agent->>Rules: qualify_jobs
        Rules->>Rules: 三态资格判断
        Agent->>Rules: analyze_job
        Rules-->>Agent: 竞争、分数、风险快照
        Agent->>LLM: 结构化结果 + 证据片段
        LLM-->>Agent: 报告草稿
        Agent->>Agent: 证据覆盖与安全校验
        Agent-->>API: 岗位清单 + 风险 + 来源
        API-->>User: 可核验报告
    end
```

## 关键边界

- LLM 不直接抓取网页，只能通过 `Tool Gateway` 调用工具。
- 资格结论、竞争比和风险指标由规则/分析引擎计算。
- `Evidence Store` 保存原文版本，回答必须引用证据片段。
- 用户长期记忆只有在用户确认后才写入。
