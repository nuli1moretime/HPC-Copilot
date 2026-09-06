#!/bin/bash
# ═══ 一键创建全部 10 个测试脚本 ═══
# 用法：在平台终端中执行
#   bash setup_benchmark_scripts.sh

mkdir -p ~/benchmark_test && cd ~/benchmark_test

# --- 01_timeout.sh ---
cat > 01_timeout.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_timeout
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:00:10
#SBATCH --nodes=1

echo "Starting timeout test at $(date)"
echo "This job will sleep for 60s but only has 10s limit"
sleep 60
echo "This line should never print"
EOF

# --- 02_invalid_qos.sh ---
cat > 02_invalid_qos.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=invalid_qos_name
#SBATCH --job-name=bench_bad_qos
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "This should never run"
EOF

# --- 03_invalid_partition.sh ---
cat > 03_invalid_partition.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=NONEXISTENT_PARTITION
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_bad_partition
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "This should never run"
EOF

# --- 04_wrong_path.sh ---
cat > 04_wrong_path.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_wrong_path
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "Attempting to run a script that does not exist..."
python nonexistent_script.py
EOF

# --- 05_bad_module.sh ---
cat > 05_bad_module.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_no_module
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

module load cuda/99.99
echo "This should not print if module load fails the script"
EOF

# --- 06_import_error.sh ---
cat > 06_import_error.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_import_err
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "Testing Python import error..."
python -c "import nonexistent_package_xyz"
EOF

# --- 07_permission_denied.sh ---
cat > 07_permission_denied.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_permission
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "Testing permission denied..."
echo "test" > /root/should_not_write_here.txt
EOF

# --- 08_normal_success.sh ---
cat > 08_normal_success.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_success
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "This is a normal job running successfully"
echo "Current time: $(date)"
echo "Hostname: $(hostname)"
python -c "print('Hello from Python:', 2+2)"
echo "Job completed normally."
EOF

# --- 09_runtime_error.sh ---
cat > 09_runtime_error.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_exit_code
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "Testing runtime exception..."
python -c "
def divide(a, b):
    return a / b
result = divide(10, 0)
print(result)
"
EOF

# --- 10_conda_not_found.sh ---
cat > 10_conda_not_found.sh << 'EOF'
#!/bin/bash
#SBATCH --partition=P107-A100
#SBATCH --qos=qos_p107-a100
#SBATCH --job-name=bench_conda
#SBATCH --output=slurm-%j.out
#SBATCH --time=00:01:00

echo "Testing conda without initialization..."
conda activate myenv
python --version
EOF

chmod +x *.sh
echo ""
echo "Done! Created 10 scripts in ~/benchmark_test/"
ls -la *.sh
