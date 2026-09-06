# Slurm 用户命令精选参考

> 来源：107平台培训PDF + docs-main + 平台实测
> 适用对象：HPC Copilot 智能体知识库 / 初学者速查

---

## 命令速查表

| 命令 | 用途 | 常用示例 |
|------|------|----------|
| sinfo | 查看分区和节点状态 | sinfo, sinfo -N -p P107-RTX5090 |
| squeue | 查看作业队列 | squeue -u $USER, squeue -p Students |
| sbatch | 提交批处理作业 | sbatch job.sh |
| srun | 交互式运行 | srun -p Students --gres=gpu:1 --pty bash |
| scancel | 取消作业 | scancel <JobID>, scancel -u $USER -t PENDING |
| scontrol | 查看/修改作业和分区详情 | scontrol show job <JobID>, scontrol show part |
| sacct | 查看作业历史 | sacct -u $USER -X --format=JobID,State |
| sacctmgr | 管理账户/QOS | sacctmgr show assoc user=$USER |

---

## sinfo — 查看集群与分区状态

```bash
sinfo                          # 查看所有分区和节点状态
sinfo -N                       # 按节点逐行显示
sinfo -N -p P107-RTX5090      # 查看指定分区的每个节点
sinfo -s                       # 紧凑概览格式
sinfo --noheader               # 跳过表头（也可用 -h）
```

输出字段：PARTITION分区名（*=默认）、NODES该状态节点数、STATE状态码、NODELIST节点列表。

状态码含义：idle=空闲可调度、mix=部分占用、alloc=全部占满、down*=宕机（*表示已降级）、drng=排空中（正在恢复）、comp=即将完成。

同一分区可有多行（每行一种状态），总节点数=各状态行NODES之和。例如P107-RTX5090显示"1 mix + 14 idle"表示共15个节点，1个部分占用14个空闲。

---

## squeue — 查看作业队列

```bash
squeue                         # 查看所有作业
squeue -u $USER                # 只看自己的作业
squeue -p P107-RTX5090        # 查看指定分区的作业
squeue --noheader              # 跳过表头（也可用 -h）
squeue --format="%i %P %j %b %M"  # 自定义格式（含GPU分配）
```

输出字段：JOBID作业编号、PARTITION所在分区、NAME作业名（-J参数）、ST状态码、TIME已运行时长、NODELIST运行节点/排队原因。

状态码：R=RUNNING运行中、PD=PENDING排队中、CG=COMPLETING退出中、CD=COMPLETED已完成、F=FAILED失败、CA=CANCELLED取消、S=SUSPENDED挂起。

排队原因（Reason列）速查：
- (Resources) 资源不足，等待分配
- (Priority) 优先级不足
- (Dependency) 依赖的作业未完成
- (AssocGrpJobsLimit) 达到QOS作业数上限
- (AssocGrpCPULimit) 达到CPU配额上限
- (BeginTime) 等待开始时间到达

---

## sbatch — 提交批处理作业

```bash
sbatch job.sh                  # 提交作业脚本
sbatch -p CPU-6530 job.sh      # 命令行指定分区（覆盖脚本内的#SBATCH设置）
sbatch --test-only job.sh      # 仅测试调度可行性，不实际运行
```

输出：Submitted batch job <JobID>

命令行参数优先级高于脚本中的#SBATCH参数。

---

## SBATCH 参数详解

| 参数 | 短选项 | 说明 | 示例 |
|------|--------|------|------|
| --job-name | -J | 作业名称 | -J my_train |
| --partition | -p | 目标分区 | -p P107-RTX5090 |
| --nodes | -N | 申请节点数 | -N 2 |
| --gres | | 通用资源（GPU） | --gres=gpu:2 或 --gres=gpu:5090:1 |
| --ntasks | -n | 总MPI进程数 | -n 4 |
| --cpus-per-task | -c | 每任务CPU核数 | -c 8 |
| --mem | | 内存上限 | --mem=64G |
| --time | -t | 最大运行时长 | --time=02:00:00 |
| --output | -o | 标准输出文件 | -o logs/%x-%j.out |
| --error | -e | 错误输出文件 | -e logs/%x-%j.err |
| --qos | | 指定QOS | --qos=qos_stu_default |
| --array | | 数组作业 | --array=1-10 |
| --account | -A | 账户 | -A myaccount |
| --mail-type | | 邮件通知类型 | --mail-type=END |
| --exclusive | | 独占整个节点 | |

文件名占位符：%j=JobID、%x=作业名、%t=数组任务ID。

---

## srun — 交互式运行

```bash
# CPU交互式（调试用）
srun -p Students --qos=qos_stu_default -c 1 -t 00:10:00 --pty bash

# GPU交互式
srun -p Students --qos=qos_stu_default --gres=gpu:1 -c 1 -t 00:10:00 --pty bash

# 直接运行命令
srun -p P107-RTX5090 --gres=gpu:1 --time=01:00:00 python train.py
```

交互式会话仅用于调试。退出后资源自动释放。长时间任务请用sbatch。

---

## scancel — 取消作业

```bash
scancel 2049                   # 取消单个作业
scancel -u $USER -t PENDING    # 取消自己所有排队作业
scancel -p P107-RTX5090       # 取消某分区所有自己的作业
```

只能取消自己的作业。

---

## scontrol — 查看详情

```bash
scontrol show job 18447        # 查看单个作业完整配置（排错首选）
scontrol show part             # 查看所有分区详情
scontrol show part P107-RTX5090  # 查看指定分区
scontrol show nodes            # 查看所有节点状态
scontrol show hostnames $SLURM_JOB_NODELIST  # 展开节点列表
```

