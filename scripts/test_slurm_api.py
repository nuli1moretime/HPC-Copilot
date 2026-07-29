"""测试 Slurm REST API (slurmrestd) 连通性。

用法：
    python scripts/test_slurm_api.py --host <集群地址> --port 6820 --user <用户名>

slurmrestd 默认端口是 6820，但也可能是其他端口，问管理员确认。
"""

import argparse
import sys

import httpx


def test_slurm_api(base_url: str, user: str, token: str = ""):
    """探测 slurmrestd 各端点。"""

    headers = {}
    # slurmrestd 认证方式：JWT token 或 basic auth（取决于配置）
    if token:
        headers["X-SLURM-USER-TOKEN"] = token
    else:
        headers["X-SLURM-USER-NAME"] = user

    client = httpx.Client(timeout=10.0, headers=headers)

    endpoints = [
        ("Slurm 版本", "/slurm/v0.0.40/openapi"),
        ("作业列表", "/slurm/v0.0.40/jobs"),
        ("分区信息", "/slurm/v0.0.40/partitions"),
        ("QoS 列表", "/slurm/v0.0.40/qos"),
        ("节点信息", "/slurm/v0.0.40/nodes"),
    ]

    print(f"\n🔍 测试目标: {base_url}")
    print(f"   认证用户: {user}")
    print("=" * 50)

    success_count = 0
    for name, path in endpoints:
        url = f"{base_url}{path}"
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  ✅ {name}: {resp.status_code}")
                # 打印一点摘要信息
                if "jobs" in data:
                    print(f"     → 当前作业数: {len(data['jobs'])}")
                elif "partitions" in data:
                    names = [p["name"] for p in data["partitions"][:5]]
                    print(f"     → 分区: {names}")
                elif "qos" in data:
                    names = [q["name"] for q in data["qos"][:5]]
                    print(f"     → QoS: {names}")
                success_count += 1
            else:
                print(f"  ❌ {name}: HTTP {resp.status_code}")
                print(f"     → {resp.text[:200]}")
        except httpx.ConnectError:
            print(f"  ❌ {name}: 连接失败（服务未开启或端口不对）")
            break
        except httpx.TimeoutException:
            print(f"  ❌ {name}: 超时")
            break
        except Exception as e:
            print(f"  ❌ {name}: {type(e).__name__}: {e}")

    print("=" * 50)
    if success_count > 0:
        print(f"🎉 slurmrestd 可用！成功访问 {success_count}/{len(endpoints)} 个端点")
        print("   可以走 REST API 路线。")
    else:
        print("😅 slurmrestd 不可用，建议改走 SSH (paramiko) 路线。")
        print("   或者联系管理员确认：")
        print("   1. slurmrestd 服务是否已启动")
        print("   2. 端口号是多少（默认 6820）")
        print("   3. 认证方式（JWT / basic auth）")

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="测试 Slurm REST API 连通性")
    parser.add_argument("--host", required=True, help="集群地址，如 hpc.xxx.edu.cn")
    parser.add_argument("--port", type=int, default=6820, help="slurmrestd 端口（默认 6820）")
    parser.add_argument("--user", required=True, help="你的集群用户名")
    parser.add_argument("--token", default="", help="JWT token（如果认证方式是 JWT）")
    parser.add_argument("--https", action="store_true", help="是否使用 HTTPS")
    args = parser.parse_args()

    scheme = "https" if args.https else "http"
    base_url = f"{scheme}://{args.host}:{args.port}"

    test_slurm_api(base_url, args.user, args.token)
