# 财报分析 + 关联交易识别 AgentScope Demo

本目录注册全部 9 个技能 Agent，编排依赖集中定义于 `orchestration/analysis_pipeline.py` 顶部的 `NODES`。
在线模式使用 **AgentScope 2.0.11 官方 TeamPipeline** 启动全部成员；无上游者立即运行，其余成员等待所有必需上游完成，接收对应报告后运行。每个节点输出独立 Markdown，并汇总为九个报告 Tab 和审计记录。
当前业务数据全部来自 mock；“在线”指调用真实模型，不表示已连接真实业务系统。

## 在页面填写参数并开始调查

安装依赖后，在 `agentscope_demo` 目录执行：

```bash
.venv/bin/python web_server.py
```

打开 **http://127.0.0.1:8000**，填写企业 ID、企业名称、财报年度、调查场景与操作人，
选择“智能体调查”或“离线预览”，点击“开始调查”。业务参数由表单提交，不再需要修改 Python 或拼接命令行参数。
当前 mock 财报支持 2022—2025，可用 `DEMO001` / `2025` 做首次离线验证。
智能体调查需要先在本目录 `.env` 中配置模型；密钥不会下发浏览器。

页面开始调查后切换为实时工作台：左侧显示并行智能体，中间显示选中智能体的实际执行步骤，
右侧为可导出的结构化审计日志，记录操作人、企业、技能、输入摘要指纹、来源覆盖、工具结果指纹、步骤结果、校验与异常；支持当前智能体和全部调查两个范围。
开始、结束、修正等记录按时间完整保留，不用最新状态覆盖历史。
预定步骤先显示等待，收到真实事件后才切换状态；未执行的步骤明确标注，不虚增进度。
页面同时展示调查企业、报告期、事件连接状态、最近活动时间及当前步骤等待时长。
中间列的业务步骤直接读取各 Skill 的 `### 步骤` 定义，并逐步发起模型任务；
收到该步的结构化结果后才更新状态。结果摘要明确标为模型摘要，资料不足与不适用单独标记，不能当作独立核验成功。
最终报告在步骤结束后汇总生成并校验，报告修正不会重跑所有业务步骤。
这比原先一次性生成报告需要更多模型调用，耗时与模型用量可能增加。
离线模式明确跳过模型业务步骤，只运行本地预览。
不模拟百分比或展示模型内部思考。完成后嵌入合并 HTML，并支持分别下载 Markdown，可点击“新建调查”回到表单。

事件通过 `/api/jobs/<任务ID>/events` 的 SSE 接口推送，支持事件编号断线续传及当前服务内重放。
代码更新后请先用 Ctrl+C 停止旧服务，再重新运行 `web_server.py` 并刷新页面，否则旧进程不会加载新的事件接口。

网页与命令行统一使用 `runs/<任务ID>/`，每次任务只有一份过程审计：

```text
runs/<任务ID>/
├── reports/        九份 Markdown 和合并 HTML
├── audit.jsonl     唯一过程审计，供页面回放和导出
├── summary.json    最终状态、输入摘要、报告路径和计数
└── diagnostics/   按技能分目录保存原始回复及校验错误
```

页面订阅和导出从同一审计记录转换，不再额外保存 `progress.jsonl`；
`summary.json` 不复制完整工具、步骤和校验日志。网页任务 ID、审计任务 ID 与目录名一致。
刷新当前页面可恢复当前服务内任务；服务重启后不恢复内存任务，文件仍保留在上述目录。
旧 `web_runs/`、根目录 `audit/` 和 `reports/` 仅保留历史资料，新任务不再向这些位置写入。
浏览器断开不会中止调查。

端口占用时可用 `.venv/bin/python web_server.py --port 8001`，然后打开相应地址。
这是本机 Python 服务，默认只监听 `127.0.0.1`；直接双击 `web/index.html` 无法运行 Python Agent。
按 Ctrl+C 停止接收新请求；若调查仍在执行，进程会等待该任务结束后退出。
原有命令行入口保留，使用方式如下。