scontrol show job 关键字段：
- JobId: 作业编号
- JobState: 当前状态
- Partition: 所在分区
- RunTime: 已运行时长
- TimeLimit: 最大运行时间
- NodeList: 分配到的节点
- NumCPUs: 分配的CPU核数
- ReqTRES: 请求的资源（cpu/gpu/内存）
- AllocTRES: 实际分配的资源
- QOS: 使用的QOS
- Command: 执行的命令
- Reason: 排队原因（PENDING时查看）
- ExitCode: 退出码（FAILED时查看，格式为exit:signal）

---

## sacct — 查看作业历史

```bash
sacct -u $USER -X --format=JobID,JobName,Elapsed,State
sacct -u $USER -X --format=JobID,Partition,State,ExitCode
```

常用输出列：JobID作业编号、JobName作业名、Partition分区、AllocCPUS分配CPU数、ReqMem申请内存、Elapsed已运行时长、State最终状态、ExitCode退出码（0:0=正常）。

注意：sacct在107平台可能功能受限，不可完全依赖。

---

## sacctmgr — 查看QOS和账户

```bash
sacctmgr show assoc user=$USER format=User,Partition,QOS  # 查看自己可用的QOS
sacctmgr list qos -p             # 列出所有QOS
sacctmgr show user $USER withassoc  # 查看用户详细信息
```

---

## 作业管理（挂起/恢复/重排队）

```bash
# 排队中的作业
scontrol hold 2049              # 挂起（暂停分配）
scontrol release 2049           # 释放（恢复排队）

# 运行中的作业
scontrol suspend 2049           # 暂停运行（让出资源）
scontrol resume 2049            # 继续运行

# 重新排队
scontrol requeue 2049           # 让作业重新排队（适用于被抢占或失败的作业）
```

使用场景：排队久等高优先级作业来临时hold自己的低优作业腾位置；运行中的作业被抢占后用requeue等资源够再跑。

---

## Slurm 环境变量（仅作业脚本内有效）

| 变量 | 含义 |
|------|------|
| $SLURM_JOB_ID | 作业编号 |
| $SLURM_JOB_NAME | 作业名称 |
| $SLURM_SUBMIT_DIR | 提交作业的目录 |
| $SLURM_JOB_NODELIST | 分配的节点列表 |
| $SLURM_NNODES | 节点总数 |
| $SLURM_NTASKS | 总任务数（-n） |
| $SLURM_CPUS_PER_TASK | 每任务CPU（-c） |
| $SLURM_GPUS | 分配到的GPU数 |
| $SLURM_GPUS_PER_NODE | 每节点GPU数 |
| $SLURM_NPROCS | 分配的CPU核数 |
| $SLURM_MEM_PER_NODE | 每节点内存 |
| $SLURM_PARTITION | 当前分区名 |
| $SLURM_ARRAY_TASK_ID | 数组作业任务编号 |
| $SLURM_JOB_CPUS_PER_NODE | 各节点CPU核数 |
| $SLURM_TASKS_PER_NODE | 各节点任务数 |

重要：这些变量仅在sbatch提交的作业脚本内有效，在登录节点Shell中无定义。

---

## 107平台分区速查

| 分区 | 节点 | CPU | GPU | 说明 |
|------|------|-----|-----|------|
| CPU-6530* | anode[01-15] | 1920 | — | 默认分区 |
| CPU-8358P | anode[16-17] | 256 | — | 大内存 |
| GPU-RTX5090 | anode[01-15] | 1920 | 120 | GPU通用 |
| GPU-A100 | anode[16-17] | 256 | 16 | A100大模型 |
| P107-RTX5090 | anode[01-15] | 1920 | 120 | 比赛GPU |
| P107-A100 | anode[16-17] | 256 | 16 | 比赛高精度 |
| Students | anode[05-17] | 1664 | 104 | 学生统一认证 |

---

## 107平台QOS速查

| QOS | 分区 | MaxJobs | GrpTRES | 时长 |
|-----|------|---------|---------|------|
| qos_p107-rtx5090 | P107-RTX5090 | 4 | cpu=16, gpu=4 | 不限 |
| qos_p107-a100 | P107-A100 | 4 | cpu=16, gpu=2 | 不限 |
| qos_stu_default | Students | — | 4CPU/1GPU/16G | 4h |
| qos_stu_small | Students | — | 8CPU/1GPU/32G | 8h |
| qos_stu_medium_2gpu | Students | — | 24CPU/2GPU/128G | 12h |
| qos_stu_long | Students | — | 16CPU/1GPU/64G | 72h |
| qos_stu_cpu_long | Students | — | 32CPU/0GPU/128G | 72h |

---

## 排查流程决策树

作业出问题时按以下顺序排查：

1. 作业在哪？→ squeue -u "$USER"
   - 看不到 → 作业已结束，用sacct查历史
   - PD → 看Reason列，跳到步骤2
   - R → 作业在运行，跳到步骤4
   - F/CA → 作业失败/被取消，跳到步骤3

2. 为什么排队？→ scontrol show job <JobID> 看Reason
   - Resources → 资源不足，减少申请或等待
   - Priority → 优先级不够，等更高优作业完成
   - AssocGrpJobsLimit → 达到QOS上限，等现有作业完成或减少申请
   - Dependency → 依赖的作业未完成

3. 为什么失败？→ 查看日志文件
   - tail -n 80 logs/*.err → 错误日志
   - tail -n 80 logs/*.out → 程序输出
   - scontrol show job <JobID> → 看ExitCode（格式exit:signal）
   - ExitCode非0 → 程序自身错误
   - ExitCode=0:9(SIGKILL) → 被系统杀死（OOM或超时）

4. 运行中但异常？
   - GPU任务：在脚本中加入nvidia-smi检查
   - 检查日志是否在更新：tail -f logs/*.out
   - 检查运行时长是否合理
