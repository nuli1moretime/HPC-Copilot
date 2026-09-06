# 107平台使用策略与故障诊断指南

> 来源：中国科大"一〇七杯"算力平台赛道培训（李会民，2026-06-28）+ 平台实测补充
> 适用对象：HPC Copilot 智能体知识库

---

## 平台概览

中国科大超算中心107平台是一个17节点GPU集群，面向"一〇七杯"参赛队伍开放。平台包含两类计算节点：anode[01-15]配备双路Xeon 6530 CPU + 8块RTX 5090 GPU + 512GB内存，节点间以InfiniBand NDR 200Gbps互联；anode[16-17]配备双路Xeon 8358P CPU + 8块A100 80G GPU + 1TB内存，以InfiniBand HDR 100Gbps互联。共享存储采用曙光ParaStor（983TB），挂载在/public（等同于/home），各节点另有独立/tmp空间。操作系统为Ubuntu 24.04 LTS，调度器为Slurm 25.11，Web管理平台为SCOW（OpenSCOW）。管理节点tradmin-01运行slurmctld，登录节点tradmin-02（内网11.11.10.202）供用户交互。外网入口为107.ustc.edu.cn。

---

## 访问方式

平台提供三种访问方式。第一，SSH命令行：ssh <用户名>@107.ustc.edu.cn，需提前申请开通SSH公钥认证，并配置Google Authenticator二次验证（2FA），客户端须支持Keyboard Interactive认证模式。第二，SCOW Web门户：https://107.ustc.edu.cn，通过统一身份认证登录，提供作业提交/管理、文件管理、WebShell终端、在线VSCode等功能。第三，Slurm REST API：http://107.ustc.edu.cn:6820，使用JWT Token认证（通过scontrol token lifespan=86400生成，默认1天有效期），请求头携带Authorization: Bearer <token>，支持JSON格式，遵循OpenAPI v3规范，常用端点包括/slurm/v0.0.41/jobs和/slurm/v0.0.41/nodes。

---

## 分区与QoS

平台共有7个分区。CPU-6530为默认分区，覆盖anode[01-15]，1920 CPU核，无GPU。CPU-8358P覆盖anode[16-17]，256 CPU核，无GPU，大内存节点。GPU-RTX5090覆盖anode[01-15]，120块RTX 5090。GPU-A100覆盖anode[16-17]，16块A100。P107-RTX5090为比赛专用GPU分区，覆盖anode[01-15]，1920 CPU核 + 120块RTX 5090。P107-A100为比赛专用高精度分区，覆盖anode[16-17]，256 CPU核 + 16块A100 80G。Students为学生统一认证分区，覆盖anode[05-17]。

QoS（Quality of Service）控制资源配额。比赛分区QoS限制如下：qos_p107-rtx5090允许最多4个作业同时运行，GrpTRES限制为cpu=16, gpu=4，运行时长不限；qos_p107-a100允许最多4个作业，GrpTRES限制为cpu=16, gpu=2，时长不限。查看分区允许QoS：scontrol show partition <分区名> | grep AllowQos。查看自己可用QoS：sacctmgr show assoc user=$USER format=User,Partition,QOS。查看全局QoS：sacctmgr list qos -p。

---

## 作业提交（sbatch）

提交批处理作业使用sbatch命令。典型作业脚本格式：

```bash
#!/bin/bash
#SBATCH -J my_job                    # 作业名称
#SBATCH -p P107-RTX5090              # 分区
#SBATCH -N 1                         # 节点数
#SBATCH --gres=gpu:2                 # GPU数量
#SBATCH -n 4                         # 总MPI进程数
#SBATCH --cpus-per-task=8            # 每任务CPU核数
#SBATCH --time=02:00:00              # 超时时间
#SBATCH --output=%j.out              # 标准输出（%j=JobID）
#SBATCH --error=%j.err               # 错误输出

python train.py --epochs 100
```

提交命令：sbatch job.sh。命令行参数优先级高于脚本内#SBATCH参数，例如sbatch -p CPU-6530 job.sh会覆盖脚本中的分区设置。

常用SBATCH参数速查：-J/--job-name作业名称，-p/--partition分区，-N/--nodes节点数，--gres=gpu:N GPU数量，-n/--ntasks总进程数，-c/--cpus-per-task每任务CPU，--time超时，-o/--output标准输出，-e/--error错误输出，--mem内存上限，--qos指定QoS，--array数组作业。

---

## 作业状态与查询

