# financial-report-analysis AgentScope Demo

本目录只实现 `financial-report-analysis` 子智能体，不调用或修改其他 Skill 的执行逻辑。

## 1. 运行链路

```text
模拟信贷系统接口（mock_source.py）
        │ 生成企业、报告期、系统字段代码、金额、来源记录
        ▼
MySQL（financial_statement_values）
        │
        ▼
AgentScope Toolkit 注册的工具
  ├─ query_financial_data：按 company_id / report_period 查询原始科目
  ├─ calculate_financial_indicators：按 Python 工具内置公式计算指标
  └─ get_data_quality_summary：返回缺失字段、口径限制
        │
        ▼
AgentScope 2.x Agent（自主选择并调用 FunctionTool）
        │
        ▼
严格填写 financial-report-template.md，校验后输出客户经理报告
        │
        ├─ reports/：Markdown 报告 + 静态 HTML 页面
        └─ audit/：JSON 审计记录
```

**职责边界：** MySQL 只保存原始字段；Python 计算工具负责确定性算术；大模型负责解释趋势、识别风险、形成综合判断和撰写报告。模型不得编造缺失字段或把近似口径写成精确口径。

## 2. 环境准备

推荐使用独立 Python 3.11 或 3.12 虚拟环境（AgentScope 2.x 要求 Python 3.11+；避免污染你现有的 Anaconda 环境）。

```bash
cd financial-report-analysis/agentscope_demo
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\\Scripts\\activate
python -m pip install -r requirements.txt
cp .env.example .env
```

修改 `.env` 中 MySQL 连接参数。模型通过 AgentScope 2.0.5 的 `OpenAICredential` 和 `OpenAIChatModel` 连接企业部署的 OpenAI 兼容接口，支持企业 DeepSeek 服务。

```dotenv
LLM_BASE_URL=http://企业服务器:端口/v1/chat/completions
LLM_MODEL=企业部署实际对外提供的模型标识
LLM_API_KEY=企业接口密钥
LLM_STREAM=false
```

- 地址支持基础路径 `http://企业服务器:端口/v1` 或完整的 `/v1/chat/completions`，程序自动去除完整地址末尾的 `/chat/completions`，避免重复拼接。
- `LLM_MODEL` 填部署服务要求的 `model` 值，不一定是 `deepseek-chat`，请以企业部署配置为准。
- 默认使用非流式请求；服务支持流式工具调用时可设置 `LLM_STREAM=true`。
- 接口须支持 `tools`、`tool_choice` 和 `tool_calls` 的工具调用交互；仅能普通对话的服务无法直接支持当前自主调用工具的流程。
- 如果部署方明确说明无需鉴权，可以将 `LLM_API_KEY` 填为 `not-required`；客户端仍会发送该占位 Bearer 值。
- 任意 `LLM_*` 配置存在时，企业配置必须完整，程序不会借用旧 OpenRouter 的地址、密钥或模型。
- 完全没有 `LLM_*` 配置时，保留原 `OPENROUTER_API_KEY`、`OPENROUTER_MODEL`、`OPENROUTER_BASE_URL` 的兼容方式。
- 审计记录的 `model` 保存实际传给模型客户端的模型标识；离线模式为 `null`。
- 已有 `.env` 请添加上述配置；首次配置可从 `.env.example` 复制。不要覆盖已有数据库配置或密钥。

## 3. 启动 MySQL

在本目录执行：

```bash
docker compose up -d mysql
```

MySQL 首次启动时自动执行 `db/init.sql`。默认开发数据库为 `financial_demo`，仅供本地 Demo，禁止直接用于生产环境。

## 4. 生成并写入 Mock 接口数据

```bash
python mock_source.py --company-id DEMO001 --company-name 示例制造股份有限公司
python seed_mock_data.py --company-id DEMO001
```

`mock_source.py` 模拟信贷系统返回的接口 payload；`seed_mock_data.py` 将 payload 入库。示例包含 2022—2025 年的三表核心科目和映射表列出的补充字段。数据均为虚构演示数据，不代表真实企业。

也可先检查接口模拟数据：

```bash
python mock_source.py --print-only
```

## 5. 运行财报分析

### 真实 AgentScope 模式（需要配置企业模型接口）

```bash
python run_demo.py --company-id DEMO001 --company-name 示例制造股份有限公司 --report-period 2025
```

