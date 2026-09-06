# RAG 模块设计构想

## 整体架构

```
用户提问
  │
  ▼
─────────────────────┐
│  1. Query Embedding  │  ← 调用 qwen3-embedding 将问题转向量
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  2. FAISS 向量检索   │  ← 在本地索引中找最相似的文档块（top-10）
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  3. Reranker 重排序  │  ← qwen3-reranker 精排，取 top-3（可选）
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  4. Prompt 注入      │  ← 将 3 段相关文档拼入 system prompt
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  5. LLM 生成回答     │  ← deepseek-v4-pro 基于真实文档回答
└─────────────────────┘
           ▼
        最终回复
```

## 核心设计原则

1. **规则定事实，RAG 补知识** — 错误诊断仍走规则引擎（确定性），RAG 只用于对话问答场景（LLM 生成解释/回答问题时注入平台知识）
2. **离线建索引，在线只检索** — 文档切块和 embedding 在启动时一次性完成，用户提问时只做向量检索（毫秒级）
3. **可降级** — 如果 embedding API 不可用，自动降级为关键词匹配（BM25），不影响系统可用性
4. **可观测** — 每次检索记录命中的文档块和相似度分数，方便调试和评测

## 文件结构

```
backend/
├── rag/                          # RAG 模块（新增）
│   ├── __init__.py
│   ├── loader.py                 # 文档加载与切块
│   ├── embedding.py              # Embedding API 封装
│   ├── vector_store.py           # FAISS 向量索引
│   ├── reranker.py               # Reranker 精排（可选）
│   ├── retriever.py              # 检索编排器（对外接口）
│   └── prompts.py                # RAG 相关的 prompt 模板
├── core/
│   ── llm_client.py             # 现有 LLM 客户端（需微调以支持 RAG prompt）
├── main.py                       # 需修改：chat_ws 中注入检索上下文
└── config/
    ── rag_config.yaml           # RAG 配置（模型、chunk 大小、top-k 等）

data/
├── docs/                         # 知识库文档（从 docs-main 复制过来）
│   ├── basics/
│   ├── guides/
│   └── overview/
└── rag_index/                    # 持久化的 FAISS 索引（启动时自动构建）
    ├── index.faiss
    └── metadata.json
```

## 各模块职责

### 1. loader.py — 文档加载与切块

**输入：** `data/docs/` 下的所有 .md 文件
**输出：** `List[DocChunk]`，每个 chunk 包含：
```python
@dataclass
class DocChunk:
    id: str              # 唯一标识，如 "basics/jobs.md#提交任务"
    text: str            # 块文本（200-500 tokens）
    source_file: str     # 来源文件
    heading: str         # 所属标题（如 "## 交互式任务与批处理任务"）
    metadata: dict       # frontmatter 中的元数据
```

**切块策略：**
- 按二级标题（`##`）切分，每个二级标题下的内容作为一个 chunk
- 如果单个 chunk 超过 500 tokens，按三级标题（`###`）再切
- 保留 frontmatter 中的 `page_type`、`audience` 等元数据，用于后续过滤

**预估产出：** 约 40-60 个 chunk（20 个文件 × 平均 2-3 个二级标题）

### 2. embedding.py — Embedding 服务

```python
class EmbeddingService:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本转向量，调用 qwen3-embedding API"""

    async def embed_query(self, text: str) -> list[float]:
        """单条查询转向量"""
```

- 调用 `https://api.llm.ustc.edu.cn/v1/embeddings`
- 支持批量请求（减少 API 调用次数）
- 内置缓存（相同文本不重复调用）

### 3. vector_store.py — FAISS 向量索引

```python
class VectorStore:
    def build(self, chunks: list[DocChunk], embeddings: list[list[float]]):
        """构建 FAISS 索引（Inner Product = cosine similarity）"""

    def search(self, query_vec: list[float], top_k: int = 10) -> list[SearchResult]:
        """向量检索，返回 top-k 个最相似的 chunk"""

    def save(self, path: str):
        """持久化索引到磁盘"""

    def load(self, path: str):
        """从磁盘加载索引"""
```

