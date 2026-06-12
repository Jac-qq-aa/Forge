#!/usr/bin/env python3
"""Forge 安全部署脚本 - 使用 SSH 远程部署"""

import paramiko
import os
import time
import sys

# 服务器配置
SERVER_HOST = "47.107.254.82"
SERVER_USER = "root"
SERVER_PASSWORD = "Ngu#_fsjv2_chat01_1394*&"
PROJECT_DIR = "/mnt/Forge"
LOCAL_PROJECT_DIR = "/home/hugo/Forge"

def ssh_connect():
    """建立 SSH 连接"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(SERVER_HOST, username=SERVER_USER, password=SERVER_PASSWORD, timeout=30)
        print(f"✅ SSH 连接成功: {SERVER_HOST}")
        return client
    except Exception as e:
        print(f"❌ SSH 连接失败: {e}")
        sys.exit(1)

def run_command(client, cmd, check=True):
    """执行远程命令"""
    print(f"\n[执行] {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
    exit_code = stdout.channel.recv_exit_status()
    output = stdout.read().decode('utf-8')
    error = stderr.read().decode('utf-8')

    if output:
        print(output)
    if error and exit_code != 0:
        print(f"错误: {error}")

    if check and exit_code != 0:
        raise Exception(f"命令执行失败: {cmd}")

    return output, exit_code

def check_existing_services(client):
    """检查现有 Docker 服务"""
    print("\n" + "="*50)
    print("[检查] 现有 Docker 服务")
    print("="*50)
    output, _ = run_command(client, "docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'", check=False)
    return output

def check_port(client, port):
    """检查端口是否被占用"""
    print(f"\n[检查] 端口 {port} 是否可用...")
    output, code = run_command(client, f"netstat -tlnp | grep :{port}", check=False)
    if code == 0 and output.strip():
        print(f"❌ 端口 {port} 已被占用:")
        print(output)
        return False
    else:
        print(f"✅ 端口 {port} 可用")
        return True

def upload_project(client):
    """上传项目文件"""
    print("\n" + "="*50)
    print("[上传] 项目文件到服务器")
    print("="*50)

    # 创建 SFTP 连接
    sftp = client.open_sftp()

    # 创建目标目录
    try:
        sftp.stat(PROJECT_DIR)
        print(f"目录 {PROJECT_DIR} 已存在")
    except FileNotFoundError:
        run_command(client, f"mkdir -p {PROJECT_DIR}")
        print(f"创建目录 {PROJECT_DIR}")

    # 需要上传的关键文件列表
    upload_files = [
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "Dockerfile.gateway",
        "langgraph.json",
        "requirements.txt",
        ".env.prod",
        "deploy_server.sh",
    ]

    # 上传文件
    for file in upload_files:
        local_path = os.path.join(LOCAL_PROJECT_DIR, file)
        remote_path = os.path.join(PROJECT_DIR, file)
        if os.path.exists(local_path):
            print(f"  上传: {file}")
            sftp.put(local_path, remote_path)

    # 上传 forge 目录（递归）
    print("\n上传 forge 目录...")
    run_command(client, f"mkdir -p {PROJECT_DIR}/forge")

    # 递归上传 forge 目录
    forge_local = os.path.join(LOCAL_PROJECT_DIR, "forge")
    forge_remote = PROJECT_DIR + "/forge"

    for root, dirs, files in os.walk(forge_local):
        # 转换本地路径到远程路径
        rel_path = os.path.relpath(root, forge_local)
        remote_root = forge_remote + "/" + rel_path.replace(os.sep, '/')

        # 创建目录
        for d in dirs:
            remote_dir = remote_root + "/" + d
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                run_command(client, f"mkdir -p '{remote_dir}'")

        # 上传文件（排除 __pycache__）
        for f in files:
            if "__pycache__" not in root and ".pyc" not in f:
                local_file = os.path.join(root, f)
                remote_file = remote_root + "/" + f
                print(f"  {rel_path}/{f}")
                try:
                    sftp.put(local_file, remote_file)
                except Exception as e:
                    print(f"    跳过: {e}")

    sftp.close()
    print("✅ 文件上传完成")

def setup_env(client):
    """配置环境变量"""
    print("\n" + "="*50)
    print("[配置] 环境变量")
    print("="*50)

    # 检查 .env 是否存在
    output, code = run_command(client, f"test -f {PROJECT_DIR}/.env && echo 'exists'", check=False)

    if "exists" in output:
        print(".env 已存在，跳过配置")
        return True

    # 复制模板
    run_command(client, f"cp {PROJECT_DIR}/.env.prod {PROJECT_DIR}/.env")
    print("已创建 .env 文件，需要手动配置 QWEN_API_KEY")
    print("请在服务器上执行: nano /mnt/Forge/.env")
    return False

def start_services(client):
    """启动服务"""
    print("\n" + "="*50)
    print("[启动] Forge 服务")
    print("="*50)

    # 检查是否已有 Milvus 运行
    output, _ = run_command(client, "docker ps | grep milvus", check=False)

    if output.strip():
        print("✅ Milvus 已在运行，复用现有服务")
        # 只启动 Forge 服务
        cmd = f"cd {PROJECT_DIR} && docker compose -f docker-compose.prod.yml up -d redis langgraph gateway --no-deps"
        run_command(client, cmd)
    else:
        print("⚠️ Milvus 未运行，启动完整服务栈")
        # 先启动 Milvus
        cmd1 = f"cd {PROJECT_DIR} && docker compose -f docker-compose.yml up -d"
        run_command(client, cmd1)
        print("等待 Milvus 启动...")
        time.sleep(45)
        # 再启动 Forge 服务
        cmd2 = f"cd {PROJECT_DIR} && docker compose -f docker-compose.prod.yml up -d"
        run_command(client, cmd2)

    print("等待服务启动...")
    time.sleep(30)

def verify_deployment(client):
    """验证部署"""
    print("\n" + "="*50)
    print("[验证] 部署状态")
    print("="*50)

    # 检查容器状态
    print("\nForge 容器状态:")
    run_command(client, "docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}' | grep -E 'forge|NAME'", check=False)

    # 健康检查
    print("\nGateway 健康检查:")
    output, code = run_command(client, "curl -s http://localhost:13324/health", check=False)
    if code == 0 and output:
        print("✅ Gateway 运行正常")
    else:
        print("❌ Gateway 未响应，请检查日志: docker logs forge-gateway")

def main():
    print("="*60)
    print("Forge 远程部署脚本")
    print(f"服务器: {SERVER_HOST}")
    print(f"目标目录: {PROJECT_DIR}")
    print("="*60)

    # 连接服务器
    client = ssh_connect()

    try:
        # 1. 检查现有服务
        check_existing_services(client)

        # 2. 检查端口
        if not check_port(client, 13324):
            print("\n❌ 端口 13324 被占用，部署终止")
            print("请先处理端口冲突")
            return

        # 3. 上传项目
        upload_project(client)

        # 4. 配置环境变量
        env_ready = setup_env(client)
        if not env_ready:
            print("\n⚠️ 需要手动配置 .env 文件后才能启动服务")
            print("请执行以下步骤:")
            print("  1. ssh root@47.107.254.82")
            print("  2. nano /mnt/Forge/.env")
            print("  3. 填写 QWEN_API_KEY=your-actual-key")
            print("  4. 重新运行此脚本")
            return

        # 5. 启动服务
        start_services(client)

        # 6. 验证部署
        verify_deployment(client)

        print("\n" + "="*60)
        print("🎉 部署完成!")
        print(f"API 地址: http://{SERVER_HOST}:13324")
        print("查看日志: docker logs forge-gateway -f")
        print("="*60)

    finally:
        client.close()

if __name__ == "__main__":
    main()