AgentScope 2.x Agent 会按 Skill 指令自主调用 MySQL 查询工具、指标计算工具和数据质量工具，再生成分析报告。三个工具统一接收四位年度或空字符串，容忍年度外多传一层引号和首尾空白；格式非法返回 `error`，目标年度不存在返回 `not_found`，不会把空质量检查标记为成功。查询和指标计算仍返回全部年度以支持趋势分析，质量检查按目标年度筛选。

### 离线演示模式（不调用大模型）

```bash
python run_demo.py --mode offline --company-id DEMO001 --company-name 示例制造股份有限公司 --report-period 2025
```

离线模式用于验证 MySQL 查询、字段映射、公式计算、报告落盘和审计链路；它不是大模型分析结果。真实大模型分析请使用默认 `agentscope` 模式。

输出位置：
- `reports/`：同名 `.md` 财报分析报告和 `.html` 静态页面；两种运行模式均自动输出
- `audit/`：工具调用、字段来源、计算结果摘要和执行状态

HTML 使用简约报告版式，内嵌全部样式，双击文件即可离线查看；包含章节目录、宽表格横向滚动、手机适配和打印样式。浏览器中可使用 Ctrl+P / Command+P 打印或另存为 PDF，默认横向 A4 以容纳多年度表格。Markdown 中的原始 HTML 按文本显示，不执行脚本。

已有 Markdown 也可以单独转换，无需数据库或模型：

```bash
python render_report.py reports/DEMO001_2025_20261109_214537.md
# 可选自定义输出位置
python render_report.py reports/mock_preview_2025.md -o reports/preview.html
```

页面模板和样式位于 `templates/report.html`、`templates/report.css`。审计记录保留原有 `report_path`，新增 `html_report_path`。

### 客户经理报告交付规则

最终Markdown严格填写 `../assets/financial-report-template.md`：保留八个章节、全部编号子章节、表格指标行和非年度列。展示最近三个可用年度，缺少资料保留原位置并简述限制；不增加技术列或数据缺口章节。

报告不展示公式、计算过程、数据库表名、取数字段代码或工具执行状态。1.3只保留审计意见、会计政策变更、合并范围变化、数据完整性四项，总计不超过220字。必要的模拟数据、未审计和近似口径提示仍保留。

`tools/report_contract.py` 直接读取原始模板校验输出。先统一年度表头（如 `2025`、`2025 年`、`2025年度`）、编号标题和明确等价的指标名称，再用与HTML渲染器一致的Markdown解析器检查表格；不改写财务数值。大模型报告不符合要求时最多自动修正两次，修正请求附上已填入可用年度的完整模板骨架；仍不合格则报错，不保存为完成报告。主入口在写入Markdown和HTML之前再次校验。校验仅检查结构和展示规范，不代替财务结论复核。

每次生成在 `audit/diagnostics/<本次ID>/` 保存 `attempt_N.raw.md`（原始回复）、`attempt_N.normalized.md`（格式规范化后的正文）、`attempt_N.validation.json`（本次错误）。最终失败会另存失败审计JSON，终端错误包含诊断目录，方便定位，不再丢失模型回复。诊断文件供内部排查，不作为客户经理交付报告。

交付规范化会将明确的数据缺失说明（如“附注未接入数据库”）转为“附注未提供”，将单个数值结果（如“其他应收款/净资产=4.62%”）转为“其他应收款/净资产为4.62%”。数值、缺失提示和风险判断保留；真实计算表达式和系统字段代码仍拦截。错误记录会包含命中行号和文字，供模型针对性修正，避免只返回笼统错误。

内部审计单独留存原始财务记录、完整计算结果（含公式和来源字段）、工具事件和报告校验结果。离线模式同样使用原模板生成排版预览，未完成的分析明确标注，不虚构结论。历史报告不会因更新代码而自动重写。

## 6. 入参说明

| 参数 | 必需 | 说明 |
|---|---|---|
| `company_id` | 是 | 信贷系统/客户主数据中的企业唯一 ID；不是企业名称 |
| `company_name` | 建议 | 报告展示名称 |
| `report_period` | 是 | 年度报告期，如 `2025` |
| `analysis_scenario` | 否 | 贷前尽调/贷后检视/风险预警，默认贷前尽调 |
| `operator` | 否 | 客户经理姓名或工号 |

`company_id` + `report_period` 是查询主键。企业名称只用于报告展示，不应用来代替唯一 ID 查询。