- 使用 `faiss.IndexFlatIP`（归一化后 IP = cosine similarity）
- 启动时检查磁盘是否有缓存索引，有则直接加载，无则重新构建
- 40-60 个 chunk 的索引大小 < 1MB，加载时间 < 10ms

### 4. reranker.py — 重排序（可选）

```python
class Reranker:
    async def rerank(
        self, query: str,
        chunks: list[DocChunk],
        top_n: int = 3
    ) -> list[DocChunk]:
        """用 qwen3-reranker 对候选 chunk 精排"""
```

- 输入：query + top-10 候选 chunk
- 输出：精排后的 top-3
- 如果 API 不可用，跳过此步，直接返回 FAISS 的 top-3

### 5. retriever.py — 对外接口

```python
class Retriever:
    async def retrieve(self, query: str, top_k: int = 3) -> str:
        """
        完整检索流程：
        1. embed query
        2. FAISS search (top-10)
        3. rerank (top-3)
        4. 格式化为 prompt 上下文字符串
        """
```

返回格式示例：
```
【平台文档参考】
--- 来源: basics/jobs.md > 交互式任务与批处理任务 ---
平台上的正式计算任务建议通过 Slurm 提交...
（交互式任务适合短时间调试...）

--- 来源: basics/slurm.md > 常用命令 ---
srun -p Students --qos=qos_stu_default -c 1 -t 00:10:00 --pty bash
...
```

### 6. prompts.py — Prompt 模板

```python
RAG_SYSTEM_PROMPT_TEMPLATE = """
你是 HPC Copilot，帮助算力平台初学者的智能体。
...（现有 prompt 内容）...

【平台文档参考 — 以下信息来自平台官方文档，请优先基于此回答】
{retrieved_context}

【回答规则】
- 优先引用上述文档中的信息
- 如果文档中没有相关信息，诚实说明
- 给出可以直接复制粘贴的命令
...
"""
```

## 与现有系统的集成点

### 修改 `backend/main.py` 的 chat_ws

```python
# 现有逻辑：
# system_prompt = CHAT_SYSTEM_PROMPT

# 修改为：
if retriever and use_rag:
    context = await retriever.retrieve(user_message, top_k=3)
    system_prompt = build_rag_prompt(CHAT_SYSTEM_PROMPT, context)
else:
    system_prompt = CHAT_SYSTEM_PROMPT
```

### 配置开关

在 `connection_config.json` 或 `rag_config.yaml` 中加一个 `enable_rag: true/false` 开关，方便演示时对比有/无 RAG 的效果。

## 启动流程

```
应用启动
  │
  ├── 1. 加载 data/docs/ 下所有 .md 文件
  ├── 2. 按标题切块 → 得到 ~50 个 DocChunk
  ├── 3. 检查 data/rag_index/ 是否有缓存
  │     ├── 有 → 直接加载 FAISS 索引 + metadata
  │     └── 无 → 调用 qwen3-embedding 批量向量化 → 构建 FAISS → 持久化
  └── 4. Retriever 就绪，等待用户提问
```

首次启动耗时：约 5-10 秒（50 个 chunk 的 embedding API 调用）
后续启动耗时：< 100ms（直接加载缓存索引）

## 评测方案（与 RAG 同步建设）

### 检索质量评测
- 准备 20 条"问题 → 期望命中文档"的映射
- 指标：top-3 命中率、MRR（Mean Reciprocal Rank）

### 回答质量对比评测
- 同一组问题，分别用"无 RAG"和"有 RAG"跑两次
- 指标：准确性、幻觉率、完整性（LLM-as-judge 打分）

## 依赖

```
faiss-cpu          # 向量检索（CPU 版，无需 GPU）
httpx              # 已有，用于调用 embedding/reranker API
pyyaml             # 已有，用于配置文件
```

新增依赖只有 `faiss-cpu`，约 30MB，安装简单。
