#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_import_err
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意 import 不存在的 Python 包
# 预期结果：ModuleNotFoundError: No module named 'nonexistent_package_xyz'
echo "Testing Python import error..."
python -c "import nonexistent_package_xyz"