## 7. 从 Mock 切换真实系统

只替换数据接入适配层，不需要改 Agent 的分析流程：

1. 保留数据库字段表结构，或按真实信贷库结构调整 `db/mysql_client.py` 的 SQL。
2. 将 `mock_source.py` / `seed_mock_data.py` 替换为真实接口适配器，把真实接口返回的系统字段代码、企业 ID、报告期、金额和来源记录写入 MySQL。
3. 确认生产接口返回的是详细字段代码（SY02、ZCFZ38 等）还是简表代码（B-SYJB02 等）；本 Demo 查询和计算统一使用 Excel 中的详细字段代码，不混用两套代码。
4. 对 `SY31`、`ANNEX`、受限资金、关联方其他应收款、维持性资本开支等 Excel 标记的待确认字段，先与系统接口方确认；未返回就保持 unavailable，不要用其他字段冒充。
5. 将 `.env` 的数据库连接切换为受控的正式连接，使用只读账号查询；生产凭据通过密钥管理系统注入，不提交到代码仓库。

## 8. 关键设计说明

- 原始字段对接参考上传的 Excel；实际计算规则统一在 `tools/indicator_calculator.py` 中维护。
- `SY31` 优先、`SY30` 备选；使用 SY30 时必须在报告中注明净利润口径。
- ROE 当前只能用 SY31 归母净利润作为可用代理，不得声称是扣非归母 ROE。
- 应收/存货周转天数按 Skill 公式使用 365 天；不直接使用系统 360 天 INDEX37/38。
- 流动比率、速动比率按倍数输出，不乘 100。
- DSCR 使用 XJ32 总资本开支近似维持性资本开支，必须标注“近似口径/待确认”；ANNEX07 的期间和“应还”口径也需确认。
- 关联资金占用率缺少关联方其他应收款拆分，工具返回 unavailable，不会拿 ZCFZ13 总额冒充关联方金额。
- 行业基准、审计意见、报表附注、客户/供应商集中度等当前映射表没有完整数据库字段，因此报告必须注明未接入，不能虚构分析结果。

### 指标规则的维护位置

- `tools/indicator_calculator.py`：维护实际计算公式、字段选择/回退规则、缺失处理和输出单位。工具结果中的 `formula`、`source_field_codes`、`note` 供内部分析与审计使用；客户经理报告只呈现指标结果和必要的业务口径说明。
- `config/financial_indicator_mapping.json`：只保留 `category`、`indicator`、`field_mapping`、`source`、`limitations`，供数据对接和人工查阅；不维护 `formula` 或 `calculation`，运行时不读取它执行计算。
- `config/system_field_dictionary.json`：供数据对接查阅的字段字典，目前不参与运行时动态映射。
- `references/financial-indicators-calculation.md` 和原始 Excel 是方法与来源参考；实际执行以 Python 工具为准。

修改计算规则时，修改 `indicator_calculator.py` 并验证对应测试；只有对接字段或业务限制发生变化时，才同步调整 JSON。不要把公式复制回 JSON。审计中的 `calculation_file` 标明计算实现，`mapping_file_role: reference_only` 表示映射文件仅为参考。历史报告和审计记录保留原样。

## 9. 测试

```bash
pytest -q
```

当前环境未提供真实 MySQL 服务和真实 LLM Key 时，不能声称已经完成真实外部系统联调。可以先用 Docker MySQL + Mock 数据完成端到端演示。


## 10. AgentScope 2.x 迁移说明

本 Demo 已将原 1.x 调用方式迁移到 AgentScope 2.0.5：
- `AgentScope 2.x Agent` → `Agent`
- 当前模型接入：`OpenAIChatModel(credential=OpenAICredential(...), model=..., client_kwargs={"base_url": ...})`，连接企业 OpenAI 兼容接口。
- `Toolkit.register_tool_function(...)` → `Toolkit(tools=[FunctionTool(...)])`
- `Msg(...)` 与 `agent(...)` → `UserMsg(...)` 与 `await agent.reply(...)`
- 不再依赖 `InMemoryMemory`、`DashScopeChatFormatter` 和 1.x `ToolResponse` 返回类型。

建议在新虚拟环境中安装 `requirements.txt`。不要在原有 base 环境中混装 AgentScope 1.x 和 2.x。MCP 依赖由 AgentScope 2.0.5 的依赖声明解析，不需要为了这套 Demo 手动固定 MCP 2.3.0。
