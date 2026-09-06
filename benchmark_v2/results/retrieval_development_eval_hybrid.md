# RAG检索开发集评测

> 这是公开开发集结果，用于改进切块和检索，不能作为最终竞赛成绩。

## 运行配置

- Embedding模型：`qwen3-embedding`
- 生产Top-K：3
- FAISS候选数：10
- Reranker：关闭
- Embedding失败：0
- 用时：30.40秒

## 核心指标

| 指标 | 命中 | Hit Rate | MRR |
|---|---:|---:|---:|
| 严格章节@1 | 37/79 | 46.8% | 0.468 |
| 严格章节@3 | 64/79 | 81.0% | 0.625 |
| 正确文件@1 | 52/79 | 65.8% | 0.658 |
| 正确文件@3 | 75/79 | 94.9% | 0.787 |

## 未命中正确文件的题目

- `q_err_timeout`：CANCELLED DUE TO TIME LIMIT是什么意思？；Top-1为 `107_platform_guide.md > 常见问题与排查 > 作业一直PENDING`
- `q_err_preempted`：作业被PREEMPTED了怎么办？；Top-1为 `scow_platform_guide.md > 常见问题（FAQ） > 作业一直排队`
- `q_cmd_squeue_reason`：squeue的Reason列各种含义是什么？；Top-1为 `107_platform_guide.md > 常见问题与排查 > 作业一直PENDING`
- `q_errformat_02`：TIMEOUT信息写在哪里？.out文件里有吗？；Top-1为 `107_platform_guide.md > 常见问题与排查 > 作业运行失败`

## 找对文件但未命中指定章节

- `q_partition_02`：P107-RTX5090和P107-A100有什么区别？（正确文件排名3）
- `q_submit_01`：怎么写一个GPU训练的sbatch脚本？（正确文件排名1）
- `q_submit_03`：SBATCH参数--gres=gpu:1是什么意思？（正确文件排名3）
- `q_submit_04`：怎么提交数组作业？（正确文件排名1）
- `q_submit_07`：提交前需要检查什么？（正确文件排名2）
- `q_module_02`：怎么查看有哪些module可以加载？（正确文件排名1）
- `q_gpu_01`：怎么确认我的作业分配到GPU了？（正确文件排名1）
- `q_basic_04`：用户有sudo权限吗？（正确文件排名1）
- `q_basic_06`：REST API怎么认证？（正确文件排名1）
- `q_cmd_squeue`：怎么查看我的作业在排队？（正确文件排名3）
- `q_debug_03`：怎么查看作业为什么排队？（正确文件排名2）

## 指标解释

- 严格章节命中要求文件名和章节标题都符合预设答案，适合检查切块质量。
- 正确文件命中只要求来源文件正确，允许同一文档中的相邻章节提供等价信息。
- 检索命中不等于最终回答正确，最终还需结合三组回答对照和真实平台执行结果。
