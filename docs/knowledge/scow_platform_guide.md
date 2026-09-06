# SCOW平台使用指南（docs-main整理）

> 来源：USTC本科算力平台文档 docs-main（v0.1, 2026-05-21）
> 适用对象：HPC Copilot 智能体知识库

---

## 平台定位与架构

中国科大本科算力平台（https://107.ustc.edu.cn/）是一组学校统一管理的服务器，面向课程作业、训练项目、数值计算、统计模拟和科研训练。用户提交代码、数据和作业需求，平台在合适的计算节点上运行任务，日志和结果留在用户工作目录。

平台由三类节点组成。登录节点（tradmin-02）用于文件管理、编辑脚本、安装轻量依赖、提交作业，不可在此运行训练或大型计算。计算节点（anode系列）通过Slurm调度访问，用于实际的GPU训练、大规模数值计算等。共享存储让登录节点和计算节点看到相同的用户文件，在登录节点上传的代码在计算任务中可直接访问。

重要认知：登录Shell不等于能跑训练——训练必须通过Slurm到计算节点；关闭浏览器不会停止批处理作业（由调度器管理）；资源是共享的，不要盲目申请过多资源。

---

## 快速入门流程

首次使用推荐路径：打开https://107.ustc.edu.cn/ → 学校账号登录 → 确认看到文件管理、Shell终端、作业/Slurm入口、资源申请 → 创建VS Code应用或登录集群Shell → 创建工作目录 → 准备最小程序 → 提交第一个批处理任务 → 查看日志。

创建工作目录示例：
```bash
mkdir -p ~/projects/hello-cp4u/{src,logs,outputs}
cd ~/projects/hello-cp4u
```

推荐项目目录结构：
```
my-project/
  src/       # 代码
  data/      # 数据或数据链接
  scripts/   # sbatch脚本、启动脚本
  logs/      # 标准输出和错误日志
  outputs/   # 模型、图表、结果表格
```

---

## 访问方式与入口

平台提供四种入口。SCOW Web门户（https://107.ustc.edu.cn/）提供图形界面，包括应用管理、作业提交、文件管理、WebShell。VS Code在线编辑器通过"应用"入口创建，适合编码+终端+环境配置。登录集群Shell用于创建目录、检查文件、提交Slurm作业、短时调试。SSH命令行目前不作为主线入口（平台不假设SSH可用）。

默认分区和QOS：统一认证登录默认使用Students分区和qos_stu_default QOS。

---

## QOS资源配额

平台QOS层级（2026-05-21快照）：

qos_stu_default：4 CPU / 1 GPU / 16G内存 / 最长4小时，适用于默认用户、小作业、调试。
qos_stu_small：8 CPU / 1 GPU / 32G内存 / 最长8小时，适用于课程实验、小模型训练。
qos_stu_medium_2gpu：24 CPU / 2 GPU / 128G内存 / 最长12小时，适用于双GPU训练。
qos_stu_long：16 CPU / 1 GPU / 64G内存 / 最长72小时，适用于单GPU长训练。
qos_stu_cpu_long：32 CPU / 0 GPU / 128G内存 / 最长72小时，适用于CPU长任务、数据预处理。

资源申请流程：进入https://107.ustc.edu.cn/apply → 选择QOS层级 → 填写任务用途和时间范围 → 如需导师确认则填写导师信息 → 管理员审批 → QOS授权到Slurm账户 → 创建应用或提交作业时选择新QOS。

---

## 环境配置

核心原则：每个项目应有独立的conda/mamba环境。

安装Miniconda：
```bash
cd ~
wget https://mirrors.ustc.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p "$HOME/miniconda3"
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

配置USTC镜像源（.condarc）：
```yaml
channels:
  - https://mirrors.ustc.edu.cn/anaconda/cloud/conda-forge
  - https://mirrors.ustc.edu.cn/anaconda/pkgs/main
  - https://mirrors.ustc.edu.cn/anaconda/pkgs/r
