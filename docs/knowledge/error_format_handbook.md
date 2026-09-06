# 107平台常见错误格式手册

> 来源：benchmark 74个测试案例 + 107平台实测（2026-08-27）
> 适用对象：HPC Copilot 智能体知识库 / RAG检索源
> 用途：让agent识别"平台实际输出的长什么样"，而非只匹配通用Slurm文档格式

---

## INVALID_QOS — 无效QOS

触发场景：sbatch提交时指定的QOS名称错误或无权限。

实际输出格式：
```
sbatch: error: Batch job submission failed: Invalid qos specification
```

带Hint的变体：
```
sbatch: error: Batch job submission failed: Invalid qos specification
Hint: QoS 'qos_p107' is not valid for partition P107-A100
```

带资源分配失败的变体：
```
$ sbatch train.sh
sbatch: error: Batch job submission failed: Invalid qos specification
sbatch: error: Unable to allocate resources
```

诊断要点：关键词"Invalid qos specification"。用户常犯错误是把分区名和QOS名搞混（如写qos_p107而不是qos_p107-rtx5090）。

---

## INVALID_PARTITION — 无效分区

触发场景：指定的分区名拼写错误或不存在。

真实平台输出（107.ustc.edu.cn 2026-08-27实测）：
```
sbatch: error: invalid partition specified: DO_NOT_EXIST
sbatch: error: Batch job submission failed: Invalid partition name specified
```

旧版Slurm文档格式：
```
sbatch: error: Batch job submission failed: Invalid partition specification
```

带分区详情的变体：
```
sbatch: error: Batch job submission failed: Invalid partition specification
sbatch: error: Partition 'P107-V100' not found
```

诊断要点：107平台实际输出是"Invalid partition name specified"而非文档中的"Invalid partition specification"，两种都要匹配。

---

## WORKDIR_NOT_FOUND — 工作目录失效

触发场景：提交作业时所在目录已被删除或移动。

标准格式：
```
slurmstepd: error: chdir(/home/user/deleted_dir) failed: No such file or directory
slurmstepd: error: _run_prolog: chdir failed
```

getcwd失败：
```
slurmstepd: error: getcwd failed: No such file or directory
slurmstepd: error: Unable to set working directory
```

无法打开工作目录：
```
slurmstepd: error: can't open /data/tmp/exp42 working directory
```

诊断要点：关键词"chdir failed"/"getcwd failed"/"can't open.*working"。用户需要cd $HOME后重新提交。

---

## ENTRYPOINT_NOT_FOUND — 入口文件不存在

触发场景：作业脚本中指定的Python脚本或可执行文件不存在。

真实平台输出（107.ustc.edu.cn 2026-08-27实测）：
```
python: can't open file '/home/scc/sa25232066/benchmark_test/nonexistent_script.py': [Errno 2] No such file or directory
```

Python FileNotFoundError：
```
Traceback (most recent call last):
  File "run.py", line 5, in <module>
    data = open("data/train.csv")
FileNotFoundError: [Errno 2] No such file or directory: 'data/train.csv'
```

bash找不到脚本：
```
/var/spool/slurm/job12345/slurm_script: line 12: ./train.sh: No such file or directory
```

诊断要点：关键词"can't open file"/"No such file or directory"/"FileNotFoundError"。注意区分是入口文件不存在还是数据文件不存在。

---

## MODULE_NOT_FOUND — 模块不存在

触发场景：module load指定的软件模块名称或版本错误。

真实平台输出（107.ustc.edu.cn 2026-08-27实测）：
```
ERROR: Unable to locate a modulefile for 'cuda/99.99'
```

Lmod格式变体：
```
Lmod has detected the following error: The following module(s) are unknown: 'cuda/12.0'
```

带搜索提示：
```
Lmod has detected the following error:
Unable to find: "tensorflow/2.15".
Searching modules...
```

诊断要点：107平台使用Lmod，实际格式是"ERROR: Unable to locate a modulefile for"。用户应用module avail查看可用模块。

---

## TIMEOUT — 作业超时

触发场景：作业运行时间超过--time限制被系统终止。

标准Slurm超时取消：
```
slurmstepd: error: *** JOB 12345 ON gpu-node-01 CANCELLED AT 2026-07-29T10:30:00 DUE TO TIME LIMIT ***
```

sacct风格状态输出：
```
JobID    State      Reason
-----    -----      ------
12345    TIMEOUT    TimeLimit
```

作业输出中的超时标记：
```
Epoch 3/10: loss=0.543
Epoch 4/10: loss=0.421
slurmstepd: error: Detected job exceeded time limit, terminating
```

诊断要点：关键词"DUE TO TIME LIMIT"/"CANCELLED AT.*TIME"/"exceeded time limit"。注意：TIMEOUT信息不写入slurm-xxx.out文件，在.err或squeue状态字段中。

---

## OUT_OF_MEMORY — 内存超限

触发场景：作业使用的内存超过--mem申请的上限。

