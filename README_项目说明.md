# 财报分析 + 关联交易识别 AgentScope Demo

本项目通过 AgentScope 2.0.11 官方 TeamPipeline 按依赖并行及串行运行全部九个技能智能体。
当前数据由 Mock 数据提供层生成，无需启动 MySQL 或执行入库脚本。

完整安装、模型配置、启动与输出说明见 [agentscope_demo/README.md](agentscope_demo/README.md)。

从项目根目录快速运行（需要 Python 3.11+）：

```bash
cd agentscope_demo
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run_demo.py --mode offline --company-id DEMO001 --report-period 2025
```

离线模式通过同一官方管道运行九个本地预览节点，验证依赖及材料传递，不调用业务模型。
实际运行全部 Agent 时，按 Demo README 配置 `.env`，使用 `--mode agentscope`。
请在独立虚拟环境安装依赖；当前 Mock 数据均为虚构数据。

也可以通过页面填写调查参数：

```bash
# 在 agentscope_demo 目录执行
.venv/bin/python web_server.py
```

打开 http://127.0.0.1:8000，填写表单并点击“开始调查”。选择“离线预览”无需模型密钥；
选择“智能体调查”时由服务端读取 `.env`，使用官方 TeamPipeline 按依赖运行全部 Agent。

所有新任务输出统一位于 `agentscope_demo/runs/<任务ID>/`：`reports/` 保存报告，`audit.jsonl` 保存唯一过程审计，`summary.json` 保存结果汇总，`diagnostics/` 保存内部诊断。旧目录仅保留历史资料。