作业生命周期状态包括：PENDING（排队等待资源）、RUNNING（正在执行）、COMPLETED（正常结束）、FAILED（执行出错）、TIMEOUT（超时终止）、CANCELLED（被用户取消）。

squeue查看作业队列：squeue显示所有作业，squeue -u $USER查看自己的作业，squeue -p P107-RTX5090查看指定分区。输出字段：JOBID作业编号、分区、名称、ST状态码（R=运行/PD=排队/CG=退出中/S=挂起）、时间、原因/节点。排队原因：(Resources)资源不足、(Priority)优先级不足、(Dependency)依赖未完成、(AssocGrp...)达到QoS上限。

scontrol show job <JobID>查看作业详情：显示JobState、Partition、RunTime、TimeLimit、NodeList、NumCPUs、ReqTRES（请求资源）、AllocTRES（实际分配）、QOS、Command等。排查技巧：PENDING作业看Reason，FAILED看ExitCode，对比ReqTRES与AllocTRES。

sacct查看作业历史：sacct -u $USER -X --format=JobID,JobName,Elapsed,State。注意：sacct在本平台可能不可用或功能受限。

---

## 作业管理命令

scancel取消作业：scancel <JobID>取消单个，scancel -u $USER -t PENDING取消自己所有排队作业，scancel -p P107-RTX5090取消某分区所有作业。只能取消自己的作业。

srun交互式运行：srun -p P107-RTX5090 --gres=gpu:1 --time=01:00:00 python train.py，适用于调试和短任务。

挂起/恢复：scontrol hold <JobID>挂起排队中作业，scontrol release释放，scontrol suspend暂停运行中作业，scontrol resume继续。scontrol requeue让作业重新排队（适用于被抢占或资源不足失败的作业）。

---

## 查看集群状态

sinfo查看分区与节点状态：sinfo显示所有分区，sinfo -N -p <分区>按节点查看，sinfo -s紧凑概览，sinfo --noheader跳过表头。状态码：idle=空闲、mix=部分占用、alloc=全满、down*=宕机。同一分区可有多行（每行一种状态），总节点数=各状态行节点数之和。

登录节点查看实时状态：htop查看CPU/内存占用，nvidia-smi查看GPU状态（注意登录节点无GPU），scontrol show nodes查看节点分配，scontrol show jobs查看所有作业概览。

GPU分配查看：squeue --format="%i %P %j %b %M"。

---

## Slurm环境变量

以下变量仅在sbatch提交的作业脚本内有效（登录节点Shell中无定义）：$SLURM_JOB_ID作业编号，$SLURM_JOB_NAME作业名称，$SLURM_SUBMIT_DIR提交目录，$SLURM_JOB_NODELIST分配节点列表，$SLURM_NTASKS总任务数，$SLURM_CPUS_PER_TASK每任务CPU，$SLURM_GPUS分配GPU数，$SLURM_GPUS_PER_NODE每节点GPU数，$SLURM_NPROCS分配CPU核数，$SLURM_MEM_PER_NODE每节点内存，$SLURM_PARTITION当前分区名，$SLURM_ARRAY_TASK_ID数组作业任务编号。

展开节点列表：scontrol show hostnames $SLURM_JOB_NODELIST。

---

## 软件环境

平台预装Python 3.12（路径/public/app/python3.12/3.12），已包含numpy、torch、tensorflow、pandas、scikit-learn等20+常用包。模块系统使用Lmod，可用模块包括：cuda/12.6、cuda/13.0、python3.12/3.12、miniconda/py312、apptainer/1.4.5、go/1.24.5/1.25.6、vscode/4.21.1/4.118.0，以及Intel oneAPI系列（mkl、mpi、compiler、dnnl、ccl、tbb）。

使用module avail查看可用模块，module load <名称>加载。推荐使用Conda管理个人Python环境（miniconda/py312模块）。用户无sudo权限，不可自行安装系统级软件，如有需要须联系管理员。

## Apptainer容器

平台提供`apptainer/1.4.5`模块。已有`.sif`镜像时，可以在登录节点准备命令，再通过Slurm在计算节点运行：

```bash
module load apptainer/1.4.5
apptainer --version
apptainer exec ~/images/my_app.sif <程序命令>
```

需要进入镜像调试时可使用`apptainer shell ~/images/my_app.sif`。容器默认可以访问提交目录和用户主目录，仍应把重要结果写入共享存储。

