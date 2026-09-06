#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_conda
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意在脚本中直接用 conda（不先 source/init）
# 预期结果：conda: command not found
echo "Testing conda without initialization..."
conda activate myenv
python --version
