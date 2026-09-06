# Benchmark 平台实测指南

> 本指南帮助你在校算力平台（107.ustc.edu.cn）上逐一运行测试脚本，收集真实错误日志。
> 预计耗时：15-20 分钟（大部分是等待作业运行的时间）。

## 前置准备

1. 登录平台 WebShell（通过 HPC Copilot 或直接访问平台）
2. 把 `benchmark/platform_scripts/` 目录上传到你的主目录：
   ```bash
   mkdir -p ~/benchmark_test
   ```
   然后将所有 .sh 文件放到 `~/benchmark_test/` 下。

   **快捷方式**：如果你的本地电脑能 scp 到平台，可以用：
   ```bash
   scp platform_scripts/*.sh sa25232066@107.ustc.edu.cn:~/benchmark_test/
   ```
   否则直接在 WebShell 里用 `cat > 文件名 << 'EOF'` 逐个粘贴创建。

3. 给所有脚本加执行权限：
   ```bash
   cd ~/benchmark_test
   chmod +x *.sh
   ```

## 测试步骤

### 测试 1：超时错误（TIMEOUT）
```bash
cd ~/benchmark_test
sbatch 01_timeout.sh
```
- 预期：提交成功，显示 `Submitted batch job <JOBID>`
- 等待约 15 秒后作业被终止
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（应包含 DUE TO TIME LIMIT）

### 测试 2：无效 QoS
```bash
sbatch 02_invalid_qos.sh
```
- 预期：提交直接失败，终端立即显示错误
- **记录终端输出**（应包含 Invalid qos specification）

### 测试 3：无效分区
```bash
sbatch 03_invalid_partition.sh
```
- 预期：提交直接失败
- **记录终端输出**（应包含 Invalid partition specification）

### 测试 4：文件路径错误
```bash
sbatch 04_wrong_path.sh
```
- 预期：提交成功，作业很快结束（FAILED）
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（应包含 can't open file / No such file or directory）

### 测试 5：无效 Module
```bash
sbatch 05_bad_module.sh
```
- 预期：提交成功，作业失败
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（应包含 module 相关错误）

### 测试 6：Python Import 错误
```bash
sbatch 06_import_error.sh
```
- 预期：提交成功，作业失败
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（应包含 ModuleNotFoundError）

### 测试 7：权限拒绝
```bash
sbatch 07_permission_denied.sh
```
- 预期：提交成功，作业中写文件失败
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（应包含 Permission denied）

### 测试 8：正常作业（对照组）
```bash
sbatch 08_normal_success.sh
```
- 预期：提交成功，作业正常完成（COMPLETED）
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（不应有任何错误信息）

### 测试 9：Python 运行时异常
```bash
sbatch 09_runtime_error.sh
```
- 预期：提交成功，作业失败
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（应包含 ZeroDivisionError traceback）

### 测试 10：Conda 未初始化
```bash
sbatch 10_conda_not_found.sh
```
- 预期：提交成功，作业失败
- 查看输出：`cat slurm-<JOBID>.out`
- **记录输出内容**（应包含 conda: command not found）

## 结果收集

每次运行后，把 `slurm-<JOBID>.out` 的内容保存到 `benchmark/results/` 目录。
命名格式：`<测试编号>_<错误类型>.txt`，例如：
- `01_timeout.txt`
- `02_invalid_qos.txt`（这个直接记录终端输出）
- `04_entrypoint.txt`
- ...

## 注意事项

- 测试 2 和 3 会在 sbatch 时直接失败，不会产生 slurm-xxx.out 文件，直接记录终端输出即可
- 测试 1 需要等 10 秒让超时触发
- 如果某个测试的行为和预期不同（比如平台配置特殊导致没报错），也如实记录——这本身就是有价值的 benchmark 数据
- 跑完后告诉我结果，我会把真实日志补充进测试案例，替换掉模拟文本

## 最终目标

跑完这 10 个测试后，你将拥有：
- 10 条来自真实平台的错误/正常日志
- 可以验证 HPC Copilot 是否正确识别每一种错误
- 为设计文档提供"在真实平台验证"的数据支撑