show_channel_urls: true
```

pip镜像：pip config set global.index-url https://mirrors.ustc.edu.cn/pypi/web/simple

创建环境：
```bash
conda create -n py310 python=3.10 -y
conda activate py310
```

安装GPU版PyTorch（登录节点无GPU，conda可能解析为CPU版，须强制指定CUDA）：
```bash
CONDA_OVERRIDE_CUDA="12.4" conda install pytorch torchvision torchaudio pytorch-cuda=12.4 -c pytorch -c nvidia -y
```

在作业脚本中激活conda环境（注意set +u/set -u防止批处理中未定义变量报错）：
```bash
set +u
source ~/miniconda3/etc/profile.d/conda.sh
conda activate py310
set -u
python src/train.py
```

GPU环境检查（必须在已分配GPU的环境中执行）：
```bash
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
```

---

## 作业提交

交互式 vs 批处理：交互式（srun --pty bash）适合短时调试（检查GPU、试命令、环境调试）；批处理（sbatch脚本）适合训练、模拟、批量实验、需要日志的任务、需要关浏览器后继续运行的任务。

最小作业脚本：
```bash
#!/bin/bash
#SBATCH -J hello
#SBATCH -o logs/%x-%j.out
#SBATCH -e logs/%x-%j.err
#SBATCH -t 00:05:00
set -euo pipefail
cd ~/projects/my-project
python src/hello.py
```

GPU训练脚本模板：
```bash
#!/bin/bash
#SBATCH -J my-train
#SBATCH -p Students
#SBATCH --qos=qos_stu_default
#SBATCH --gres=gpu:5090:1
#SBATCH --cpus-per-task=4
#SBATCH -t 04:00:00
#SBATCH -o logs/%x-%j.out
#SBATCH -e logs/%x-%j.err
set -euo pipefail
cd ~/projects/my-project
set +u
source ~/miniconda3/etc/profile.d/conda.sh
conda activate py310
set -u
nvidia-smi
python src/train.py
```

提交前检查清单：logs/目录已存在、cd路径是真实项目路径、环境名称正确且可手动激活、已用少量数据通过冒烟测试、资源参数在平台/课程限制内。

指定GPU类型（如允许）：
```bash
#SBATCH --gres=gpu:5090:1
#SBATCH --gres=gpu:A100:1
```

---

## 文件管理

上传流程：小文件通过GUI上传；大目录先在本地打包tar -czf，上传压缩包后在平台解压tar -xzf。

验证上传完整性：
```bash
ls -lh my-project.tar.gz       # 对比大小
sha256sum my-project.tar.gz    # 对比校验和
```

外部下载注意：GitHub/HuggingFace在平台可能不稳定，建议使用USTC镜像、课程提供的下载链接或国内镜像。先在本地clone仓库，打包后上传。

下载结果：
```bash
cd ~/projects/my-project
tar -czf outputs-$(date +%Y%m%d).tar.gz outputs logs
```

反模式：所有文件堆在主目录根下、不记录参数就覆盖旧结果、在作业脚本中使用终端特定的相对路径、在共享存储堆积无关大文件。

---

## 命令行常用命令

位置确认：hostname、pwd、whoami、date
文件目录：ls -lh、mkdir -p、cd、cp、mv、rm、tar -czf/tar -xzf、sha256sum
日志查看：cat、less、tail -n、tail -f、sed -n、grep -n
编辑：nano（新手友好）、vim、python -m py_compile（仅语法检查）
Python/环境：python -V、which python、conda env list、conda activate、pip list、pip freeze > requirements.lock.txt
Slurm作业：sbatch、squeue -u "$USER"、scontrol show job <job_id>、scancel <job_id>、scontrol show part、sinfo

Web终端注意事项：可能断连；Ctrl+C是中断程序不是复制；Ctrl+V可能被浏览器拦截；Mac部分Ctrl+箭头组合键无法到达终端；解决方案：右键复制粘贴，使用tmux保持会话。

---

## 作业状态与排查

作业状态速查：PD=PENDING排队等待资源、R=RUNNING运行中、CG=COMPLETING退出中、CD=COMPLETED正常结束、F=FAILED失败、Ca=CANCELLED取消。

排查顺序：
1. squeue -u "$USER" — 作业还在排队还是已在运行？
2. scontrol show job <job_id> — 资源请求和等待原因
3. tail -n 80 logs/<file>.err — 错误日志
4. tail -n 80 logs/<file>.out — 程序输出
5. 如果是GPU任务：检查nvidia-smi输出

scontrol show part关键字段：PartitionName、AllowAccounts、AllowQos、Nodes、State、TRES。
sinfo节点状态：idle=空闲、mix=部分占用、comp=基本满、down/down*=不可用、drng=排空中。

---

## 常见问题（FAQ）

### nvidia-smi找不到GPU
可能原因：在登录节点运行（无GPU）、作业未申请GPU、作业还在排队。解决：确认脚本中有GPU申请，在脚本中加入nvidia-smi，用squeue检查。

### 作业一直排队
原因：资源紧张、申请过多、分区/QOS不匹配、达到用户上限。解决：先跑小规模冒烟测试。

### QOSMaxWallDurationPerJobLimit错误
运行时间超过QOS限制（默认最长4小时）。解决：减少-t到4小时以内。

### QOSMaxCpuPerUserLimit错误
CPU申请超过用户/QOS限制（默认4 CPU）。解决：保持--cpus-per-task <= 4。

### 没有日志文件
原因：logs/目录不存在、路径错误、作业未启动、Slurm在写入前就拒绝。解决：提交前mkdir -p logs。

### 训练OOM（CUDA out of memory）
解决：减小batch size、模型大小或输入大小。系统杀进程：减少数据加载worker数或申请更多内存。数据加载慢：文件组织、worker数或存储I/O问题。

### conda安装了CPU版PyTorch
登录节点无GPU，conda解析为CPU版。修复：CONDA_OVERRIDE_CUDA="12.4" conda install pytorch ...

### 作业中找不到conda
批处理作业不自动加载conda init脚本。修复：在脚本中加入source ~/miniconda3/etc/profile.d/conda.sh。

### 更多CPU worker更快？
不一定。过多worker导致CPU争用、内存压力、文件系统压力。先小规模基准测试。

### 关闭浏览器任务会停吗？
批处理作业：不会，由调度器管理独立于浏览器。交互式Shell/Web终端：可能受影响。使用tmux保持交互式会话；长任务用批处理脚本。

### nvidia-smi报告Driver/library version mismatch
NVML/驱动库版本与节点驱动不匹配。记录JobID、节点名、GPU型号、完整错误信息，报告平台管理员。

### 向平台求助时需提供
问题发生的步骤、作业ID、提交脚本、.out和.err关键内容、资源请求参数、已尝试的排查步骤。

---

## 学科场景指南

### 深度学习作业
适用：PyTorch/TensorFlow训练、GPU模型实验、从个人电脑迁移。流程：组织项目目录 → 配置conda环境 → 写冒烟测试（1 epoch、小batch） → 提交GPU冒烟脚本 → 逐步扩大规模 → 归档结果。

### 数学统计
适用：Monte Carlo模拟、参数扫描/网格搜索、数值优化、ODE求解、批量独立实验。流程：准备最小输入 → 小规模验证输出格式 → 参数放配置文件或命令行 → Slurm脚本提交 → 每个实验独立目录。

### 物理化学
适用：物理模拟/数值求解、化学/材料/结构计算、批量实验数据处理。建议：每组输入独立目录避免输出覆盖、先小系统/少步数测试、保存软件版本+输入文件+提交脚本+输出日志。

---

## 交互式计算

CPU交互式：
```bash
srun -p Students --qos=qos_stu_default -c 1 -t 00:10:00 --pty bash
```

GPU交互式：
```bash
srun -p Students --qos=qos_stu_default --gres=gpu:1 -c 1 -t 00:10:00 --pty bash
```

注意：交互式会话仅用于调试，训练/模拟/批量实验应使用sbatch脚本。退出交互会话后资源自动释放。

---

## 平台状态快照（2026-05-21）

登录节点：tradmin-02
用户目录：/home/scc/<account>
登录Shell Python：3.12.3
Students分区节点：anode[05-17]
Students分区TRES：cpu=1664, mem=7500G, gres/gpu=104
可用QOS：qos_stu001, qos_stu_default, qos_stu_small, qos_stu_medium, qos_stu_medium_2gpu, qos_stu_long, qos_stu_cpu_long
节点状态含义：idle=空闲、mix=部分占用、comp=基本满、down=不可用

---

## 关键链接

平台入口：https://107.ustc.edu.cn/
资源申请：https://107.ustc.edu.cn/apply
USTC镜像：https://mirrors.ustc.edu.cn
Slurm官方文档：https://slurm.schedmd.com/documentation.html
问题反馈：hmli@ustc.edu.cn