Slurm OOM killer：
```
slurmstepd: error: Detected 1 oom-kill event(s) in StepId=12345.batch cgroup
Some of the step processes have been killed
```

内存限制超出：
```
slurmstepd: error: Exceeded job memory limit at some point
slurmstepd: error: Job 12345 exceeded memory limit
```

被系统Killed（最简形式）：
```
Training model...
Loading dataset (12GB)...
Killed
```

内核OOM日志：
```
[12345.678] oom-kill: invoked oom-killer: gfp_mask=0x6200ca(GFP_HIGHUSER_MOVABLE)
[12345.679] Out of memory: Killed process 98765 (python) total-vm:32000000kB
```

诊断要点：关键词"oom-kill"/"exceeded memory limit"/"Killed"。裸"Killed"需结合上下文判断（也可能是其他信号）。

---

## PROGRAM_EXIT_NONZERO — 程序非零退出

触发场景：程序自身抛出异常或返回非零退出码。

真实平台输出（107.ustc.edu.cn 2026-08-27实测）：
```
Testing runtime exception...
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ZeroDivisionError: division by zero
```

ModuleNotFoundError：
```
Traceback (most recent call last):
  File "train.py", line 1, in <module>
    import torch
ModuleNotFoundError: No module named 'torch'
```

slurmstepd退出码报告：
```
slurmstepd: error: Detected job exit code 1
Job completed with exit code 1
```

CUDA error导致非零退出：
```
RuntimeError: CUDA error: device-side assert triggered
CUDA kernel errors might be asynchronously reported at some other API call
exit code 1
```

诊断要点：关键词"Traceback"/"exit code [1-9]"/"ModuleNotFoundError"/"ImportError"。这是兜底规则，优先级应低于更具体的错误类型。

---

## DISK_QUOTA — 磁盘配额不足

触发场景：写入文件时超出磁盘配额或磁盘空间满。

Python OSError：
```
OSError: [Errno 122] Disk quota exceeded
Error writing checkpoint to /home/sa25232066/checkpoints/epoch5.pt
```

系统命令写入失败：
```
cp: error writing '/home/sa25232066/data/output.tar': No space left on device
cp: failed to extend '/home/sa25232066/data/output.tar': No space left on device
```

conda安装时配额不足：
```
Collecting package metadata (current_repodata.json): done
ERROR conda.core.link:_execute(700): An error occurred while installing package 'pytorch-2.1.0'.
OSError: [Errno 122] Disk quota exceeded
```

诊断要点：关键词"Disk quota exceeded"/"No space left on device"/"Errno 122"/"Errno 28"。

---

## PERMISSION_DENIED — 权限问题

触发场景：写入无权限的目录或执行无权限的脚本。

真实平台输出（107.ustc.edu.cn 2026-08-27实测）：
```
Testing permission denied...
/var/spool/slurmd/job45841/slurm_script: line 9: /root/benchmark_should_fail.txt: Permission denied
```

脚本无执行权限：
```
/var/spool/slurm/job5678/slurm_script: line 8: ./run_experiment.sh: Permission denied
```

Python写文件权限错误：
```
Traceback (most recent call last):
  File "train.py", line 88, in <module>
    torch.save(model.state_dict(), "/shared/models/best.pt")
PermissionError: [Errno 13] Permission denied: '/shared/models/best.pt'
```

诊断要点：关键词"Permission denied"/"PermissionError"/"Errno 13"。107平台用户无sudo权限，写/root或系统目录会触发此错误。

---

## GPU_OOM — GPU显存不足

触发场景：深度学习训练时GPU显存不够。

PyTorch CUDA OOM：
```
torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB.
GPU 0 has a total capacity of 39.56 GiB of which 1.23 GiB is free.
```

CUDA运行时错误：
```
RuntimeError: CUDA error: out of memory
CUDA error: cudaErrorMemoryAllocation
```

TensorFlow GPU OOM：
```
tensorflow.python.framework.errors_impl.ResourceExhaustedError:
OOM when allocating tensor with shape[32,512,512,3]
[_Derived_]  All 1 GPUs are allocated but 2 requested.
```

CUDA malloc失败详情：
```
CUDA out of memory. Tried to allocate 4.00 GiB (GPU 0; 79.15 GiB total capacity;
74.32 GiB already allocated; 2.11 GiB free)
```

诊断要点：关键词"CUDA out of memory"/"OutOfMemoryError"/"cudaErrorMemoryAllocation"/"ResourceExhaustedError"。建议减小batch size或模型大小。

---

## NODE_FAIL — 节点故障

触发场景：计算节点硬件故障或意外重启。

Slurm NODE_FAIL状态：
```
slurmstepd: error: Node failure on gpu-node-03
Job state changed to NODE_FAIL
```

节点意外重启：
```
slurmstepd: error: Node gpu-node-01 unexpectedly rebooted
slurmstepd: error: Job 23456 terminated due to node failure
```

batch step failure：
```
slurmstepd: error: Detected batch step failure
srun: error: gpu-node-02: task 0: Terminated (Node failure)
```

