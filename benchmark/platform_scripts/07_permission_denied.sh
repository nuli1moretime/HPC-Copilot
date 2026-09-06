#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_permission
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意写一个无权限的目录
# 预期结果：Permission denied
echo "Testing permission denied..."
echo "test" > /root/should_not_write_here.txt
