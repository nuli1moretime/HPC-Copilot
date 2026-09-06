# SBATCH 脚本编写规范与常见坑

> 来源：107平台培训PDF + docs-main + 平台实测（2026-08-27）
> 适用对象：HPC Copilot 智能体知识库 / RAG检索源
> 用途：当用户请求"帮我写个脚本"时，RAG注入这些平台约束，防止生成不可用的脚本

---

## 脚本基本结构

每个SBATCH脚本必须包含两部分：SBATCH头部声明（#开头的特殊注释）和执行命令。

```bash
#!/bin/bash
#SBATCH -J my_job                    # 作业名称（squeue/sacct可识别）
#SBATCH -p P107-RTX5090              # 分区（比赛用P107-RTX5090或P107-A100）
#SBATCH -N 1                         # 节点数
#SBATCH --gres=gpu:1                 # GPU数量
#SBATCH -n 4                         # 总进程数
#SBATCH --cpus-per-task=8            # 每任务CPU核数
#SBATCH --mem=32G                    # 内存上限
#SBATCH --time=02:00:00              # 最大运行时间（HH:MM:SS）
#SBATCH --output=logs/%x-%j.out      # 标准输出（%x=作业名, %j=JobID）
#SBATCH --error=logs/%x-%j.err       # 错误输出

# === 执行命令 ===
set -euo pipefail                    # 推荐：遇错即停
cd ~/projects/my-project             # 切到项目目录
python src/train.py                  # 执行入口
```

---

## 必须遵守的平台约束

### 1. 分区和QOS必须匹配

107比赛分区：P107-RTX5090（RTX 5090）和P107-A100（A100 80G）。
对应QOS：qos_p107-rtx5090 和 qos_p107-a100。
不要写不存在的分区名（如P107-V100、GPU-5090等）。

### 2. QOS资源上限不可超过

qos_p107-rtx5090：最多4个作业同时运行，每组cpu=16 + gpu=4，时长不限。
qos_p107-a100：最多4个作业同时运行，每组cpu=16 + gpu=2，时长不限。
Students分区默认：4CPU / 1GPU / 16G / 4小时。

超过上限会在提交时直接报错（QOSMaxWallDurationPerJobLimit / QOSMaxCpuPerUserLimit）。

### 3. 命令行参数覆盖脚本参数

sbatch -p CPU-6530 job.sh 会覆盖脚本内的 #SBATCH -p P107-RTX5090。
如果用户说"用某个分区提交"，优先在命令行指定而非修改脚本。

### 4. 脚本中必须用绝对路径或显式cd

作业在计算节点执行，工作目录默认是提交时的目录。如果提交后目录被删除，作业会失败。推荐在脚本开头显式cd到项目目录。

### 5. 日志目录必须预先创建

SBATCH不会自动创建logs/目录。如果-o logs/%x-%j.out但logs/不存在，作业可能无法写入日志。在脚本中加入mkdir -p logs。

---

## Conda环境激活（最常见的坑）

### 问题：批处理脚本中conda不可用

批处理作业不自动加载conda init脚本。直接在脚本中写conda activate会报"conda: command not found"。

### 正确写法

```bash
#!/bin/bash
#SBATCH -J my-train
#SBATCH -p P107-RTX5090
#SBATCH --gres=gpu:1
#SBATCH -t 04:00:00
#SBATCH -o logs/%x-%j.out
#SBATCH -e logs/%x-%j.err

set -euo pipefail
cd ~/projects/my-project

# 正确激活conda（注意set +u / set -u）
set +u
source ~/miniconda3/etc/profile.d/conda.sh
conda activate myenv
set -u

python src/train.py
```

### 为什么需要set +u / set -u？

conda.sh脚本中引用了一些可能未定义的shell变量，在set -u模式下会报错。set +u临时关闭未定义变量检查，激活后set -u恢复。

### 如果conda本身未加载

107平台需要先加载miniconda模块：
```bash
module load miniconda/py312
source /public/home/$USER/miniconda3/etc/profile.d/conda.sh
conda activate myenv
```

---

## GPU相关注意事项

### 登录节点无GPU

nvidia-smi在登录节点无法使用。检查GPU必须在已分配GPU的计算环境中。

### 安装GPU版PyTorch

登录节点无GPU，conda可能解析为CPU版PyTorch。必须强制指定CUDA版本：
```bash
CONDA_OVERRIDE_CUDA="12.4" conda install pytorch torchvision torchaudio pytorch-cuda=12.4 -c pytorch -c nvidia -y
```

### 在脚本中验证GPU

```bash
nvidia-smi                                          # 查看GPU状态
python -c "import torch; print(torch.cuda.is_available())"  # 验证PyTorch可用GPU
```