失败详情请查看服务终端及对应任务审计文件。

## 1. 运行链路与目录

```text
开始 ─┬─ 行业分析 → 尽调主引擎 → 股权穿透 → 财报分析 ─┬─ 关联交易识别与风险排查
      ├─ 信贷政策环境分析                              └─ 信贷风险结构化提取
      ├─ 风险逆向思维链
      └─ 反欺诈多模态交叉验证
```

图外的建议性 Skill 上下游不会被自动添加。任一节点失败会取消本次调查其余任务；未满足依赖的下游不会启动。
内部 `run_single_skill(key, AnalysisRequest(..., user_materials={...}))` 仅运行指定技能，不补跑上下游；页面暂不提供单技能入口。



- `orchestration/agent_factory.py`：创建并运行财报及关联交易专用 Agent，适配工具协议，执行报告校验与重试。
- `orchestration/analysis_pipeline.py`：注册 Agent 分支，通过官方 `TeamPipeline` 调度。
- `orchestration/model_settings.py`：读取并检查模型地址、模型名称和密钥。
- `data_provider.py` / `mock_source.py`：统一获取财报与关联交易模拟数据。
- `tools/financial_tools.py` / `tools/related_party_tools.py`：两个 Agent 各自的工具箱。
- `../financial-report-analysis/` / `../credit-related-party-detection/`：技能、业务规则和报告模板。
- `runs/<任务ID>/`：任务报告、唯一过程审计、汇总及诊断。

**职责边界：** 数据提供层获取数据，Python 工具计算指标，业务模型解释趋势与风险并撰写报告。
调度者使用本地固定路由，不额外调用远程模型；不同业务 Agent 各自持有工具箱和上下文。
离线模式同样通过 TeamPipeline 验证依赖图和材料传递，九个节点运行本地预览函数，不调用业务模型。

## 2. 环境准备

推荐使用独立 Python 3.11 或 3.12 虚拟环境（AgentScope 2.x 要求 Python 3.11+；避免污染你现有的 Anaconda 环境）。

