# HPC Copilot Benchmark v2

这套评测用于比较“仅大模型”“大模型＋平台知识库”和“HPC Copilot完整上下文”三种方案。旧的 `benchmark/` 继续作为模块开发与回归测试，不能直接充当最终竞赛成绩。

## 基本原则

1. 现有题目全部视为开发集。
2. 正式测试集在系统稳定后建立，固定为30题。
3. 正式测试前用 `freeze.py` 记录代码、知识库、题目和评测程序的SHA-256指纹。
4. 三组使用同一API、同一模型、temperature=0和同一组问题。
5. 保存原始回答，评分程序不调用大模型裁判。
6. 脚本题最终还要在真实平台记录“提交成功”和“运行完成”两项结果。
7. 正式测试后若修改业务代码或知识库，本轮成绩作废。

## 数据来源标记

每道题必须标记一种来源：

- `official_doc`：平台或Slurm文档；
- `real_platform_log`：真实平台日志，必须脱敏；
- `synthetic_variant`：根据典型问题构造的变体，不能冒充真实日志。

## 推荐执行顺序

```powershell
python benchmark_v2/validate_cases.py benchmark_v2/cases/development.yaml
python benchmark_v2/run_comparison.py --cases benchmark_v2/cases/development.yaml --output benchmark_v2/results/development_responses.json
```

这一个对照命令会依次保存原始回答、客观评分JSON和可读Markdown报告。`score_results.py` 与 `render_report.py` 仍可用于重新评分旧结果。

程序默认每次请求间隔2秒，对429限流和5xx网关错误自动退避重试，并在每题后保存进度。中断后重新运行同一条命令会跳过已经成功的题目。也可以只补跑某一组：

```powershell
python benchmark_v2/run_comparison.py --cases benchmark_v2/cases/development.yaml --output benchmark_v2/results/development_responses.json --systems hpc_copilot
```

运行对照时，如果项目配置里没有保存API Key，程序会在终端中隐藏输入内容，并且不会把Key写入结果文件。`llm_only`仍使用与产品一致的基础系统提示词，但不提供平台知识库和诊断工具结果；这样可以更保守地衡量知识库与工具带来的增益。

诊断规则可以在没有大模型API Key时单独评测：

```powershell
python benchmark_v2/run_rule_eval.py
python benchmark_v2/run_retrieval_eval.py
```

正式评测时先验证题目，再冻结：

```powershell
python benchmark_v2/freeze.py --cases benchmark_v2/cases/final/final_v1.yaml
python benchmark_v2/run_comparison.py --cases benchmark_v2/cases/final/final_v1.yaml --freeze-manifest benchmark_v2/results/freeze_manifest.json
```

冻结文件会记录当前工作区是干净还是有未提交修改，并为影响结果的文件生成内容指纹。即使工作区暂时无法提交，也可以证明评测前后使用的是同一份代码。

脚本生成结果需要逐个在平台原样提交。复制 `platform_results.template.yaml`，记录第一次提交是否成功、最终状态、退出码和关键输出，再使用 `merge_platform_results.py` 合并。人工修改后才能运行的脚本，其“首次提交成功”必须记为失败。

## 结果表述边界

这是一套针对107算力平台的项目组自建评测，不是第三方公开Benchmark。答辩时应同时公开题目来源比例、评分规则、原始回答、平台运行日志和冻结指纹。
