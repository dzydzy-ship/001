# AI 商家营销运营助手（MVP）

面向商家的 AI 营销运营系统，把「大模型 + 商品知识库 + 平台规则 + 热点数据 + 自动化工作流」组合起来，
实现从商品信息管理、热点发现到内容生成的完整闭环。**不训练大模型，只做编排。**

## 两大模块

- **模块一：商品信息智能加工** —— 把商家零散资料结构化提取，按平台策略生成标题/文案/短视频脚本/标签。
- **模块二：热点发现与商品关联** —— 持续采集热点，两阶段匹配后输出「热点营销机会卡」。

完整闭环：`商品资料管理 → 平台化内容生成 → 热点持续监测 → 商品×热点匹配 → 营销内容生成 → 成本与风险审核 → 人工确认发布 → 经营效果反馈`

## 快速开始

```powershell
cd C:\CC\ai-marketing-ops
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 即可使用。首次启动会自动写入 3 个示例商品并采集一批热点。

## 接入真实大模型（可选）

复制 `.env.example` 为 `.env`（或直接设置环境变量），填入任意 OpenAI 兼容接口：

```
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

**不填 API Key 时**，系统自动使用内置 Mock（规则化提取/生成/判断），保证开箱即跑、可完整演示流程。

## 架构

```
ai-marketing-ops/
├── app/
│   ├── main.py            # FastAPI 入口 + 生命周期（建库、种子、调度）
│   ├── config.py          # 环境变量 + YAML 配置加载
│   ├── database.py        # SQLAlchemy（SQLite / 可切 PostgreSQL）
│   ├── models.py          # Product / Hotspot / Content / Match
│   ├── schemas.py         # Pydantic 结构（AI 结构化输出约束）
│   ├── llm.py             # OpenAI 兼容客户端 + JSON Schema 校验
│   ├── embedding.py       # TF-IDF 余弦相似度（生产替换 Embedding+pgvector）
│   ├── scheduler.py       # APScheduler 定时采集热点
│   ├── seed.py            # 演示数据
│   ├── routers/           # products / content / hotspots / matching
│   └── services/
│       ├── extraction.py  # 模块一：结构化提取
│       ├── content.py     # 模块一：平台化内容生成
│       ├── audit.py       # 模块一：事实/合规/格式校验
│       ├── hotspots.py    # 模块二：热点采集与热度快照
│       └── matching.py    # 模块二：两阶段匹配 + 评分模型
├── config/
│   ├── platforms.yaml     # 平台策略配置库
│   └── risk_keywords.yaml # 风险/合规词表
└── frontend/index.html    # 单页前端（无构建步骤）
```

## 关键设计原则

1. **AI 只做理解/判断/生成，外部数据源提供真实信息，业务规则约束行为。** 不让 AI 凭空猜参数、热点事实、价格或平台规则。
2. **提取一次、复用多次。** 商品结构化数据落库，不每次重读原始资料；缺失字段标「待确认」而非编造。
3. **平台规则外置为配置。** 改规则不改代码（`config/platforms.yaml`）。
4. **两阶段匹配。** 先程序筛选（关键词+语义相似度+时效+敏感过滤），再大模型业务判断，最后按 `S=0.30R+0.25T+0.20A+0.15P+0.10C` 汇总。
5. **风险一票否决。** 敏感/负面/无法证实的功效直接禁止或人工复核，不因总分高放行。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/products` | 创建商品（可只传 raw_material 自动提取） |
| POST | `/products/extract` | 预览结构化提取 |
| GET | `/products` | 商品列表 |
| GET | `/content/platforms` | 平台策略列表 |
| POST | `/content/generate` | 按平台生成内容 |
| POST | `/content/{id}/review` | 内容审核（approved/rejected） |
| POST | `/hotspots/refresh` | 手动采集热点 |
| GET | `/hotspots` | 热点列表 |
| POST | `/matching/run` | 执行匹配，生成机会卡 |
| POST | `/matching/{id}/review` | 机会卡审核 |

## 从 MVP 到生产的升级路径

- **数据库**：`DATABASE_URL` 换成 PostgreSQL，`embedding.py` 换成真实 Embedding + pgvector 向量检索。
- **热点源**：在 `services/hotspots.py` 接入官方 API / 趋势服务 / RSS，逐个核实授权、配额与延迟，**勿用未授权爬虫**。
- **调度**：规模扩大后换 Celery Beat + 消息队列/流式处理。
- **反馈优化（第四阶段）**：记录曝光/互动/点击/转化/利润，迭代匹配权重（当前权重为启动假设值）。
- **经营闭环**：匹配成功后接入库存、促销价、毛利率、预算、履约能力校验，毛利为负时提示停止而非继续投放。
