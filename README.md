# HPC Copilot

HPC Copilot 面向第一次接触学校算力平台的学生，主要解决四类问题：平台使用问答、Slurm 命令解释、作业脚本生成和常见报错诊断。它不训练专用模型，而是把平台规则、知识库检索、确定性诊断和大模型表达组合成一套工作流。


## 目录

```text
backend/
  main.py          REST、WebSocket 和工作流编排
  core/            数据模型、规则诊断、LLM 客户端
  executor/        Web Shell、SSH、Slurm REST 三种平台接入
  agent/           终端监控和可审计的作业模板
  rag/             知识库加载、检索和重排
  config/          报错规则与 RAG 配置
frontend/          React 前端
docs/knowledge/    平台、Slurm 和报错知识库
tests/             当前后端的自动测试
benchmark/         第一版诊断与RAG开发集
benchmark_v2/      三组对照、冻结指纹、客观评分和报告工具
```

## 本地启动

后端：

```powershell
python -m pip install -r backend/requirements.txt
python backend/run.py --dev
```

前端另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。Docker 是可选的交付方式，本地开发不需要 Docker。

## 检查

```powershell
python -m pytest -q
cd frontend
npm run build
```
