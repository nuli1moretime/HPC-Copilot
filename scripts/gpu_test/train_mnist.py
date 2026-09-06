"""GPU 环境验证脚本 — PyTorch MNIST 1 epoch。

目标：确认 CUDA + PyTorch + GPU 可正常调用。
如果集群无法下载 MNIST（无外网），自动降级为随机张量训练，
同样能验证 GPU 计算能力。
"""

import time
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

def main():
    print("=" * 50)
    print("  GPU 环境验证 — PyTorch MNIST 1 Epoch")
    print("=" * 50)

    # 1. 基础环境信息
    print(f"\n[环境] PyTorch 版本: {torch.__version__}")
    print(f"[环境] CUDA 可用: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"[环境] CUDA 版本: {torch.version.cuda}")
        print(f"[环境] GPU 数量: {torch.cuda.device_count()}")
        for i in range(torch.cuda.device_count()):
            print(f"[环境] GPU {i}: {torch.cuda.get_device_name(i)}")
            mem = torch.cuda.get_device_properties(i).total_mem / 1024**3
            print(f"[环境] GPU {i} 显存: {mem:.1f} GB")
    else:
        print("[错误] CUDA 不可用！请检查 module load cuda 和驱动。")
        return

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n[配置] 使用设备: {device}")

    # 2. 加载 MNIST（无外网时降级为随机数据）
    try:
        from torchvision import datasets, transforms
        transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,))
        ])
        train_dataset = datasets.MNIST(
            "./data", train=True, download=True, transform=transform
        )
        print(f"[数据] MNIST 下载/加载成功，样本数: {len(train_dataset)}")
    except Exception as e:
        print(f"[数据] MNIST 下载失败 ({e})，使用随机张量代替（仍验证 GPU 计算）")
        # 生成 10000 个 28x28 随机样本
        X = torch.randn(10000, 1, 28, 28)
        y = torch.randint(0, 10, (10000,))
        train_dataset = TensorDataset(X, y)

    train_loader = DataLoader(train_dataset, batch_size=128, shuffle=True, num_workers=0)

    # 3. 简单 CNN 模型
    model = nn.Sequential(
        nn.Conv2d(1, 32, 3, padding=1),
        nn.ReLU(),
        nn.MaxPool2d(2),
        nn.Conv2d(32, 64, 3, padding=1),
        nn.ReLU(),
        nn.MaxPool2d(2),
        nn.Flatten(),
        nn.Linear(64 * 7 * 7, 128),
        nn.ReLU(),
        nn.Linear(128, 10),
    ).to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)

    print(f"[模型] 参数量: {sum(p.numel() for p in model.parameters()):,}")
    print(f"[训练] 开始 1 epoch, batch_size=128, 共 {len(train_loader)} batches\n")

    # 4. 训练 1 epoch
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0
    t_start = time.time()

    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target)
        loss.backward()
        optimizer.step()

        total_loss += loss.item()
        pred = output.argmax(dim=1)
        correct += pred.eq(target).sum().item()
        total += target.size(0)

        if (batch_idx + 1) % 100 == 0:
            elapsed = time.time() - t_start
            print(f"  batch {batch_idx+1}/{len(train_loader)}, "
                  f"loss={total_loss/(batch_idx+1):.4f}, "
                  f"acc={100.*correct/total:.1f}%, "
                  f"耗时={elapsed:.1f}s")

    t_end = time.time()

    # 5. 结果汇总
    print("\n" + "=" * 50)
    print("  验证结果")
    print("=" * 50)
    print(f"  训练耗时:   {t_end - t_start:.2f}s")
    print(f"  平均 loss:  {total_loss / len(train_loader):.4f}")
    print(f"  训练精度:   {100. * correct / total:.2f}%")
    print(f"  吞吐量:     {total / (t_end - t_start):.0f} samples/s")
    print(f"  GPU 利用:   ✅ CUDA 正常调用")
    print("=" * 50)
    print("\n🎉 GPU 环境验证通过！PyTorch + CUDA 工作正常。")


if __name__ == "__main__":
    main()
