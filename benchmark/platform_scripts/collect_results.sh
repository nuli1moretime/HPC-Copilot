#!/bin/bash
# ═══ 收集全部测试结果 ═══
# 用法：等所有作业跑完后，执行
#   cd ~/benchmark_test && bash collect_results.sh

echo "=========================================="
echo "  Benchmark Results Collection"
echo "  $(date)"
echo "=========================================="
echo ""

mkdir -p results

# 收集所有 slurm-*.out 文件
echo "--- All slurm output files ---"
for f in slurm-*.out; do
    if [ -f "$f" ]; then
        echo ""
        echo ">>> FILE: $f"
        echo "--- content ---"
        cat "$f"
        echo "--- end ---"
        cp "$f" results/
    fi
done

echo ""
echo "=========================================="
echo "  Job history (squeue)"
echo "=========================================="
squeue -u $USER 2>&1 || echo "(no jobs in queue)"

echo ""
echo "=========================================="
echo "  Collection complete!"
echo "  Results saved to ~/benchmark_test/results/"
echo "=========================================="
echo ""
echo "Next: copy ALL the output above and send it back."
echo "You can use: cat results/*.out"