### 指定GPU类型

```bash
#SBATCH --gres=gpu:5090:1    # 指定RTX 5090
#SBATCH --gres=gpu:A100:1    # 指定A100
```

---

## 多节点MPI并行作业

多节点MPI作业同时受到分区、QoS、节点数和CPU总数限制。提交前先确认账号允许的资源，不要直接照搬节点数：

```bash
sinfo -s
sacctmgr show assoc where user=$USER format=User,Account,Partition,QOS
module avail 2>&1 | grep -i mpi
```

平台提供`mpi/2021.18`和`mpi/latest`模块。下面是脚本结构示例，其中分区、QoS和节点数必须换成账号实际允许的值：

```bash
#!/bin/bash
#SBATCH --job-name=mpi-demo
#SBATCH --partition=<可用分区>
#SBATCH --qos=<与分区匹配的QoS>
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=4
#SBATCH --time=00:10:00
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail
module load mpi/latest
srun ./my_mpi_program
```

优先使用`srun`启动MPI程序，让Slurm按已分配资源创建进程。不要在登录节点直接运行`mpirun`。如果默认Students/QoS不允许多节点，应申请相应权限或先改成单节点验证，不能通过虚构分区或超额申请绕过限制。

## set -euo pipefail 的使用

### 推荐在脚本开头加入

```bash
set -euo pipefail
```

含义：-e 遇错即停（命令返回非零退出码时终止脚本）、-u 未定义变量报错、-o pipefail 管道中任一命令失败则整体失败。

### 重要：无set -e时错误后继续执行

如果脚本没有set -e，某条命令失败后后续命令会继续执行。例如python train.py报错后，脚本不会停止，可能产生误导性的"成功"输出。

### conda激活时的例外

conda.sh激活脚本与set -u不兼容，需要在激活前后用set +u / set -u包裹。

---

## 时间格式

正确格式：
- --time=02:00:00 （2小时）
- --time=00:30:00 （30分钟）
- --time=1-00:00:00 （1天）
- --time=72:00:00 （72小时，部分格式支持）

错误格式：
- --time=2days （不被接受）
- --time=2h （不被接受）
- --time=120 （仅数字，含义不明）

---

## 输出文件路径

### 推荐用logs目录 + 占位符

```bash
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
```

%x=作业名，%j=JobID，%t=数组任务ID。

### 不要写到/tmp

/tmp是各节点独立的临时空间，作业结束后可能被清理。重要输出写到/public或~/projects/下。

### 标准输出和错误分开

分开写-o和-e方便定位问题。.out包含程序正常输出，.err包含错误信息和Slurm系统消息。注意TIMEOUT信息不写入.out，在.err或squeue状态中。

---

## 数据与文件管理

### 项目目录结构推荐

```
my-project/
  src/       # 代码
  data/      # 数据或数据链接
  scripts/   # sbatch脚本
  logs/      # 标准输出和错误日志
  outputs/   # 模型、图表、结果
```

### 大文件不要直接上传

GitHub/HuggingFace在平台可能不稳定。建议：本地clone → 打包tar -czf → 上传 → 平台解压tar -xzf。或使用USTC镜像。

### 验证上传完整性

```bash
ls -lh my-project.tar.gz       # 对比大小
sha256sum my-project.tar.gz    # 对比校验和
```

---

## 常见错误与预防

### 1. 路径拼写错误

错误：python tran.py（漏字母）
预防：脚本中用绝对路径，提交前ls确认文件存在。

### 2. 分区名/QOS名拼写错误

错误：-p P107-5090（应为P107-RTX5090）、--qos=qos_p107（不完整）
预防：用sinfo查看分区名，sacctmgr show assoc查看可用QOS。

### 3. 忘记激活conda

错误：直接写python train.py，用的是系统Python而非conda环境
预防：脚本中显式source conda.sh + conda activate。

### 4. 日志目录不存在

错误：-o logs/%x-%j.out 但logs/目录未创建
预防：脚本开头加mkdir -p logs，或提交前手动创建。

### 5. 时间申请过短或格式错误

错误：--time=10:00（10分钟可能不够）、--time=2days（格式错误）
预防：先跑小规模估算时间，用HH:MM:SS格式。

### 6. 资源申请超过QOS上限

错误：--gres=gpu:8（超过qos_p107-rtx5090的gpu=4上限）
预防：查看QOS限制，合理分配GPU数量。

### 7. 在登录节点跑训练

错误：直接在Shell中运行python train.py（占用登录节点资源）
预防：始终通过sbatch提交到计算节点。

### 8. 脚本无执行权限

