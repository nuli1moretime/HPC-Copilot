#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_no_module
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意加载不存在的 module
# 预期结果：Lmod has detected the following error: unknown module
module load cuda/99.99
echo "This should not print if module load fails the script"