诊断要点：关键词"Node failure"/"unexpectedly rebooted"/"NODE_FAIL"/"Node.*terminated"。用户无法修复，需重新提交或联系管理员。

---

## PREEMPTED — 作业被抢占

触发场景：高优先级作业抢占资源，低优先级作业被终止。

高优先级抢占：
```
slurmstepd: error: Job 34567 preempted by higher priority job
Job state: PREEMPTED
```

squeue显示PREEMPTED：
```
JOBID PARTITION NAME USER STATE TIME NODES
34567 Students train.py sa25232066 PREEMPTED 0:45:12 1
```

诊断要点：关键词"PREEMPTED"/"preempted by higher priority"。用户可用scontrol requeue重新排队。

---

## CONDA_ENV — Conda环境问题

触发场景：作业脚本中未正确初始化conda或环境不存在。

真实平台输出（107.ustc.edu.cn 2026-08-27实测）：
```
Testing conda without init...
/var/spool/slurmd/job45844/slurm_script: line 9: conda: command not found
Python 3.12.3
```

conda activate未配置：
```
CommandNotFoundError: Your shell has not been properly configured to use 'conda activate'.
To initialize your shell, run: $ conda init <SHELL_NAME>
```

环境不存在：
```
EnvironmentNameNotFound: Could not find conda environment: myenv
Valid environments: base
```

诊断要点：关键词"conda: command not found"/"conda activate"/"Could not find conda environment"。107平台需先module load miniconda/py312再source conda.sh。

---

## CUDA_DEVICE — GPU设备/驱动错误

触发场景：CUDA设备不可用、驱动版本不匹配或GPU被占用。

无CUDA设备：
```
AssertionError: Torch not compiled with CUDA enabled
No CUDA-capable device is detected
```

驱动版本过旧：
```
RuntimeError: The NVIDIA driver on your system is too old (found version 11080).
Please update your GPU driver to a compatible version.
```

GPU被占用：
```
RuntimeError: CUDA error: all CUDA-capable devices are busy or unavailable
cudaErrorDevicesUnavailable
```

诊断要点：关键词"Torch not compiled with CUDA"/"NVIDIA driver.*too old"/"devices are busy"/"No CUDA-capable device"。常见于安装了CPU版PyTorch或在登录节点运行。

---

## INVALID_TIME — 时间格式错误

触发场景：--time参数格式不正确。

sbatch时间格式错误：
```
sbatch: error: Invalid time limit specification: --time=2days
```

资源分配失败（时间相关）：
```
sbatch: error: Batch job submission failed: Unable to allocate resources
sbatch: error: Invalid time limit specification
```

诊断要点：关键词"Invalid time limit specification"。正确格式：--time=HH:MM:SS 或 --time=D-HH:MM:SS。

---

## DEPENDENCY — 作业依赖问题

触发场景：依赖的前置作业失败或未完成。

依赖作业失败：
```
slurmstepd: error: Dependency failed: job 11111 did not complete successfully
Job 11112 cancelled due to dependency
```

依赖链断裂：
```
sbatch: error: Batch job submission failed: Dependency satisfied but job 11111 already completed with error
```

诊断要点：关键词"Dependency failed"/"dependency"/"did not complete successfully"。用户应先检查依赖作业的状态。

---

## ACCOUNT_ISSUE — 账号/关联问题

触发场景：账号无权限或未关联到请求的账户。

账号无效：
```
sbatch: error: Batch job submission failed: Invalid account specification
Access denied for account 'research_group'
```

关联未找到：
```
sbatch: error: Batch job submission failed: Association not found
User sa25232066 is not associated with the requested account
```

诊断要点：关键词"Invalid account"/"Association not found"/"Access denied for account"。

---

## QOS限制类错误（非INVALID_QOS）

触发场景：QOS本身有效但超出配额限制。

超过最大运行时间：
```
sbatch: error: Batch job submission failed: QOSMaxWallDurationPerJobLimit
```

超过CPU配额：
```
sbatch: error: Batch job submission failed: QOSMaxCpuPerUserLimit
```

诊断要点：关键词"QOSMax"。与INVALID_QOS不同，这里是QOS名称正确但申请的资源超过了该QOS允许的上限。

---

## 错误格式匹配注意事项

1. 107平台的实际报错格式可能与Slurm官方文档不同——以实测为准
2. invalid_partition：平台实际输出"Invalid partition name specified"，非文档中的"Invalid partition specification"
3. module错误：实际格式"ERROR: Unable to locate a modulefile for"，非Lmod标准格式
4. TIMEOUT信息不写入slurm-xxx.out，在.err文件或squeue状态字段中
5. 脚本无set -e时，错误后命令会继续执行，最终输出可能包含多个错误
6. 权限错误由bash报告，格式为"/var/spool/slurmd/jobNNN/slurm_script: line N: ...: Permission denied"
7. conda未初始化时报"conda: command not found"，不是Python错误
8. 裸"Killed"可能是OOM也可能是其他信号，需结合上下文