错误：sbatch ./train.sh 但train.sh没有chmod +x
预防：创建脚本后chmod +x train.sh，或用sbatch train.sh（不带./）。

### 9. 没有set -e导致错误被忽略

错误：python train.py失败但后续命令继续执行，看起来"成功"
预防：脚本开头加set -euo pipefail。

### 10. 写文件到无权限的目录

错误：torch.save(model, "/shared/models/best.pt")（/shared无写权限）
预防：输出路径写在自己的/home或/public目录下。

---

## 完整模板集合

### 最小测试脚本

```bash
#!/bin/bash
#SBATCH -J hello-test
#SBATCH -p Students
#SBATCH --qos=qos_stu_default
#SBATCH -t 00:05:00
#SBATCH -o logs/%x-%j.out
#SBATCH -e logs/%x-%j.err

set -euo pipefail
mkdir -p logs
cd ~/projects/hello-test

echo "Hello from $(hostname)"
echo "Python: $(python -V)"
echo "Date: $(date)"
pwd
ls -la
```

### GPU训练脚本

```bash
#!/bin/bash
#SBATCH -J gpu-train
#SBATCH -p P107-RTX5090
#SBATCH --qos=qos_p107-rtx5090
#SBATCH -N 1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH -o logs/%x-%j.out
#SBATCH -e logs/%x-%j.err

set -euo pipefail
mkdir -p logs
cd ~/projects/my-train

# 激活conda
set +u
module load miniconda/py312
source /public/home/$USER/miniconda3/etc/profile.d/conda.sh
conda activate myenv
set -u

# 环境检查
nvidia-smi
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# 开始训练
python src/train.py --epochs 100 --batch-size 32
```

### 数组作业（超参数搜索）

```bash
#!/bin/bash
#SBATCH -J hp-search
#SBATCH -p P107-RTX5090
#SBATCH --qos=qos_p107-rtx5090
#SBATCH --gres=gpu:1
#SBATCH -n 4
#SBATCH --time=02:00:00
#SBATCH --array=1-10
#SBATCH -o logs/%x-%j-%t.out
#SBATCH -e logs/%x-%j-%t.err

set -euo pipefail
mkdir -p logs outputs
cd ~/projects/hp-search

# 根据任务编号选择超参数
case $SLURM_ARRAY_TASK_ID in
  1) LR=0.1;   BS=32;;
  2) LR=0.05;  BS=32;;
  3) LR=0.01;  BS=64;;
  4) LR=0.005; BS=64;;
  5) LR=0.001; BS=128;;
  *) LR=0.001; BS=32;;
esac

set +u
source ~/miniconda3/etc/profile.d/conda.sh
conda activate myenv
set -u

python src/train.py --lr $LR --batch-size $BS \
  --output outputs/run-${SLURM_ARRAY_TASK_ID}
```

### 多GPU分布式训练

```bash
#!/bin/bash
#SBATCH -J dist-train
#SBATCH -p P107-RTX5090
#SBATCH --qos=qos_p107-rtx5090
#SBATCH -N 2
#SBATCH --gres=gpu:4
#SBATCH -n 16
#SBATCH --time=12:00:00
#SBATCH -o logs/%x-%j.out
#SBATCH -e logs/%x-%j.err

set -euo pipefail
mkdir -p logs
cd ~/projects/dist-train

export MASTER_ADDR=$(hostname)
export MASTER_PORT=29500
export NCCL_DEBUG=INFO

set +u
source ~/miniconda3/etc/profile.d/conda.sh
conda activate myenv
set -u

torchrun --nnodes=$SLURM_JOB_NUM_NODES \
         --nproc_per_node=4 \
         --rdzv_id=$SLURM_JOB_ID \
         --rdzv_backend=c10d \
         --rdzv_endpoint=$MASTER_ADDR:$MASTER_PORT \
         src/train.py --epochs 500
```

注意：qos_p107-rtx5090限制gpu=4/作业，这里--gres=gpu:4刚好到上限。qos_p107-a100限制gpu=2。

---

## 提交前检查清单

1. logs/目录是否存在？（或脚本中有mkdir -p logs）
2. cd路径是否是真实的项目路径？
3. conda环境名称是否正确？能否手动激活？
4. 是否用少量数据通过了冒烟测试？
5. 资源参数是否在QOS限制内？（GPU数、CPU数、内存、时间）
6. 分区名和QOS名拼写是否正确？
7. 入口脚本文件名和路径是否正确？
8. 输出路径是否在自己的目录下（有写权限）？
9. 脚本是否有set -euo pipefail（或至少set -e）？
10. 如果是GPU任务，是否安装了GPU版PyTorch（非CPU版）？
