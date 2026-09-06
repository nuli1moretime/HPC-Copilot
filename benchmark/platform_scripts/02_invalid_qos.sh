#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=invalid_qos_name
#SBATCH --job-name=bench_bad_qos
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意使用无效 QoS
# 预期结果：sbatch 直接报错 "Invalid qos specification"
echo "This should never run"
