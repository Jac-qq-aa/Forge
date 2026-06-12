# Forge 临时端口 systemd 源码部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不停止现有 Docker 生产服务的前提下，把 Forge Web、Gateway、LangGraph 以宿主机源码 + systemd 方式跑在临时端口，完成验证后再决定是否切正式端口。

**Architecture:** 生产源码目录使用远程 `/mnt/Forge`。创建 `/mnt/Forge/.venv` 安装 Python 依赖；新增/同步 `run_web.py`、`run_gateway.py`，通过 systemd 环境变量指定临时端口和服务间地址；新增三个临时 systemd unit，分别托管 LangGraph、Gateway、Web。

**Tech Stack:** Ubuntu 24.04、Python 3.12、venv、FastAPI/Uvicorn、LangGraph CLI、systemd、现有 Docker Redis/Milvus 作为临时依赖。

---

### Task 1: 准备源码运行入口

**Files:**
- Modify: `/home/hugo/Forge/run_web.py`
- Modify: `/home/hugo/Forge/run_gateway.py`
- Copy to remote: `/mnt/Forge/run_web.py`
- Copy to remote: `/mnt/Forge/run_gateway.py`

- [ ] **Step 1: Web 入口读取显式端口**

把 `run_web.py` 的 uvicorn 端口改为：

```python
web_port = int(os.environ["WEB_PORT"])
print(f'[Web] Starting Web UI on port {web_port}...')
uvicorn.run(
    "forge.web.app:app",
    host="0.0.0.0",
    port=web_port,
    reload=False,
)
```

- [ ] **Step 2: Gateway 入口读取显式端口**

把 `run_gateway.py` 的 uvicorn 端口改为：

```python
gateway_port = int(os.environ["GATEWAY_PORT"])
print(f'[Gateway] Starting Gateway API on port {gateway_port}...')
print('[Gateway] LangGraph Server should be reachable via LANGGRAPH_API_URL')
print('[Gateway] Start LangGraph Server before Gateway')
uvicorn.run(
    "forge.gateway.app:app",
    host="0.0.0.0",
    port=gateway_port,
    reload=False,
)
```

- [ ] **Step 3: 同步入口脚本到远程**

使用 SFTP 上传：

```text
/home/hugo/Forge/run_web.py -> /mnt/Forge/run_web.py
/home/hugo/Forge/run_gateway.py -> /mnt/Forge/run_gateway.py
```

- [ ] **Step 4: 远程编译检查**

Run:

```bash
cd /mnt/Forge
python3 -m py_compile run_web.py run_gateway.py
```

Expected: exit code 0。

### Task 2: 创建 Python venv 并安装依赖

**Files:**
- Create directory: `/mnt/Forge/.venv`

- [ ] **Step 1: 创建 venv**

Run:

```bash
cd /mnt/Forge
python3 -m venv .venv
```

Expected: `/mnt/Forge/.venv/bin/python` 存在。

- [ ] **Step 2: 安装 Python 依赖**

Run:

```bash
cd /mnt/Forge
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Expected: pip install exit code 0。

- [ ] **Step 3: 安装 Playwright Chromium**

Run:

```bash
cd /mnt/Forge
.venv/bin/python -m playwright install chromium
```

Expected: exit code 0。

### Task 3: 创建临时 systemd 服务

**Files:**
- Create: `/etc/systemd/system/forge-langgraph-source.service`
- Create: `/etc/systemd/system/forge-gateway-source.service`
- Create: `/etc/systemd/system/forge-web-source.service`

- [ ] **Step 1: 创建 LangGraph 临时服务**

Unit 内容：

```ini
[Unit]
Description=Forge LangGraph Source Temporary Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/mnt/Forge
EnvironmentFile=/mnt/Forge/.env
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
Environment=MILVUS_HOST=127.0.0.1
Environment=MILVUS_PORT=19530
ExecStart=/mnt/Forge/.venv/bin/langgraph dev --host 0.0.0.0 --port 12024
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: 创建 Gateway 临时服务**

Unit 内容：

```ini
[Unit]
Description=Forge Gateway Source Temporary Service
After=network.target forge-langgraph-source.service
Wants=forge-langgraph-source.service

[Service]
Type=simple
WorkingDirectory=/mnt/Forge
EnvironmentFile=/mnt/Forge/.env
Environment=GATEWAY_PORT=18001
Environment=LANGGRAPH_SERVER_URL=http://127.0.0.1:12024
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
Environment=MILVUS_HOST=127.0.0.1
Environment=MILVUS_PORT=19530
ExecStart=/mnt/Forge/.venv/bin/python /mnt/Forge/run_gateway.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: 创建 Web 临时服务**

Unit 内容：

```ini
[Unit]
Description=Forge Web Source Temporary Service
After=network.target forge-gateway-source.service
Wants=forge-gateway-source.service

[Service]
Type=simple
WorkingDirectory=/mnt/Forge
EnvironmentFile=/mnt/Forge/.env
Environment=WEB_PORT=18000
Environment=GATEWAY_API_URL=http://127.0.0.1:18001
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
Environment=MILVUS_HOST=127.0.0.1
Environment=MILVUS_PORT=19530
ExecStart=/mnt/Forge/.venv/bin/python /mnt/Forge/run_web.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: 重载 systemd**

Run:

```bash
systemctl daemon-reload
```

Expected: exit code 0。

### Task 4: 启动并验证临时服务

**Files:**
- Runtime only

- [ ] **Step 1: 启动服务**

Run:

```bash
systemctl start forge-langgraph-source
systemctl start forge-gateway-source
systemctl start forge-web-source
```

Expected: 三个服务处于 active/running 或启动中。

- [ ] **Step 2: 查看状态**

Run:

```bash
systemctl --no-pager --full status forge-langgraph-source forge-gateway-source forge-web-source
```

Expected: 没有持续重启失败。

- [ ] **Step 3: 健康检查**

Run:

```bash
curl -sS -o /tmp/source-web.out -w '%{http_code}' http://127.0.0.1:18000/
curl -sS -o /tmp/source-gateway.out -w '%{http_code}' http://127.0.0.1:18001/health
curl -sS -o /tmp/source-langgraph.out -w '%{http_code}' http://127.0.0.1:12024/ok
```

Expected: Web/Gateway/LangGraph 返回 200。

- [ ] **Step 4: 不切正式端口**

保留现有 Docker 生产服务运行，等待用户确认下一阶段。
