#!/bin/bash
#SBATCH --partition=NONEXISTENT_PARTITION
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_bad_partition
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意使用不存在的分区
# 预期结果：sbatch 直接报错 "Invalid partition specification"
echo "This should never run"
