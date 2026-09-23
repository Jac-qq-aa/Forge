#!/bin/bash
# Forge 生产环境部署脚本
# 使用方式: ./deploy.sh [命令]

set -e

DOCKER_VOLUME_DIRECTORY=${DOCKER_VOLUME_DIRECTORY:-.}

echo "Forge 部署脚本"
echo "================"

case "$1" in
  start)
    echo "启动所有服务..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    echo "等待服务启动完成..."
    sleep 30
    echo "检查服务状态..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
    ;;

  stop)
    echo "停止所有服务..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml down
    ;;

  restart)
    echo "重启所有服务..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml down
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    ;;

  logs)
    echo "查看日志..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f --tail=100
    ;;

  status)
    echo "服务状态..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
    echo ""
    echo "健康检查:"
    curl -s http://localhost:13324/health | jq . || echo "Gateway 未响应"
    ;;

  build)
    echo "构建镜像..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml build
    ;;

  update)
    echo "更新部署（拉取最新代码并重启）..."
    git pull
    docker compose -f docker-compose.yml -f docker-compose.prod.yml build
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    ;;

  *)
    echo "使用方式: ./deploy.sh [命令]"
    echo ""
    echo "可用命令:"
    echo "  start    - 启动所有服务"
    echo "  stop     - 停止所有服务"
    echo "  restart  - 重启所有服务"
    echo "  logs     - 查看日志"
    echo "  status   - 检查服务状态"
    echo "  build    - 构建镜像"
    echo "  update   - 拉取最新代码并重启"
    ;;
esac