```bash
# 从项目根目录进入；当前结构是根目录下直接放 agentscope_demo
cd agentscope_demo
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# 仅首次配置时复制，避免覆盖已有 .env
# macOS / Linux：
test -f .env || cp .env.example .env
# Windows PowerShell：if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

模型通过 AgentScope 2.0.11 的 `OpenAICredential` 和 `OpenAIChatModel` 连接企业部署的 OpenAI 兼容接口，支持企业 DeepSeek 服务。

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
- 已有 `.env` 请添加上述配置；首次配置可从 `.env.example` 复制。

## 3. 数据准备

本 Demo 默认通过 `data_provider.py` 内部调用 `mock_source.py` 自动生成虚拟测试数据，**无需**启动 MySQL 或执行入库脚本。示例包含 2022—2025 年的三表核心科目和映射表列出的补充字段。数据均为虚构演示数据，不代表真实企业。

可单独查看接口模拟数据：

```bash
python mock_source.py --print-only
```

## 4. 启动全部智能体并查看报告

### 真实 AgentScope 模式（需要配置企业模型接口）

```bash
python run_demo.py --mode agentscope --company-id DEMO001 --company-name 示例制造股份有限公司 --report-period 2025
```

此命令默认按依赖运行全部九个技能，不需要分别启动多个进程；命令行方式无需 Web 服务。
`--report-period` 用于财报分析；当前关联交易分支按企业与排查场景执行，没有单独的报告期参数。

财报 Agent 按 Skill 指令调用查询工具、指标计算工具和数据质量工具，再生成分析报告。三个工具统一接收四位年度或空字符串，容忍年度外多传一层引号和首尾空白；格式非法返回 `error`，目标年度不存在返回 `not_found`，不会把空质量检查标记为成功。查询和指标计算仍返回全部年度以支持趋势分析，质量检查按目标年度筛选。

### 首次验证：离线演示模式（不需要模型密钥）

```bash
python run_demo.py --mode offline --company-id DEMO001 --company-name 示例制造股份有限公司 --report-period 2025
```

离线模式用于验证数据获取、字段映射、公式计算、报告落盘和审计链路；它不是大模型分析结果。真实大模型分析请使用默认 `agentscope` 模式。

成功后终端打印四个文件的绝对路径。两种模式均会生成：

| 目录 | 文件 | 内容 |
|---|---|---|
| `runs/<任务ID>/reports/` | `<企业ID>_<年度>_<时间戳>_<节点key>.md` | 九个技能各自的报告 |
| `runs/<任务ID>/reports/` | `<企业ID>_<年度>_<时间戳>.html` | 全部技能报告的合并页面 |
| `runs/<任务ID>/` | `audit.jsonl` | 唯一过程审计（逐条追加） |
| `runs/<任务ID>/` | `summary.json` | 结果汇总及报告路径，不重复完整过程日志 |

macOS 可复制终端打印的 HTML 路径后执行 `open "完整路径"`，或直接在文件管理器中双击。

HTML 使用简约报告版式，内嵌全部样式，双击文件即可离线查看；包含章节目录、宽表格横向滚动、手机适配和打印样式。浏览器中可使用 Ctrl+P / Command+P 打印或另存为 PDF，默认横向 A4 以容纳多年度表格。Markdown 中的原始 HTML 按文本显示，不执行脚本。

已有 Markdown 也可以单独转换：

```bash
python render_report.py runs/任务ID/reports/实际生成的_financial.md
# 可选自定义输出位置
python render_report.py runs/任务ID/reports/实际生成的_related_party.md -o runs/任务ID/reports/preview.html
```

页面模板和样式位于 `templates/report.html`、`templates/report.css`。合并页面由 `tools/report_renderer.py` 的 `render_combined_html` 生成。
审计路径字段为 `financial_report_path`、`related_party_report_path`、`html_report_path`。

### 客户经理报告交付规则

财报 Markdown 严格填写 `../financial-report-analysis/assets/financial-report-template.md`：保留八个章节、全部编号子章节、表格指标行和非年度列。展示最近三个可用年度，缺少资料保留原位置并简述限制；不增加技术列或数据缺口章节。

报告不展示公式、计算过程、数据库表名、取数字段代码或工具执行状态。1.3只保留审计意见、会计政策变更、合并范围变化、数据完整性四项，总计不超过220字。必要的模拟数据、未审计和近似口径提示仍保留。

`tools/report_contract.py` 直接读取原始模板校验输出。先统一年度表头（如 `2025`、`2025 年`、`2025年度`）、编号标题和明确等价的指标名称，再用与HTML渲染器一致的Markdown解析器检查表格；不改写财务数值。大模型报告不符合要求时最多自动修正两次，修正请求附上已填入可用年度的完整模板骨架；仍不合格则报错，不保存为完成报告。主入口在写入Markdown和HTML之前再次校验。校验仅检查结构和展示规范，不代替财务结论复核。

每次生成在 `runs/<任务ID>/diagnostics/<技能key>/` 保存 `attempt_N.raw.md`（原始回复）、`attempt_N.normalized.md`（格式规范化后的正文）、`attempt_N.validation.json`（本次错误）。最终失败统一在该任务的 `summary.json` 中记录失败状态，终端错误包含诊断目录，方便定位，不再丢失模型回复。诊断文件供内部排查，不作为客户经理交付报告。

交付规范化会将明确的数据缺失说明（如“附注未接入数据库”）转为“附注未提供”，将单个数值结果（如“其他应收款/净资产=4.62%”）转为“其他应收款/净资产为4.62%”。数值、缺失提示和风险判断保留；真实计算表达式和系统字段代码仍拦截。错误记录会包含命中行号和文字，供模型针对性修正，避免只返回笼统错误。

内部审计单独留存原始财务记录、完整计算结果（含公式和来源字段）、工具事件和报告校验结果。离线模式同样使用原模板生成排版预览，未完成的分析明确标注，不虚构结论。历史报告不会因更新代码而自动重写。

关联交易报告使用独立模板 `../credit-related-party-detection/assets/related-party-report-template.md` 和
`tools/related_party_contract.py` 校验，最多同样执行初次生成和两次修正。
诊断文件为 `rp_attempt_N.raw.md` 和 `rp_attempt_N.validation.json`。
当前征信接口未接入，报告必须披露限制。离线关联交易预览不经过该模型报告校验重试流程。

## 5. 入参说明

所有参数均为命令行选项，名称使用连字符：

| 参数 | 必需 | 说明 |
|---|---|---|
| `--mode` | 否 | `agentscope`（默认，运行九个 Agent）或 `offline`（生成九份节点预览） |
| `--company-id` | 是 | 企业唯一 ID，如 `DEMO001` |
| `--company-name` | 否 | 企业展示名称；省略时使用数据提供层返回的名称 |
| `--report-period` | 是 | 财报年度，如 `2025`；当前 mock 支持 2022—2025 |
| `--scenario` | 否 | 分析场景，默认 `贷前尽调` |
| `--operator` | 否 | 操作人，默认 `DEMO_OPERATOR` |

查看实际参数：`python run_demo.py --help`。

## 6. 从 Mock 切换真实接口

只替换数据接入适配层，不需要改 Agent 的分析流程：

1. 修改 `data_provider.py` 中的 `get_financial_data` 函数，将 `build_payload` 替换为真实接口的 HTTP 请求（如 `httpx.get(...)`）。
2. 确认生产接口返回的结构与 `mock_source.build_payload()` 一致（包含 `company` 和 `records` 字段）。
3. 确认生产接口返回的是详细字段代码（SY02、ZCFZ38 等）还是简表代码（B-SYJB02 等）；本 Demo 查询和计算统一使用 Excel 中的详细字段代码，不混用两套代码。
4. 对 `SY31`、`ANNEX`、受限资金、关联方其他应收款、维持性资本开支等 Excel 标记的待确认字段，先与接口方确认；未返回就保持 unavailable，不要用其他字段冒充。

关联交易接入点为 `data_provider.get_related_party_data()`，需保持 `company`、`customer_info`、
`equity_graph`、`related_parties`、`transactions`、`abnormal_features`、`red_flags`、
`data_source_availability` 等聚合字段契约。
工商、股权图谱、客户信息、交易流水和知识库的接入边界见
`../credit-related-party-detection/SKILL.md`；当前没有独立知识库查询工具，征信也尚未接入。

## 7. 关键设计说明

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
- `../financial-report-analysis/references/financial-indicators-calculation.md` 和原始 Excel 是方法与来源参考；实际执行以 Python 工具为准。

修改计算规则时，修改 `indicator_calculator.py` 并验证对应测试；只有对接字段或业务限制发生变化时，才同步调整 JSON。不要把公式复制回 JSON。审计中的 `calculation_file` 标明计算实现，`mapping_file_role: reference_only` 表示映射文件仅为参考。历史报告和审计记录保留原样。

## 8. 测试

```bash
python -m pytest tests -q
```

测试使用替身或本地调度，不请求真实业务模型。离线运行通过也不表示企业模型联调通过。

常见启动问题：

- `No module named agentscope`：确认已激活本目录 `.venv`，并执行安装依赖命令。
- 找不到 `TeamPipeline`：执行 `python -m pip install -r requirements.txt`，确认安装的是 2.0.11。
- 企业模型配置缺失：检查本目录 `.env`，任一 `LLM_*` 存在时需配齐地址、模型和密钥。
- 模型连接或工具调用失败：核对企业网络、地址、鉴权，以及服务是否支持工具调用。
- 报告校验失败：查看 `runs/<任务ID>/diagnostics/` 和 `summary.json`，不要把失败报告当作交付结果。


## 9. AgentScope 2.x 迁移说明

本 Demo 已将原 1.x 调用方式迁移到 AgentScope 2.0.11：
- `AgentScope 2.x Agent` → `Agent`
- 当前模型接入：`OpenAIChatModel(credential=OpenAICredential(api_key=..., base_url=...), model=...)`，连接企业 OpenAI 兼容接口。
- `Toolkit.register_tool_function(...)` → `Toolkit(tools=[FunctionTool(...)])`
- `Msg(...)` 与 `agent(...)` → `UserMsg(...)` 与 `await agent.reply(...)`
- 不再依赖 `InMemoryMemory`、`DashScopeChatFormatter` 和 1.x `ToolResponse` 返回类型。

建议在新虚拟环境中安装 `requirements.txt`。不要在原有 base 环境中混装 AgentScope 1.x 和 2.x。MCP 依赖由 AgentScope 2.0.11 的依赖声明解析，不需要为了这套 Demo 手动固定 MCP 版本。

## 多 Agent 并行与串行编排

- `analysis_pipeline.py`：顶部 `SkillNode/NODES/REGISTRY` 定义全部九个节点、数据接口和必需上游；下方通过官方 TeamPipeline 启动和管理成员，按依赖等待，无整层屏障。
- `execution_context.py`：将用户材料与必需上游报告送入业务 Agent 的提示词，按任务隔离。
- `agent_factory.py`：全部九个 Agent 的统一工厂，包括两个专用构建实现、其余七个配置式构建实现、技能/参考文件加载和工具注册。
- `data_provider.py`：每个技能一个独立接口函数，当前均由 `mock_source.py` 构造数据。

网页点击开始调查运行整张图，展示九个节点及依赖等待状态，并可下载每个节点报告。
`summary.json` 中的 `agents` 记录各节点的 Skill、上游、状态、调用计数和报告路径；完整步骤、工具与校验事件仅记录在 `audit.jsonl`。
反欺诈节点当前没有原始图片，报告必须注明未进行视觉核验；政策样例不代表实时监管政策。
七个新 Agent 的通用校验检查正文、全称、模拟数据声明和取数工具调用，财报与关联交易仍使用各自专用交付契约。

新增节点时在注册表、数据提供层和必要的专用报告校验器中扩展；页面列表和报告下载会自动读取注册表。


## 首轮报告生成与格式约束

分析 Skill 的步骤说明、教学示例、内部审计格式和客户交付模板分开处理。
`tools/report_instructions.py` 在加载提示词时移除内部审计及教学示例，关系图使用纯 Markdown 表格。
最终报告使用独立的写作会话，复用模型及工具但不复用步骤会话的 JSON 回复历史；
输入保留步骤结果、来源资料和计算结果，不重新生成已完成的业务步骤。
财报在首轮就提供目标年度及以前最近三年的实际模板骨架，交付快照同步筛选年度，原始分析数据不变。
关联交易模板不包含程序审计章节，程序审计独立落在审计文件和页面中。
报告校验会去重并指出违规代码所在行；只有校验未通过才进入修正，不保证任意模型始终首轮成功。


步骤结果优先通过 AgentScope 的 `structured_schema` 取得并严格校验状态、摘要与引用；
兼容接口返回的前置说明/JSON围栏，并对未转义引号等JSON语法进行本地恢复，恢复方法记录在审计中。
`insufficient_data` 是有效业务结果，页面显示“资料不足”，不会因该状态让整图失败。
缺少状态、非法状态、非文本摘要等仍会失败，不能把无效结果补成“通过”。
原始回复保留在当前任务 `diagnostics/`，取消信号（包括SDK返回的interrupted消息）统一作为取消处理，不进入格式修正。
