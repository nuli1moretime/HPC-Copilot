# HPC Copilot

HPC Copilot 面向第一次接触学校算力平台的学生，主要解决四类问题：平台使用问答、Slurm 命令解释、作业脚本生成和常见报错诊断。它不训练专用模型，而是把平台规则、知识库检索、确定性诊断和大模型表达组合成一套工作流。

当前目录是整理后的 FastAPI + React 版本。旧的 Streamlit 原型和重复的 `src/hpc_copilot` 实现已经移除，后端代码统一放在 `backend` 中。

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

## 评测

`benchmark/` 中的已有题目全部按公开开发集处理。`benchmark_v2/` 用于同一模型下的三组对照实验，并把正式测试前后的代码和知识库内容固定下来。先阅读 `benchmark_v2/README.md`，不要把开发集100%通过率当成最终泛化成绩。

## 凭证处理

Slurm Token、SSH 密码与私钥、Web Shell Cookie 和大模型 API Key 不写入浏览器 `localStorage`，后端也不会把它们保存到项目目录。后端重启后需要重新填写这些敏感字段。不要把 `.env`、私钥或真实 Cookie 提交到仓库。

## 当前边界

对话负责答疑、解释和生成建议；真正执行集群命令的入口是终端与预设作业模板。模板现有“第一个 Slurm 作业”“经典 CUDA 向量加法”和“t² 火灾增长与喷淋响应”。火灾模板用自包含的工程关联式算例体现学科交叉，不依赖外部专业软件；平台报错诊断仍只处理 Slurm、环境与资源层问题。

当前运行时连接仍按单用户演示环境设计。如果要部署成多人同时使用的公共服务，应先把全局连接配置和执行器改成按会话隔离。
