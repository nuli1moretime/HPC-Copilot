# HPC Copilot Benchmark 报告：hpc-copilot-final-v1

- 数据集状态：`frozen`
- 模型：`deepseek-v4-flash`
- 温度：`0.0`

## 汇总

| 系统 | 总分 | 诊断Macro-F1 | 禁止项命中 | 平均延迟/秒 | 完成题数 |
|---|---:|---:|---:|---:|---:|
| llm_only | 80.61 | 0.46 | 1 | 6.01 | 30/30 |
| llm_rag | 92.56 | 1.00 | 3 | 8.89 | 30/30 |
| hpc_copilot | 92.42 | 1.00 | 2 | 11.66 | 30/30 |

> `hpc_copilot` 在这份离线对照中只对诊断题额外加入确定性规则结果；其他题与 `llm_rag` 的输入相同，分数差异可能来自模型生成波动，不能解释为工具调用增益。真正的端到端增益需通过后续真实平台提交、状态跟踪和输出验证衡量。

## 分题型得分

| 系统 | 命令解释 | 报错诊断 | 平台问答 | 脚本生成 |
|---|---:|---:|---:|---:|
| llm_only | 81.95 | 68.75 | 79.17 | 92.92 |
| llm_rag | 100.00 | 93.75 | 83.33 | 95.00 |
| hpc_copilot | 100.00 | 93.75 | 86.46 | 91.35 |

## 未通过的客观检查

### llm_only

- `final_qa_003`：得分 66.67；未通过 concept_2；禁止项 0
- `final_qa_006`：得分 50.00；未通过 concept_2；禁止项 0
- `final_qa_007`：得分 66.67；未通过 concept_2；禁止项 0
- `final_qa_008`：得分 50.00；未通过 concept_1；禁止项 0
- `final_cmd_005`：得分 25.00；未通过 concept_1；禁止项 1
- `final_cmd_006`：得分 66.67；未通过 concept_2；禁止项 0
- `final_script_004`：得分 60.00；未通过 required_6, required_7, required_8, required_9；禁止项 0
- `final_script_007`：得分 83.33；未通过 required_5；禁止项 0
- `final_diag_001`：得分 50.00；未通过 error_type；禁止项 0
- `final_diag_002`：得分 75.00；未通过 concept_1；禁止项 0
- `final_diag_003`：得分 50.00；未通过 error_type；禁止项 0
- `final_diag_006`：得分 50.00；未通过 error_type；禁止项 0
- `final_diag_007`：得分 50.00；未通过 error_type；禁止项 0
- `final_diag_008`：得分 75.00；未通过 concept_2；禁止项 0

### llm_rag

- `final_qa_003`：得分 41.67；未通过 concept_2；禁止项 1
- `final_qa_006`：得分 50.00；未通过 concept_2；禁止项 0
- `final_qa_008`：得分 75.00；未通过 无；禁止项 1
- `final_script_004`：得分 60.00；未通过 required_6, required_7, required_8, required_9；禁止项 0
- `final_diag_005`：得分 75.00；未通过 无；禁止项 1
- `final_diag_008`：得分 75.00；未通过 concept_2；禁止项 0

### hpc_copilot

- `final_qa_003`：得分 41.67；未通过 concept_2；禁止项 1
- `final_qa_006`：得分 50.00；未通过 concept_2；禁止项 0
- `final_script_001`：得分 87.50；未通过 required_8；禁止项 0
- `final_script_004`：得分 60.00；未通过 required_6, required_7, required_8, required_9；禁止项 0
- `final_script_007`：得分 83.33；未通过 required_5；禁止项 0
- `final_diag_002`：得分 75.00；未通过 concept_1；禁止项 0
- `final_diag_005`：得分 75.00；未通过 无；禁止项 1

## 解释边界

- 该报告的自动分数来自预先固定的关键词、正则和错误类别，不使用大模型裁判。
- 脚本题的自动分数只代表结构和关键参数正确，不能替代真实平台提交结果。
- 正式报告还必须合并每个脚本的提交状态、最终状态、退出码和关键输出。
- 这是针对107算力平台的项目组自建评测，不应表述为第三方公开Benchmark。
