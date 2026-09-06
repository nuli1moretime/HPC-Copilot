#!/bin/bash
#SBATCH --job-name=gpu_test
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --time=00:10:00
#SBATCH --output=slurm-%j.out

echo "=============================="
echo " GPU 环境验证作业"
echo " Job ID: $SLURM_JOB_ID"
echo " Node:   $(hostname)"
echo " GPU:    $CUDA_VISIBLE_DEVICES"
echo "=============================="

# 加载环境
module load cuda/13.0 python3.12 miniconda/py312

# 确认环境
echo ""
echo "[1/3] 检查 nvidia-smi..."
nvidia-smi || echo "⚠️ nvidia-smi 失败"

echo ""
echo "[2/3] 检查 PyTorch CUDA..."
python3 -c "import torch; print(f'PyTorch {torch.__version__}, CUDA available: {torch.cuda.is_available()}')" || echo "⚠️ PyTorch 检查失败"

echo ""
echo "[3/3] 运行 MNIST 训练..."
python3 train_mnist.py

echo ""
echo "=============================="
echo " 作业完成，退出码: $?"
echo "=============================="
