"""预设作业模板 — 复杂工作流的确定性执行引擎。

设计思路：
- 对话框只做纯聊天（无 tools），保证速度和稳定
- 复杂操作（写脚本+提交+轮询+看输出）由模板预设流水线完成
- 每个阶段的结果实时推送前端，用户看到清晰的进度
- 最后一个阶段调用 LLM 做总结（体现"智能体"而非纯脚本）

模板阶段类型：
- write_file: 写文件到集群
- run_command: 执行命令并收集输出
- poll_jobs: 轮询作业状态直到终态或超时
- llm_summarize: 把前面所有输出交给 LLM 做智能总结
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TemplateStage:
    """单个执行阶段。"""
    label: str              # 前端显示的阶段名（如"写入训练脚本"）
    action: str             # write_file | run_command | poll_jobs | llm_summarize
    params: dict = field(default_factory=dict)
    # 可选：阶段输出变量名，供后续阶段引用（如 {job_id}）
    output_var: Optional[str] = None


@dataclass
class JobTemplate:
    """完整的模板工作流。"""
    id: str
    icon: str
    title: str
    desc: str
    tag: str
    stages: list[TemplateStage]
    overview: str = ""
    conditions: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    work_dir: str = ""
    artifacts: list[str] = field(default_factory=list)


# ─── 模板定义 ───────────────────────────────────────────────

GPU_VECTOR_ADD_SOURCE = '''\
#include <cuda_runtime.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <vector>

#define CUDA_CHECK(call) do { \\
    cudaError_t error = (call); \\
    if (error != cudaSuccess) { \\
        std::cerr << "CUDA error: " << cudaGetErrorString(error) << std::endl; \\
        return 1; \\
    } \\
} while (0)

__global__ void vector_add(const float* a, const float* b, float* c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}

int main() {
    const int n = 1 << 23;  // 8,388,608 elements
    const size_t bytes = static_cast<size_t>(n) * sizeof(float);
    std::vector<float> a(n, 1.25f), b(n, 2.75f), c(n);
    float *da = nullptr, *db = nullptr, *dc = nullptr;

    cudaDeviceProp prop{};
    CUDA_CHECK(cudaGetDeviceProperties(&prop, 0));
    std::cout << "GPU              : " << prop.name << '\\n';
    std::cout << "Elements         : " << n << '\\n';
    std::cout << "Data size        : " << (3.0 * bytes / 1024 / 1024) << " MiB\\n";

    CUDA_CHECK(cudaMalloc(&da, bytes));
    CUDA_CHECK(cudaMalloc(&db, bytes));
    CUDA_CHECK(cudaMalloc(&dc, bytes));
    CUDA_CHECK(cudaMemcpy(da, a.data(), bytes, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(db, b.data(), bytes, cudaMemcpyHostToDevice));

    cudaEvent_t start, stop;
    CUDA_CHECK(cudaEventCreate(&start));
    CUDA_CHECK(cudaEventCreate(&stop));
    const int threads = 256;
    const int blocks = (n + threads - 1) / threads;
    CUDA_CHECK(cudaEventRecord(start));
    vector_add<<<blocks, threads>>>(da, db, dc, n);
    CUDA_CHECK(cudaEventRecord(stop));
    CUDA_CHECK(cudaEventSynchronize(stop));
    CUDA_CHECK(cudaGetLastError());

    float milliseconds = 0.0f;
    CUDA_CHECK(cudaEventElapsedTime(&milliseconds, start, stop));
    CUDA_CHECK(cudaMemcpy(c.data(), dc, bytes, cudaMemcpyDeviceToHost));

    float max_error = 0.0f;
    for (int i = 0; i < n; ++i) max_error = std::max(max_error, std::fabs(c[i] - 4.0f));
    const double bandwidth = (3.0 * bytes) / (milliseconds * 1.0e6);
    std::cout << "Kernel time      : " << milliseconds << " ms\\n";
    std::cout << "Approx bandwidth : " << bandwidth << " GB/s\\n";
    std::cout << "Maximum error    : " << max_error << '\\n';
    std::cout << "Result           : " << (max_error < 1e-6f ? "PASS" : "FAIL") << '\\n';

    cudaEventDestroy(start); cudaEventDestroy(stop);
    cudaFree(da); cudaFree(db); cudaFree(dc);
    return max_error < 1e-6f ? 0 : 2;
}
'''

GPU_VECTOR_ADD_SBATCH = '''\
#!/bin/bash
#SBATCH --job-name=cuda_vector_add
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --time=00:03:00
#SBATCH --output=slurm-%j.out

STATUS_FILE="job-${SLURM_JOB_ID}.status"
record_exit() {
  code=$?
  printf '%s\\n' "$code" > "$STATUS_FILE"
}
trap record_exit EXIT

set -eo pipefail
on_error() {
  code=$?
  echo "Status       : FAILED"
  echo "Failed line  : ${BASH_LINENO[0]}"
  exit "$code"
}
trap on_error ERR

# Slurm 会继承提交终端的环境。先移除 Python/Conda 遗留变量，避免污染
# module 的辅助程序和 CUDA 编译工具链。
unset PYTHONHOME PYTHONPATH
module unload python3.12 miniconda/py312 2>/dev/null || true
unset PYTHONHOME PYTHONPATH

echo "========================================"
echo "  Classic CUDA Vector Addition"
echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Compute node: $(hostname)"
echo "Started at  : $(date '+%F %T')"
module load cuda/13.0
unset PYTHONHOME PYTHONPATH
command -v nvcc
nvcc --version | tail -1
nvidia-smi -L
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
echo "--- Compiling vector_add.cu ---"
nvcc -O2 -arch=sm_80 vector_add.cu -o vector_add
echo "--- Running on GPU ---"
./vector_add
echo "Finished at : $(date '+%F %T')"
echo "Status       : SUCCESS"
'''

BASIC_SLURM_SBATCH = '''\
#!/bin/bash
#SBATCH --job-name=hello_slurm
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:02:00
#SBATCH --output=slurm-%j.out

STATUS_FILE="job-${SLURM_JOB_ID}.status"
record_exit() {
  code=$?
  printf '%s\\n' "$code" > "$STATUS_FILE"
}
trap record_exit EXIT

set -eo pipefail
echo "========================================"
echo "  HPC Copilot - My First Slurm Job"
echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Compute node: $(hostname)"
echo "Started at  : $(date '+%F %T')"
echo
echo "Running a small parallel-style calculation..."
total=0
for step in 1 2 3 4 5; do
  value=$((step * step * 100))
  total=$((total + value))
  echo "[${step}/5] partial result = ${value}"
  sleep 1
done
echo
echo "Final result : ${total}"
echo "CPU cores    : $(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo unknown)"
echo "Memory info  : $(free -h 2>/dev/null | awk '/Mem:/ {print $2 " total, " $7 " available"}' || echo unavailable)"
echo "Finished at  : $(date '+%F %T')"
echo "Status       : SUCCESS"
'''

FIRE_GROWTH_SCRIPT = '''\
"""Classic t-squared fire growth + Alpert ceiling-jet + sprinkler response."""
import csv
import math

ALPHA = 0.0469          # kW/s^2, fast t-squared fire
MAX_HRR = 3000.0        # kW
FIRE_DIAMETER = 0.8     # m
CEILING_HEIGHT = 3.0    # m
RADIAL_DISTANCE = 2.0   # m, fire axis to sprinkler
AMBIENT_TEMP = 20.0     # degC
SPRINKLER_RTI = 50.0    # (m*s)^0.5
ACTIVATION_TEMP = 68.0  # degC
HEAT_OF_COMBUSTION = 18000.0  # kJ/kg
DT = 0.5
DURATION = 180.0


def alpert_ceiling_jet(q_kw, height, radius):
    """Return ceiling-jet temperature rise (K) and velocity (m/s)."""
    if q_kw <= 0:
        return 0.0, 0.0
    if radius / height <= 0.18:
        delta_t = 16.9 * q_kw ** (2.0 / 3.0) / height ** (5.0 / 3.0)
        velocity = 0.96 * (q_kw / height) ** (1.0 / 3.0)
    else:
        delta_t = 5.38 * (q_kw / radius) ** (2.0 / 3.0) / height
        velocity = 0.195 * q_kw ** (1.0 / 3.0) * height ** 0.5 / radius ** (5.0 / 6.0)
    return delta_t, velocity


rows = []
link_temp = AMBIENT_TEMP
activation_time = None
fuel_burned = 0.0
oxygen_consumed = 0.0
peak_flame_height = 0.0

for i in range(int(DURATION / DT) + 1):
    t = i * DT
    q_kw = min(ALPHA * t * t, MAX_HRR)
    flame_height = max(0.0, 0.235 * q_kw ** 0.4 - 1.02 * FIRE_DIAMETER) if q_kw else 0.0
    delta_t, velocity = alpert_ceiling_jet(q_kw, CEILING_HEIGHT, RADIAL_DISTANCE)
    gas_temp = AMBIENT_TEMP + delta_t
    if velocity > 0:
        link_temp += math.sqrt(velocity) / SPRINKLER_RTI * (gas_temp - link_temp) * DT
    if activation_time is None and link_temp >= ACTIVATION_TEMP:
        activation_time = t
    fuel_burned += q_kw / HEAT_OF_COMBUSTION * DT
    oxygen_consumed += q_kw / 13100.0 * DT
    peak_flame_height = max(peak_flame_height, flame_height)
    rows.append((t, q_kw, flame_height, gas_temp, velocity, link_temp))

with open("fire_results.csv", "w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["time_s", "hrr_kw", "flame_height_m", "ceiling_temp_c", "ceiling_velocity_m_s", "sprinkler_temp_c"])
    writer.writerows(rows)

print("=" * 66)
print("  Classic t-squared Compartment Fire Simulation")
print("=" * 66)
print(f"Growth coefficient : {ALPHA:.4f} kW/s^2 (fast fire)")
print(f"Compartment        : 6.0 m x 4.0 m x {CEILING_HEIGHT:.1f} m")
print(f"Sprinkler          : RTI={SPRINKLER_RTI:.0f}, activation={ACTIVATION_TEMP:.0f} C, radius={RADIAL_DISTANCE:.1f} m")
print("\\n time(s)   HRR(kW)  flame(m)  ceiling(C)  velocity(m/s)  link(C)")
print("-" * 66)
for row in rows[::int(10.0 / DT)]:
    print(f" {row[0]:6.0f}  {row[1]:8.1f}  {row[2]:8.2f}  {row[3]:10.1f}  {row[4]:13.2f}  {row[5]:7.1f}")

peak_hrr = rows[-1][1]
peak_ceiling_temp = max(row[3] for row in rows)
print("\\n" + "=" * 66)
print(f"Peak HRR           : {peak_hrr:.1f} kW")
print(f"Peak flame height  : {peak_flame_height:.2f} m")
print(f"Peak ceiling temp  : {peak_ceiling_temp:.1f} C")
print(f"Fuel burned        : {fuel_burned:.2f} kg")
print(f"Oxygen consumed    : {oxygen_consumed:.2f} kg")
print(f"Sprinkler response : {activation_time:.1f} s" if activation_time is not None else "Sprinkler response : not activated")
print("CSV output         : fire_results.csv")
print("Model status       : SUCCESS")
print("Note: engineering correlation model; not a substitute for CFD or design approval.")
'''

FIRE_GROWTH_SBATCH = '''\
#!/bin/bash
#SBATCH --job-name=fire_t2_case
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:02:00
#SBATCH --output=slurm-%j.out

STATUS_FILE="job-${SLURM_JOB_ID}.status"
record_exit() {
  code=$?
  printf '%s\\n' "$code" > "$STATUS_FILE"
}
trap record_exit EXIT

set -eo pipefail
on_error() {
  code=$?
  echo "Model status       : FAILED"
  echo "Failed line        : ${BASH_LINENO[0]}"
  exit "$code"
}
trap on_error ERR

# 这个算例只需要 Python 标准库。强制清理提交终端继承的 Python/Conda
# 变量并使用系统解释器，避免 PYTHONHOME 指向另一个 Python 安装。
unset PYTHONHOME PYTHONPATH
echo "========================================"
echo "  Fire Dynamics Engineering Case"
echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Compute node: $(hostname)"
echo "Started at  : $(date '+%F %T')"
PYTHON_BIN=/usr/bin/python3
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import encodings, csv, math; print("Python standard library: OK")'
"$PYTHON_BIN" -I fire_growth.py
echo "--- Result file ---"
ls -lh fire_results.csv
echo "Finished at : $(date '+%F %T')"
'''


TEMPLATES: dict[str, JobTemplate] = {}


def _register(t: JobTemplate):
    TEMPLATES[t.id] = t


_register(JobTemplate(
    id="gpu-training",
    icon="🧠",
    title="经典 CUDA 向量加法",
    desc="编译经典 CUDA 向量加法 → 提交到 A100 → 测量耗时与带宽 → AI 总结",
    tag="GPU 入门",
    overview=(
        "用 GPU 并行计算 8,388,608 个单精度浮点数的向量加法："
        "A 和 B 的每个元素分别为 1.25 和 2.75，理论结果应全部等于 4.0。"
    ),
    conditions=[
        "硬件：1 张 NVIDIA A100 GPU",
        "规模：8,388,608 个元素，输入与输出共约 96 MiB",
        "软件：CUDA 13.0，按 A100 的 sm_80 架构编译",
        "时限：3 分钟，单节点单任务",
    ],
    models=["CUDA 一维网格并行", "GPU Event 核函数计时", "CPU 逐元素误差校验"],
    outputs=["GPU 型号", "核函数耗时与估算显存带宽", "最大误差和 PASS/FAIL"],
    work_dir="~/gpu_test",
    artifacts=["~/gpu_test/vector_add.cu", "~/gpu_test/slurm-{job_id}.out"],
    stages=[
        TemplateStage(
            label="写入 CUDA 程序",
            action="write_file",
            params={"path": "~/gpu_test/vector_add.cu", "content": GPU_VECTOR_ADD_SOURCE},
        ),
        TemplateStage(
            label="写入 sbatch 脚本",
            action="write_file",
            params={"path": "~/gpu_test/submit.sh", "content": GPU_VECTOR_ADD_SBATCH},
        ),
        TemplateStage(
            label="提交作业",
            action="run_command",
            params={"command": "cd ~/gpu_test && sbatch submit.sh"},
            output_var="submit_output",
        ),
        TemplateStage(
            label="等待作业完成",
            action="poll_jobs",
            params={
                "interval": 1,
                "timeout": 180,
                "extract_job_id_from": "submit_output",
                "status_file": "~/gpu_test/job-{job_id}.status",
            },
            output_var="job_status",
        ),
        TemplateStage(
            label="查看输出日志",
            action="run_command",
            params={
                "command": "cat ~/gpu_test/slurm-{job_id}.out 2>/dev/null || echo '本次作业输出文件尚未生成'",
                "success_markers": ["Result           : PASS", "Status       : SUCCESS"],
            },
            output_var="job_output",
        ),
        TemplateStage(
            label="AI 智能总结",
            action="llm_summarize",
            params={
                "prompt": (
                    "用户刚刚在 HPC 集群上运行了经典 CUDA 向量加法算例。"
                    "以下是作业输出日志：\n\n{job_output}\n\n"
                    "请用初学者能懂的语气总结：1) CUDA 编译和 GPU 运行是否成功 "
                    "2) GPU 型号、元素数量、核函数耗时和估算带宽 3) PASS/FAIL 和最大误差代表什么 "
                    "4) 如果有报错，分析平台或 CUDA 环境原因并给修复建议。"
                    "这是 CUDA 向量加法计算，不是模型训练；不要使用训练耗时、精度、吞吐等措辞，"
                    "也不要推测日志中没有出现的温度、功耗、利用率等指标。只有日志明确出现 "
                    "Result: PASS 和 Status: SUCCESS 时才能判断成功。用少量 emoji 点缀。"
                ),
            },
        ),
    ],
))

_register(JobTemplate(
    id="slurm-first-job",
    icon="🚀",
    title="第一个 Slurm 作业",
    desc="生成最小 sbatch 脚本 → 提交 → 轮询状态 → 查看本次输出 → AI 总结",
    tag="入门",
    overview=(
        "运行一个不依赖额外软件的 Bash 小计算：依次计算 1²、2²、3²、4²、5²乘以 100，"
        "展示中间结果并求和，用来认识 Slurm 的提交、排队、运行和日志流程。"
    ),
    conditions=["资源：1 个节点、1 个任务", "时限：2 分钟", "实际计算约 5 秒"],
    models=["Bash 整数运算", "五步迭代与结果累加"],
    outputs=["每一步的部分结果", "最终求和结果", "计算节点、CPU 和内存信息"],
    work_dir="~/slurm_quickstart",
    artifacts=["~/slurm_quickstart/submit.sh", "~/slurm_quickstart/slurm-{job_id}.out"],
    stages=[
        TemplateStage(
            label="写入 sbatch 脚本",
            action="write_file",
            params={"path": "~/slurm_quickstart/submit.sh", "content": BASIC_SLURM_SBATCH},
        ),
        TemplateStage(
            label="提交作业",
            action="run_command",
            params={"command": "cd ~/slurm_quickstart && sbatch submit.sh"},
            output_var="submit_output",
        ),
        TemplateStage(
            label="等待作业完成",
            action="poll_jobs",
            params={
                "interval": 1,
                "timeout": 120,
                "extract_job_id_from": "submit_output",
                "status_file": "~/slurm_quickstart/job-{job_id}.status",
            },
            output_var="job_status",
        ),
        TemplateStage(
            label="查看输出日志",
            action="run_command",
            params={
                "command": "cat ~/slurm_quickstart/slurm-{job_id}.out 2>/dev/null || echo '本次作业输出文件尚未生成'",
                "success_markers": ["Status       : SUCCESS"],
            },
            output_var="job_output",
        ),
        TemplateStage(
            label="AI 智能总结",
            action="llm_summarize",
            params={
                "prompt": (
                    "用户刚刚在 HPC 集群上提交了第一个最小 Slurm 作业。"
                    "以下是作业输出日志：\n\n{job_output}\n\n"
                    "请用初学者能懂的语气总结：1) 作业是否成功 2) 作业号、运行节点和计算结果"
                    "3) 如果有报错，分析平台或 Slurm 层面的原因并给修复建议。用少量 emoji 点缀。"
                ),
            },
        ),
    ],
))

_register(JobTemplate(
    id="fire-growth",
    icon="🔥",
    title="t² 火灾增长与喷淋响应",
    desc="运行经典火灾增长模型 → 计算顶棚射流与喷头响应 → 输出 CSV → AI 解读",
    tag="火灾/燃烧",
    overview=(
        "模拟一个 6 m × 4 m × 3 m 房间内持续 180 秒的快速增长火灾，"
        "估算火焰高度、顶棚射流温度和速度，并计算 68 ℃ 喷头的热响应时间。"
    ),
    conditions=[
        "火灾增长：快速 t² 火，α=0.0469 kW/s²，热释放速率上限 3000 kW",
        "火源：直径 0.8 m，房间净高 3.0 m",
        "喷头：距火源轴线 2.0 m，RTI=50 (m·s)½，动作温度 68 ℃",
        "环境温度：20 ℃；时间步长 0.5 s；总时长 180 s",
    ],
    models=["t² 火灾增长", "Heskestad 火焰高度关联式", "Alpert 顶棚射流", "喷头 RTI 热响应"],
    outputs=["热释放速率和火焰高度", "顶棚温度、射流速度和喷头温度", "燃料消耗、耗氧量和喷头动作时间"],
    work_dir="~/fire_case",
    artifacts=[
        "~/fire_case/fire_results.csv",
        "~/fire_case/fire_growth.py",
        "~/fire_case/slurm-{job_id}.out",
    ],
    stages=[
        TemplateStage(
            label="写入火灾模型",
            action="write_file",
            params={"path": "~/fire_case/fire_growth.py", "content": FIRE_GROWTH_SCRIPT},
        ),
        TemplateStage(
            label="写入 sbatch 脚本",
            action="write_file",
            params={"path": "~/fire_case/submit.sh", "content": FIRE_GROWTH_SBATCH},
        ),
        TemplateStage(
            label="提交仿真作业",
            action="run_command",
            params={"command": "cd ~/fire_case && sbatch submit.sh"},
            output_var="submit_output",
        ),
        TemplateStage(
            label="等待仿真完成",
            action="poll_jobs",
            params={
                "interval": 1,
                "timeout": 120,
                "extract_job_id_from": "submit_output",
                "status_file": "~/fire_case/job-{job_id}.status",
            },
            output_var="job_status",
        ),
        TemplateStage(
            label="读取火灾结果",
            action="run_command",
            params={
                "command": (
                    "cat ~/fire_case/slurm-{job_id}.out 2>/dev/null; "
                    "echo '--- CSV last 5 rows ---'; tail -5 ~/fire_case/fire_results.csv 2>/dev/null"
                ),
                "success_markers": ["Model status       : SUCCESS", "CSV output"],
            },
            output_var="job_output",
        ),
        TemplateStage(
            label="AI 解读火灾过程",
            action="llm_summarize",
            params={
                "prompt": (
                    "用户刚在 HPC 集群上运行了一个经典 t² 火灾增长、Alpert 顶棚射流和喷淋热响应算例。"
                    "以下是模型输出：\n\n{job_output}\n\n"
                    "请以火灾安全专业学生能理解的方式解读：1) 热释放速率如何随时间增长 "
                    "2) 火焰高度和顶棚温度的变化 3) 喷头是否动作、动作时间意味着什么 "
                    "4) 燃料消耗与耗氧量 5) 说明这是工程关联式模型及其局限，不能冒充 CFD 或设计结论。"
                    "只有日志明确出现 Model status: SUCCESS（允许冒号两侧有空格）和 CSV 输出时，"
                    "才能判断仿真成功；若作业报错，不要虚构物理结果，应从 Python、Slurm 或文件路径角度给出修复建议。"
                ),
            },
        ),
    ],
))


def get_template(template_id: str) -> Optional[JobTemplate]:
    return TEMPLATES.get(template_id)


def list_templates() -> list[dict[str, Any]]:
    """返回前端需要的模板元信息列表（不含 stages 细节）。"""
    return [
        {
            "id": t.id,
            "icon": t.icon,
            "title": t.title,
            "desc": t.desc,
            "tag": t.tag,
            "overview": t.overview,
            "conditions": t.conditions,
            "models": t.models,
            "outputs": t.outputs,
            "work_dir": t.work_dir,
            "artifacts": t.artifacts,
            "stage_count": len(t.stages),
            "stage_labels": [s.label for s in t.stages],
        }
        for t in TEMPLATES.values()
    ]