当前平台访问Docker Hub可能受到DNS或外网策略限制，`apptainer pull docker://...`不保证成功。遇到`lookup registry-1.docker.io`等错误时，不要反复重试；应在可联网环境准备好`.sif`镜像后上传，或联系管理员提供镜像。容器不能绕过Slurm资源申请，也不能获得sudo权限。

---

## 存储与数据

共享存储/public（等同于/home）基于曙光ParaStor，所有节点可访问，用于存放代码、数据、输出。/tmp为各节点本地临时空间，用完即清，节点重启后清空。作业输出建议写入/public目录，避免使用/tmp存放重要结果。磁盘配额有限，使用过多会触发"Disk quota exceeded"错误。

---

## 典型作业模板

### 单GPU训练作业

```bash
#!/bin/bash
#SBATCH -J pytorch_train
#SBATCH -p P107-RTX5090
#SBATCH -N 1
#SBATCH --gres=gpu:1
#SBATCH -n 8
#SBATCH --time=04:00:00
#SBATCH --output=%j-pytorch.out
#SBATCH --error=%j-pytorch.err

source /public/home/$USER/miniconda3/etc/profile.d/conda.sh
conda activate myenv

python train.py --epochs 200 --batch-size 128 --lr 0.001
```

### 多GPU分布式训练

```bash
#!/bin/bash
#SBATCH -J distributed_train
#SBATCH -p P107-RTX5090
#SBATCH -N 2
#SBATCH --gres=gpu:4
#SBATCH -n 16
#SBATCH --time=12:00:00
#SBATCH --output=%j-dist.out

export MASTER_ADDR=$(hostname)
export MASTER_PORT=29500
export NCCL_DEBUG=INFO

torchrun --nnodes=$SLURM_JOB_NUM_NODES \
         --nproc_per_node=4 \
         --rdzv_id=$SLURM_JOB_ID \
         --rdzv_backend=c10d \
         --rdzv_endpoint=$MASTER_ADDR:$MASTER_PORT \
         train.py --epochs 500
```

注意：QoS限制每作业最多gpu=4（RTX5090）或gpu=2（A100），多GPU申请需确认不超限。

### 数组作业

```bash
#!/bin/bash
#SBATCH -J array_hp
#SBATCH -p P107-RTX5090
#SBATCH --gres=gpu:1
#SBATCH -n 4
#SBATCH --time=02:00:00
#SBATCH --array=1-10

case $SLURM_ARRAY_TASK_ID in
  1) LR=0.1;;
  2) LR=0.05;;
  3) LR=0.01;;
  4) LR=0.005;;
  *) LR=0.001;;
esac

python train.py --lr $LR --epochs 100
```

---

## SCOW Web平台功能

SCOW（Super Computing Online Workbench）是超算作业管理Web系统，地址https://107.ustc.edu.cn。功能包括：作业提交与管理（选择分区、填入脚本、提交/取消）、查看作业状态和标准输出/错误、WebShell浏览器内终端（可执行Slurm命令）、文件管理（上传下载）、在线VSCode编辑器（内置登录节点终端）。认证方式为统一身份认证/LDAP认证，Cookie名SCOW_USER（ustc-uuid格式，约24小时过期）。

---

## 监控系统

平台部署了Prometheus + Grafana监控体系。Grafana仪表盘地址http://107.ustc.edu.cn:3000（用户名/密码aiviewer），HPC监控概览http://114.214.255.131/hpc/（校内访问）。监控指标包括：节点CPU使用率/内存/显存/网络/磁盘IO、作业调度（排队数/运行数/集群负载）、GPU状态（显存/利用率/温度/ECC错误）、IB网络吞吐量。架构为：各节点部署node_exporter，登录节点部署slurm_exporter和GPU_exporter，Prometheus定时拉取数据，Grafana可视化展示。

---

## 实用工具

tmux终端会话管理：tmux new -s work创建会话，tmux attach -t work重连，Ctrl+b d断开（任务继续运行），tmux ls列出所有会话。SSH掉线后重新连接tmux attach即可恢复，适合长时间提交作业场景。

VIM编辑器基础：vim file.sh打开，i进入插入模式，Esc退出到普通模式，:wq保存退出，:q!强制退出，/关键词搜索，:%s/旧/新/g全局替换。

系统邮件：mail命令查看本地邮件（作业完成通知等）。注意#SBATCH --mail-type依赖本地sendmail，本平台未配置外部中继，不会生效。如需外部通知建议在脚本中调用Webhook/API。

Git版本控制：学校代码托管https://git.ustc.edu.cn。

---

