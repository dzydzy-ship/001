<div align="center">
  <img src="assets/banner.svg" alt="AI 商家营销运营助手" width="100%" />
</div>

<div align="center">

**把「大模型 + 商品知识库 + 平台规则 + 热点数据 + 自动化工作流」拼成一条能跑通的营销闭环。**

不训练模型，只做编排 —— 让 AI 负责理解、判断、生成，让数据源和业务规则负责事实与边界。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Windows](https://img.shields.io/badge/Windows-%E5%8F%8C%E5%87%BB%E5%8D%B3%E7%94%A8-0078D6?logo=windows&logoColor=white)](#-快速开始)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek%20%2F%20OpenAI%20%E5%85%BC%E5%AE%B9-4D6BFE)](#-接入真实大模型可选)
[![No Build](https://img.shields.io/badge/Frontend-%E5%8D%95%E9%A1%B5%C2%B7%E9%9B%B6%E6%9E%84%E5%BB%BA-22d3ee)](#-技术栈)
[![Mock Ready](https://img.shields.io/badge/%E4%B8%8D%E5%A1%AB%20Key-%E5%86%85%E7%BD%AE%20Mock%20%E5%8F%AF%E8%B7%91-8b5cf6)](#-接入真实大模型可选)

</div>

---

## 📖 项目简介

这是一套面向**中小商家 / 自营店铺**的 AI 营销运营系统。它解决的不是「让 AI 写一句话」，
而是四个连在一起的真问题：

| 痛点 | 本项目的做法 |
|---|---|
| 商品资料是零散的 Excel、口述、商品图，AI 根本不知道你在卖什么 | **结构化提取**：一次提取落库，标题、卖点、规格、标签全部变成字段 |
| 每个平台的内容规矩都不一样，一篇文案全平台发就是发不出去 | **平台策略外置**：小红书 / 抖音 / 快手 / B站 / 电商，各自一套 Skill 配置 |
| 追热点全靠人盯，热点跟自家商品有没有关系全凭感觉 | **两阶段匹配**：程序筛选 + 大模型判断，输出可执行的「营销机会卡」 |
| 打折、发券、凑满减，算到最后发现卖一单亏一单 | **利润防护 Agent**：数字只能由计算引擎给，模型不许自己算 |

> 典型场景：问一句「**SR-72 这款按两件八折 + 29 元券卖会亏吗？**」
> —— 引擎给出成交额 298、毛利 −56.26（−12.6%），触发红线拦截，而不是让模型编一个「应该还行」。

---

## ✨ 功能特性

### 1️⃣ 商品信息智能加工

- **多样输入**：Excel / CSV 表格批量导入（带列映射预览）、直接粘贴表格文本、一段口述文字。
- **结构化提取**：AI 按固定 Schema 输出 `名称 / 类目 / 卖点 / 规格 / 适用人群 / 价格 / 标签`，
  缺失字段标 `待确认`，**不许编造**。
- **批量运算**：一次对整表商品做批量计算与批量生成，结果可回写。
- **单件利润**：商品列表直接显示单件利润，支持给单品单独设置毛利率红线。

### 2️⃣ 热点发现 × 商品匹配

- **持续采集**：知乎热榜、今日头条、B 站等源（`config/sources.yaml`，可增删、可关停），APScheduler 定时刷新。
- **两阶段匹配**：先程序侧筛选（关键词 + TF-IDF 语义相似度 + 时效 + 敏感词过滤），
  再交大模型做业务判断，最后按权重汇总：

  ```
  S = 0.30·R(相关度) + 0.25·T(时效) + 0.20·A(受众契合) + 0.15·P(平台适配) + 0.10·C(转化潜力)
  ```

- **输出营销机会卡**：热点 × 商品 → 切入角度、建议平台、风险提示、可直接生成内容。

### 3️⃣ 利润防护 Agent

- **工具调用式 Agent**：模型自己决定「查哪款商品、用哪套口径、算什么账」，
  但**所有数字来自确定性计算引擎**（`app/services/pricing.py`），模型只负责解释。
- **三视图工作台**：左侧对话、中间账单（成交额 / 佣金 / 运费 / 毛利 / 毛利率逐项列出）、
  右侧全店体检（哪些商品在红线以下）。
- **计价口径可改**：佣金率、佣金基数（券后 / 券前）、运费、包邮补贴、满件折扣、券额、
  毛利率红线全部在 `config/pricing_policy.yaml` + 设置页，改口径不用改代码。
- **券后 vs 券前**：差额恒为 `券额 × 佣金率`，一眼能看出两种算法为什么都"对"。

### 4️⃣ 平台化内容生成与审核

- **5 套平台 Skill**：小红书（种草笔记）、抖音（口播脚本）、快手、B站（视频简介 / 分镜）、电商（详情页卖点）。
- **事实校验**：生成后自动比对商品事实字段，检出「编造参数 / 夸大功效 / 违规词」，命中直接打回。
- **人工审核闭环**：内容与机会卡都有 `approved / rejected` 状态，不自动发布。

### 5️⃣ 开箱即用的一键启动

- 双击 `一键启动.bat`：自动找 Python → 建 `.venv` → 装依赖（多镜像回退）→ 起服务 → 打开浏览器。
- 默认监听 `0.0.0.0`，启动后同时打印**本机地址**和**局域网地址**，同一 Wi-Fi 下的手机 / 同事电脑直接访问。
- 端口被占自动顺延、服务已在跑只开浏览器、换电脑 Copy 过来的 `.venv` 自动识别并重建。
- 出问题双击 `诊断工具.bat`，一键出体检报告。

---

## 🧭 业务闭环

```mermaid
flowchart LR
    A["商品资料<br/>Excel · CSV · 粘贴 · 口述"] --> B["结构化提取<br/>字段落库 · 缺项标待确认"]
    B --> C["平台化内容生成<br/>小红书 · 抖音 · 快手 · B站 · 电商"]
    D["热点采集<br/>知乎 · 头条 · B站"] --> E["两阶段匹配<br/>程序筛选 → 大模型判断"]
    B --> E
    E --> F["营销机会卡"]
    F --> C
    C --> G{"利润防护 + 事实/合规校验"}
    G -- 低于红线 --> H["拦截 / 人工复核"]
    G -- 通过 --> I["人工审核发布"]
    H --> I
```

---

## 🖼 界面预览

<table>
  <tr>
    <td width="50%"><img src="assets/screenshot-products.png" alt="商品管理" /></td>
    <td width="50%"><img src="assets/screenshot-profit-guard.png" alt="利润防护" /></td>
  </tr>
  <tr>
    <td align="center"><sub><b>商品管理</b>：粘贴资料自动结构化 · Excel 批量导入 · 单件利润</sub></td>
    <td align="center"><sub><b>利润防护</b>：一句话出账单 · 逐项归因 · 跌破红线拦截</sub></td>
  </tr>
</table>

---

## 🚀 快速开始

### 方式一：Windows 双击即用（推荐给非开发者）

1. **装 Python（只需一次）**：到 <https://www.python.org/downloads/> 下载安装，第一屏务必勾选
   **Add python.exe to PATH**；或命令行执行 `winget install -e --id Python.Python.3.12`。
   不需要自己建虚拟环境，也不需要手动 `pip install`。
2. **解压**整个压缩包到任意目录（建议路径短、别放 OneDrive；中文路径也支持）。
3. **双击 `一键启动.bat`**。首次会自动创建 `.venv` 并安装依赖（1~3 分钟），装好后浏览器自动打开。

启动成功后窗口会打印两行地址：

```
本机访问    http://127.0.0.1:8000
局域网访问  http://192.168.1.23:8000
```

同一路由器 / Wi-Fi 下的其他电脑、手机，用**局域网访问**那行的地址打开即可
（**不要**发 `127.0.0.1`，那个地址在别人机器上指的是别人自己）。

### 方式二：命令行手动启动（macOS / Linux / 开发调试）

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> `--host 0.0.0.0` 才能被其他机器访问；`127.0.0.1` 只有本机能开，这正是「换台电脑就打不开」的根因。
> 也可以直接跑 `python scripts/serve_local.py 8000`，会读 `APP_HOST` / `APP_PORT` 并打印局域网地址。

### 启动器常用参数

```bat
一键启动.bat                 :: 默认端口 8000，局域网可访问
一键启动.bat -Port 9000      :: 换端口（被占会自动顺延）
一键启动.bat -Local          :: 只允许本机访问（监听 127.0.0.1）
一键启动.bat -NoBrowser      :: 不自动打开浏览器
诊断工具.bat                 :: 环境自检 → logs\诊断报告.txt
允许局域网访问.bat            :: 放行防火墙 8000-8020（仅本地子网，需管理员确认一次）
```

启动器退出码：`0` 成功 / `2` 找不到 Python / `3` 环境或依赖安装失败 / `4` 服务启动失败 / `9` 启动器自身异常。

---

## ⚙️ 配置

复制 `.env.example` 为 `.env`（**`.env` 已在 `.gitignore` 里，不会被提交**），按需修改：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 空 | 任意 OpenAI 兼容 Key；**留空则自动使用内置 Mock**，全流程可跑 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | 接口地址，换 OpenAI / 通义千问只改这里 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `APP_HOST` | `0.0.0.0` | 监听地址；`127.0.0.1` 表示仅本机 |
| `APP_PORT` | `8000` | 首选端口，被占用时启动器自动顺延（最多 +30） |
| `DATABASE_URL` | 空（SQLite `data/app.db`） | 换 PostgreSQL：`postgresql+psycopg2://user:pass@host/db` |
| `HOTSPOT_INTERVAL_MINUTES` | `15` | 热点定时采集间隔（分钟） |

> 系统里已设置的同名环境变量**优先于** `.env`。
> `data/app.db` 首次启动自动生成，并写入示例商品（3 个通用 + 4 个模型飞机）与一批热点，**不要把它提交进仓库**。

### 接入真实大模型（可选）

```ini
LLM_API_KEY=sk-你的key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

也可以在页面「设置 → 大模型」里填 Key（只存进程内存，不落盘，接口只返回掩码）。
**不填 Key 时**系统自动切到内置 Mock（规则化提取 / 生成 / 判断），保证开箱即跑、可完整演示流程。

---

## 🗂 目录结构

```
店子宠物/
├── 一键启动.bat              # 双击启动（纯 ASCII 引导，逻辑在 scripts/launcher.ps1）
├── 允许局域网访问.bat         # 一键加防火墙入站规则（仅本地子网）
├── 诊断工具.bat              # 环境体检 → logs/诊断报告.txt
├── requirements.txt
├── .env.example              # 配置模板（复制成 .env 使用）
├── app/
│   ├── main.py               # FastAPI 入口 + 生命周期（建库 / 种子 / 调度）
│   ├── config.py             # 环境变量 + YAML 配置加载
│   ├── database.py           # SQLAlchemy 引擎与会话（SQLite WAL）
│   ├── models.py             # Product / Hotspot / Content / Match / ProductCost …
│   ├── schemas.py            # Pydantic 结构（约束 AI 结构化输出）
│   ├── llm.py                # OpenAI 兼容客户端 + Mock + JSON Schema 校验
│   ├── embedding.py          # TF-IDF 余弦相似度（生产可换 Embedding + pgvector）
│   ├── scheduler.py          # APScheduler 定时采集热点
│   ├── seed.py               # 示例数据
│   ├── routers/              # products / content / hotspots / matching / agent / settings / costs
│   └── services/
│       ├── extraction.py     # 结构化提取
│       ├── content.py        # 平台化内容生成
│       ├── audit.py          # 事实 / 合规 / 格式校验
│       ├── hotspots.py       # 热点采集与热度快照
│       ├── matching.py       # 两阶段匹配 + 评分模型
│       ├── pricing.py        # 利润计算引擎（Agent 取数的唯一来源）
│       ├── costs.py          # 成本 / 建议售价 / 全店体检
│       ├── table_parser.py   # Excel / CSV / 粘贴表格解析
│       ├── agent.py          # 利润防护 Agent（工具调用编排）
│       ├── agent_skill.py    # Agent 人格与工具说明（MD 载入）
│       └── collectors/       # 知乎 / 头条 / B站 / dailyhot 采集器
├── config/
│   ├── platforms.yaml        # 平台策略库（小红书 / 抖音 / 快手 / B站 / 电商）
│   ├── platform_skill_*.md   # 各平台写作 Skill
│   ├── agent_skill.md        # Agent 系统提示词
│   ├── pricing_policy.yaml   # 计价口径（佣金、运费、满减、券、红线）
│   ├── risk_keywords.yaml    # 风险 / 违规词表
│   └── sources.yaml          # 热点采集源开关
├── data/                     # SQLite 运行目录（首次启动自动生成，已忽略）
├── frontend/index.html       # 单页前端（无构建步骤、无框架、无 node_modules）
├── scripts/
│   ├── launcher.ps1          # 启动器主逻辑（探测 Python / 建 venv / 装依赖 / 端口 / 健康检查）
│   ├── allow_firewall.ps1    # 防火墙规则
│   ├── serve_local.py        # 轻量启动脚本
│   ├── demo_transcript.py    # 示例对话脚本
│   └── extract_js.py
├── tests/                    # unittest，含 AI 流程与计价口径回归
└── 说明文档/                  # 03 ~ 13：接入方案、演示手册、匹配原理、启动器原理 …
```

---

## 🛠 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| Web 框架 | **FastAPI** + Uvicorn | 自动 OpenAPI 文档（`/docs`） |
| 数据校验 | **Pydantic v2** | 约束 AI 结构化输出，不合格就重试 |
| 持久化 | **SQLAlchemy 2.0** + SQLite（WAL） | 换 PostgreSQL 只改 `DATABASE_URL` |
| 大模型 | **任意 OpenAI 兼容接口** + 内置 Mock | 默认 DeepSeek；无 Key 也能跑 |
| 相似度 | 自研 TF-IDF 余弦（纯 Python） | 无重依赖；生产可替换向量库 |
| 调度 | **APScheduler** | 定时采集热点 |
| 表格 | openpyxl / xlrd | Excel / CSV 批量导入 |
| 前端 | **原生 HTML/JS 单文件** | 零构建、零依赖，改完刷新即可 |
| 启动器 | **PowerShell + 批处理引导** | 自动探测 Python、重建 `.venv`、端口顺延 |

---

## 🧠 核心设计原则

1. **AI 只做理解 / 判断 / 生成，事实与规则来自外部。**
   不让模型猜参数、猜热点事实、猜价格、猜平台规则；缺失就标「待确认」。
2. **提取一次、复用多次。** 商品结构化数据落库，不每次重读原始资料，省 token 也省时间。
3. **平台规则外置为配置。** 改规则不改代码（`config/platforms.yaml`、`config/pricing_policy.yaml`）。
4. **两阶段匹配。** 先程序筛选（关键词 + 语义 + 时效 + 敏感过滤），再大模型业务判断，最后加权汇总。
5. **风险一票否决。** 敏感 / 负面 / 无法证实的功效直接拦截或转人工复核，不因总分高而放行。
6. **所有钱都由引擎算。** 利润防护 Agent 里模型只能调用工具取数，不许自己编数字。

---

## 🔌 API 一览

服务起来后访问 <http://127.0.0.1:8000/docs> 可以看完整交互式文档。核心接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/products/import/preview` · `/products/import/confirm` | 表格导入：预览列映射 / 确认入库 |
| `POST` | `/products/extract` | 预览结构化提取 |
| `POST` | `/products/batch-calc` | 整表批量运算 |
| `POST` | `/content/analyze` · `/content/generate` | 素材分析 / 按平台生成内容 |
| `POST` | `/content/{id}/review` | 内容审核（approved / rejected） |
| `POST` | `/hotspots/refresh` | 手动触发热点采集 |
| `POST` | `/matching/run` | 执行匹配，生成营销机会卡 |
| `POST` | `/matching/{id}/review` | 机会卡审核 |
| `POST` | `/agent/chat` | 利润防护 Agent 对话（工具调用） |
| `POST` | `/agent/calc` · `GET /agent/checks` · `GET /agent/store-scan` | 账单测算 / 体检记录 / 全店体检 |
| `GET/POST/DELETE` | `/settings/llm` · `/settings/policy` | 大模型与计价口径配置 |

<details>
<summary><b>展开全部端点（按路由分组）</b></summary>

**`/products`** — `POST /import/preview`、`POST /import/confirm`、`POST /extract`、`POST /batch-calc`、
`POST ""`、`GET ""`、`GET /{id}`、`PUT /{id}`、`DELETE /{id}`、`GET /{id}/cost`、`PUT /{id}/cost`、`GET /{id}/recommend-price`

**`/content`** — `GET /platforms`、`POST /analyze`、`POST /generate`、`GET ""`、`GET /{id}`、
`POST /{id}/review`、`DELETE /{id}`

**`/hotspots`** — `POST /refresh`、`POST ""`、`PUT /{id}`、`DELETE /{id}`、`GET ""`、`GET /{id}`

**`/matching`** — `POST /run`、`GET ""`、`GET /{id}`、`POST /{id}/review`

**`/agent`** — `POST /chat`、`POST /reset`、`GET /policy`、`GET /configs`、`GET /product/{id}/guard`、
`POST /calc`、`GET /checks`、`GET /store-scan`

**`/settings`** — `GET /llm/providers`、`GET /llm`、`POST /llm`、`POST /llm/test`、`DELETE /llm`、
`GET /policy`、`POST /policy`、`POST /policy/reset`

**基础** — `GET /`（单页前端）、`GET /health`（`{"status":"ok"}`，启动器就靠它判断服务是否真的起来了）

</details>

---

## 🌐 局域网 / 多机访问

默认监听 `0.0.0.0`，所以同一局域网内可以直接访问。其他电脑打不开时按顺序排查：

1. 在**运行服务的那台电脑**上双击一次 `允许局域网访问.bat`（管理员确认一次）。
   它只放行 TCP 8000–8020 且**仅对本地子网**生效，公网 IP 依然进不来。
2. 确认两台机器在同一网段（同一个路由器 / Wi-Fi，手机别连到「访客网络」）。
3. 公司 / 学校网络常开 **AP 隔离**，同一 Wi-Fi 也互相访问不了 —— 用手机热点验证一下。
4. 仍不行就双击 `诊断工具.bat`，把窗口内容和 `logs\诊断报告.txt` 一起发出来。

原理、新旧对照、退出码与 9 步排障见 **[`说明文档/13_启动器与局域网访问.md`](说明文档/13_启动器与局域网访问.md)**。

> ⚠️ **本应用没有登录鉴权**，`0.0.0.0` 只适合在可信的家用 / 办公局域网里使用。
> 需要公网访问请用 ngrok / cloudflared / Tailscale 之类的隧道，并自己加访问控制。

---

## ❓ 常见问题

<details>
<summary><b>第一次启动很慢？</b></summary>

首次要创建 `.venv` 并安装依赖（1~3 分钟），还要初始化数据库、采集一批热点（约十秒）。
之后启动一般几秒内完成。
</details>

<details>
<summary><b>提示「本机没有可用的 Python 3.10+」？</b></summary>

确实没装，或只装了 Microsoft Store 的占位版本（`WindowsApps\python.exe` 一执行就跳商店，启动器会跳过它）。
装一个 <https://www.python.org/downloads/> 的正式安装包即可，记得勾选 **Add python.exe to PATH**。
</details>

<details>
<summary><b>端口被占用 / 想换端口？</b></summary>

启动器会自动从 8000 往后顺延（最多 +30）并在窗口里告诉你实际端口；也可以 `一键启动.bat -Port 9000` 指定。
如果检测到同一套程序已经在跑，它只会打开浏览器，不会起第二份进程去抢同一个数据库。
</details>

<details>
<summary><b>把项目拷到别的电脑后打不开？</b></summary>

`.venv` 里记的是**原来那台机器**的 Python 绝对路径，不能跨机复用。
新版本启动器会实跑校验 `sys.prefix`，发现不一致就自动改名成 `.venv.broken-时间戳` 并重建（旧目录保留不删）。
</details>

<details>
<summary><b>旧数据库报 <code>no such column</code>？</b></summary>

新库会自动建表；旧库补两列即可：

```sql
ALTER TABLE product_costs ADD COLUMN profit_floor_pct FLOAT;
ALTER TABLE promo_configs ADD COLUMN default_coupon FLOAT;
```
</details>

<details>
<summary><b>没有 API Key 能跑吗？</b></summary>

能。不填 Key 时自动使用内置 Mock（规则化提取 / 生成 / 判断），全流程可演示，只是文采是模板级的。
</details>

---

## 📚 文档索引

| 文档 | 内容 |
|---|---|
| [`说明文档/03_接入方案.md`](说明文档/03_接入方案.md) | 整体接入思路与边界 |
| [`说明文档/06_答辩演示手册.md`](说明文档/06_答辩演示手册.md) | 演示脚本与讲解顺序 |
| [`说明文档/07_热点采集与AI匹配.md`](说明文档/07_热点采集与AI匹配.md) | 采集源、相似度、两阶段匹配原理 |
| [`说明文档/08_平台宣传Skill与事实校验.md`](说明文档/08_平台宣传Skill与事实校验.md) | 平台 Skill 与事实校验规则 |
| [`说明文档/09_本轮改动清单.md`](说明文档/09_本轮改动清单.md) | 佣金基数两档化等改动明细 |
| [`说明文档/11_表格批量导入与批量运算.md`](说明文档/11_表格批量导入与批量运算.md) | 表格导入 / 批量计算 |
| [`说明文档/13_启动器与局域网访问.md`](说明文档/13_启动器与局域网访问.md) | 启动器原理、退出码、排障 9 步 |
| [`接入说明_利润防护.md`](接入说明_利润防护.md) | 利润防护 Agent 的计价口径与接入 |
| [`更新日志.md`](更新日志.md) | 按版本记录的改动 |

---

## 🔭 从 MVP 到生产

- **数据库**：`DATABASE_URL` 换 PostgreSQL；`embedding.py` 换真实 Embedding + pgvector 向量检索。
- **热点源**：接入官方 API / 趋势服务 / RSS，逐个核实授权、配额与延迟，**勿用未授权爬虫**。
- **调度**：规模上来后换 Celery Beat + 消息队列 / 流式处理。
- **反馈优化**：记录曝光 / 互动 / 点击 / 转化 / 利润，迭代匹配权重（当前权重是启动假设值）。
- **经营闭环**：匹配成功后接库存、促销价、毛利率、预算、履约能力校验；毛利为负时提示停止而非继续投放。
- **多租户与鉴权**：加登录、按店铺隔离数据（当前为单店铺演示形态）。

---

## 🔒 安全与免责

- 仓库内**不包含**任何真实 API Key、`.env`、运行数据库或日志；`.env.example` 只有空值和占位。
- `data/app.db` 首次启动自动生成，已在 `.gitignore` 中忽略，**请勿提交**。
- 应用**无鉴权**，默认监听 `0.0.0.0` 仅用于本机 / 局域网演示；请勿直接暴露到公网。
- 计价口径、平台规则、风险词表均为**演示配置**，接入真实经营前请按自家店铺核对。
- 生成的营销内容需人工审核后发布，平台合规与广告法风险由使用者承担。

---

<div align="center">

**如果这个项目对你有帮助，点个 ⭐ Star 是最好的鼓励。**

<sub>Made with FastAPI · SQLite · vanilla JS — 以及一个不厌其烦的启动器</sub>

</div>
