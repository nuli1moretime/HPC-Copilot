#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_wrong_path
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意运行不存在的 Python 文件
# 预期结果：python: can't open file 'nonexistent_script.py'
echo "Attempting to run a script that does not exist..."
python nonexistent_script.py