## 常见问题与排查

### SSH无法连接
确认已提交SSH公钥申请。已开通用户需客户端支持Keyboard Interactive认证。

### 作业一直PENDING
用squeue -u $USER查看Reason列。常见原因：(Resources)资源不足等待、(Priority)优先级不足、(Dependency)依赖的作业未完成、(AssocGrp...)达到QoS作业数上限。

### 作业运行失败
首选查看作业脚本设定的日志文件slurm-<JobID>.out和slurm-<JobID>.err。辅助：scontrol show job <JobID>查看State/ExitCode。注意TIMEOUT信息不写入.out文件，在.err或squeue状态字段中。

### 如何访问/public下的软件
使用module avail查看可用模块，module load <软件名>加载。推荐用Conda管理个人Python环境。

### 查看GPU使用情况
squeue --format="%i %P %j %b %M"查看GPU分配。登录节点无GPU，需在计算节点或通过squeue查看。

### 磁盘配额不足
检查/public下文件大小，清理不必要的输出和临时文件。

### 权限错误
用户无sudo权限。如遇到Permission denied，检查输出路径是否在自己的目录下。计算节点/var/spool/slurmd/目录不可写。

### conda命令找不到
需先加载miniconda模块：module load miniconda/py312，然后source /public/home/$USER/miniconda3/etc/profile.d/conda.sh。脚本中如未加载模块直接调用conda会报"conda: command not found"。

### module加载失败
实际报错格式为"ERROR: Unable to locate a modulefile for '<名称>'"，检查模块名和版本是否正确（module avail查看）。

---

## 平台限制与注意事项

1. 无sudo权限，不可安装系统级软件
2. sacct在本平台功能可能受限，不可完全依赖
3. 登录节点无GPU，nvidia-smi在登录节点无法使用
4. 脚本中如无set -e，错误后命令会继续执行（不会自动停止）
5. 命令行参数优先级高于脚本内#SBATCH参数
6. Token（JWT）默认1天有效期，过期需重新生成
7. 数据放/public共享目录，/tmp各节点独立且用完即清
8. 比赛指定分区为P107-RTX5090和P107-A100
9. QoS限制：RTX5090最多4 GPU/作业，A100最多2 GPU/作业
10. SSH需要公钥认证 + Google Authenticator二次验证

---

## REST API使用

生成Token：scontrol token lifespan=86400，输出SLURM_JWT=eyJhbG...，取=后面部分。

Python代码示例：
```python
import requests, os
BASE = "http://107.ustc.edu.cn:6820"
TOKEN = os.environ.get("SLURM_JWT")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# 查询作业
r = requests.get(f"{BASE}/slurm/v0.0.41/jobs", headers=HEADERS)

# 查询指定分区
r = requests.get(f"{BASE}/slurm/v0.0.41/jobs?partition=P107-RTX5090", headers=HEADERS)

# 提交作业
payload = {"script": script_content, "job": {"partition": "P107-RTX5090", "name": "api-job", "nodes": 1, "time": 60}}
r = requests.post(f"{BASE}/slurm/v0.0.41/jobs/submit", json=payload, headers=HEADERS)

# 取消作业
r = requests.delete(f"{BASE}/slurm/v0.0.41/job/{job_id}", headers=HEADERS)
```

安全提醒：Token等于密码，不要明文写在脚本中或提交到Git，应使用环境变量。

---

## Slurm架构（背景知识）

Slurm由一组守护进程协作：munge（通信认证）、mariadb（系统数据库）、slurmdbd（作业记账）、slurmctld（主控调度，运行在管理节点tradmin-01）、slurmd（计算节点代理）、slurmrestd（REST API网关）。启动顺序：munge → mariadb → slurmdbd → slurmctld → slurmd。本平台采用Configless模式，节点启动时从slurmctld自动拉取配置。对普通用户而言，只需知道通过srun/sbatch/squeue等命令与slurmctld交互，作业在计算节点执行。

---

## 关键链接汇总

- SCOW门户：https://107.ustc.edu.cn
- Grafana监控：https://107.ustc.edu.cn:3000（aiviewer/aiviewer）
- HPC监控面板：http://114.214.255.131/hpc/（校内）
- REST API：http://107.ustc.edu.cn:6820
- USTC GitLab：https://git.ustc.edu.cn
- Slurm官方文档：https://slurm.schedmd.com/documentation.html
- 开源镜像：https://mirrors.ustc.edu.cn
- 问题反馈：hmli@ustc.edu.cn
