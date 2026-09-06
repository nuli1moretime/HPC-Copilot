#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_timeout
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:00:10
#SBATCH --nodes=1

# 故意超时：sleep 时间超过 --time 限制
# 预期结果：作业在 10 秒后被终止，报 TIMEOUT
echo "Starting timeout test at $(date)"
echo "This job will sleep for 60s but only has 10s limit"
sleep 60
echo "This line should never print"
