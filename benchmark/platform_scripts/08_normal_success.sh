#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_success
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 正常作业 — 用于验证 Agent 不会误报
# 预期结果：作业正常完成，无告警
echo "This is a normal job running successfully"
echo "Current time: $(date)"
echo "Hostname: $(hostname)"
python -c "print('Hello from Python:', 2+2)"
echo "Job completed normally."
