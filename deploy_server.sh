#!/bin/bash
# Forge 安全部署脚本 - 不影响其他服务
# 在服务器上执行此脚本

set -e

PROJECT_DIR="/mnt/Forge"
LOG_FILE="/mnt/forge_deploy.log"

echo "Forge 安全部署脚本 - $(date)" | tee -a $LOG_FILE
echo "========================================" | tee -a $LOG_FILE

# 1. 检查端口是否被占用
echo ""
echo "[检查] 端口 13324 是否可用..." | tee -a $LOG_FILE
if netstat -tlnp | grep -q ":13324"; then
    echo "❌ 端口 13324 已被占用！" | tee -a $LOG_FILE
    netstat -tlnp | grep ":13324" | tee -a $LOG_FILE
    echo "请先处理端口冲突，或修改 docker-compose.prod.yml 中的端口" | tee -a $LOG_FILE
    exit 1
else
    echo "✅ 端口 13324 可用" | tee -a $LOG_FILE
fi

# 2. 检查现有 Docker 服务
echo ""
echo "[检查] 现有 Docker 服务（不修改）..." | tee -a $LOG_FILE
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}" | tee -a $LOG_FILE

# 3. 进入项目目录
cd $PROJECT_DIR

# 4. 检查环境变量文件
echo ""
echo "[检查] 环境变量文件..." | tee -a $LOG_FILE
if [ ! -f ".env" ]; then
    echo "❌ .env 文件不存在！请先创建：" | tee -a $LOG_FILE
    echo "   cp .env.prod .env" | tee -a $LOG_FILE
    echo "   nano .env  # 填写 QWEN_API_KEY 等必填项" | tee -a $LOG_FILE
    exit 1
fi

# 检查 QWEN_API_KEY 是否配置
if grep -q "your-qwen-api-key" .env; then
    echo "❌ QWEN_API_KEY 未配置！请编辑 .env 文件填写实际 API Key" | tee -a $LOG_FILE
    exit 1
fi
echo "✅ .env 文件已配置" | tee -a $LOG_FILE

# 5. 只启动 Forge 相关服务（不启动新的 Milvus，使用现有的）
echo ""
echo "[启动] Forge 服务..." | tee -a $LOG_FILE

# 检查是否已有 Milvus 运行
if docker ps | grep -q "milvus"; then
    echo "✅ Milvus 已在运行，复用现有服务" | tee -a $LOG_FILE
    # 只启动 Redis、LangGraph、Gateway
    docker compose -f docker-compose.prod.yml up -d redis langgraph gateway --no-deps
else
    echo "⚠️  Milvus 未运行，需要启动完整服务栈" | tee -a $LOG_FILE
    # 先启动 Milvus（使用原 docker-compose.yml）
    docker compose -f docker-compose.yml up -d
    sleep 30
    # 再启动 Forge 服务
    docker compose -f docker-compose.prod.yml up -d
fi

# 6. 等待服务启动
echo ""
echo "[等待] 服务启动中..." | tee -a $LOG_FILE
sleep 20

# 7. 检查服务状态
echo ""
echo "[状态] Forge 服务运行情况：" | tee -a $LOG_FILE
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}" | grep -E "forge|NAME" | tee -a $LOG_FILE

# 8. 健康检查
echo ""
echo "[验证] Gateway API 健康检查..." | tee -a $LOG_FILE
sleep 10
curl -s http://localhost:13324/health | tee -a $LOG_FILE || echo "Gateway 未响应，请查看日志" | tee -a $LOG_FILE

echo ""
echo "========================================" | tee -a $LOG_FILE
echo "部署完成！" | tee -a $LOG_FILE
echo "API 地址: http://47.107.254.82:13324" | tee -a $LOG_FILE
echo "查看日志: docker logs forge-gateway -f" | tee -a $LOG_FILE
echo "========================================" | tee -a $LOG_FILE