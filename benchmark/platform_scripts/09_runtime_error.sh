#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_exit_code
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

# 故意让 Python 抛出运行时异常（非 import 错误）
# 预期结果：TypeError/ValueError traceback，exit code 1
echo "Testing runtime exception..."
python -c "
def divide(a, b):
    return a / b
result = divide(10, 0)
print(result)
"
