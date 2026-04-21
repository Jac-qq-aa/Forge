# Forge 部署指南

## 系统要求

### 最低配置

- **操作系统**：Linux / macOS / Windows (WSL2)
- **Python**：3.10+
- **内存**：4GB+
- **磁盘**：10GB+

### 推荐配置

- **内存**：8GB+
- **磁盘**：20GB+（存储浏览器缓存和知识库数据）

---

## 环境准备

### 1. 克隆项目

```bash
git clone <repository-url>
cd Forge
```

### 2. 创建 Python 环境

使用 conda 创建独立环境：

```bash
# 创建环境
conda create -n forge python=3.10

# 激活环境
conda activate forge
```

或使用 venv：

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

主要依赖：
- `fastapi` - Web 框架
- `uvicorn` - ASGI 服务器
- `langgraph` - 工作流引擎
- `playwright` - 浏览器自动化
- `playwright-stealth` - 反检测
- `pymilvus` - Milvus 客户端
- `sentence-transformers` - 向量模型
- `python-dotenv` - 环境变量管理

### 4. 安装 Playwright 浏览器

```bash
playwright install chromium
```

---

## 配置

### 1. 创建环境变量文件

创建 `.env` 文件：

```bash
cp .env.example .env
```

### 2. 配置 API Key

编辑 `.env` 文件，填写必要配置：

```ini
# Qwen LLM API配置（必填）
QWEN_API_KEY=your-api-key-here
QWEN_MODEL=qwen3.5-plus

# Milvus配置（可选，有默认值）
MILVUS_HOST=localhost
MILVUS_PORT=19530
```

### 3. 获取 Qwen API Key

1. 访问 [阿里云百炼平台](https://bailian.console.aliyun.com/)
2. 创建 API Key
3. 将 Key 填入 `.env` 文件

---

## Milvus 向量数据库

### Docker 部署

使用 Docker Compose 启动 Milvus：

```bash
# 启动服务
docker-compose up -d

# 查看状态
docker-compose ps
```

### docker-compose.yml

```yaml
version: '3'
services:
  milvus:
    image: milvusdb/milvus:latest
    container_name: milvus-standalone
    ports:
      - "19530:19530"
      - "9091:9091"
    volumes:
      - ./volumes/milvus:/var/lib/milvus
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
```

### Milvus GUI (Attu)

启动可视化界面：

```bash
docker run -d --name attu \
  -p 3000:3000 \
  -e MILVUS_URL=host.docker.internal:19530 \
  zilliz/attu:latest
```

访问 `http://localhost:3000` 管理知识库数据。

---

## 知识库初始化

### 1. 导入文档

将锐博集团相关文档放入项目目录，运行导入脚本：

```bash
python import_docs.py
```

支持的文档格式：
- `.docx` (Word文档)
- `.txt` (纯文本)

### 2. 验证知识库

查询知识库确认导入成功：

```bash
python query_knowledge.py
```

输入关键词搜索，检查返回的相关内容。

---

## 平台登录

### 知乎登录

首次使用知乎功能需扫码登录：

```bash
python login_zhihu.py
```

操作步骤：
1. 运行脚本，浏览器弹出知乎页面
2. 使用知乎 App 扫码登录
3. 登录成功后按 `Ctrl+C` 退出
4. 登录状态自动保存

**缓存位置**：`~/.forge/browser_data/zhihu`

### 微信公众号

微信公众号通过搜狗搜索访问，无需单独登录。

---

## 启动服务

### Web 服务

```bash
python run_web.py
```

访问 `http://localhost:8000`

### 后台运行

使用 nohup 后台运行：

```bash
nohup python run_web.py > forge.log 2>&1 &
```

查看日志：

```bash
tail -f forge.log
```

### 生产部署

使用 gunicorn + uvicorn：

```bash
gunicorn forge.web.app:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

---

## 目录结构

项目运行后生成的目录：

```
~/.forge/
├── browser_data/
│   ├── zhihu/          # 知乎浏览器缓存
│   └── wechat/         # 微信浏览器缓存
└── output/
    ├── scripts/        # 改写文案输出
    └── videos/         # 视频输出（如有）
```

---

## 常见问题

### 1. 知乎登录失败

**症状**：提示"登录页面"或无法获取内容

**解决**：
```bash
# 重新登录
python login_zhihu.py
```

### 2. Milvus 连接失败

**症状**：知识库搜索报错

**解决**：
```bash
# 检查 Milvus 是否运行
docker ps | grep milvus

# 重启 Milvus
docker-compose restart
```

### 3. Playwright 浏览器缺失

**症状**：`playwright._impl._errors.Error: Executable doesn't exist`

**解决**：
```bash
playwright install chromium
```

### 4. Qwen API 调用失败

**症状**：改写时报 API 错误

**解决**：
- 检查 `.env` 中 `QWEN_API_KEY` 是否正确
- 检查 API Key 是否有效（在阿里云百炼平台验证）
- 检查网络连接

### 5. 反爬虫触发

**症状**：知乎返回验证码页面

**解决**：
- 等待一段时间后重试
- 使用持久化上下文保持登录状态
- 减少请求频率

### 6. 内存不足

**症状**：程序崩溃或变慢

**解决**：
- 关闭其他应用释放内存
- 增加 Docker 内存限制
- 使用更轻量的向量模型

---

## 性能优化

### 1. 并发处理

Web 服务支持多 worker：

```bash
uvicorn forge.web.app:app --workers 4
```

### 2. 知识库索引

创建向量索引加速搜索：

```python
collection.create_index(
    field_name="vector",
    index_params={"metric_type": "COSINE", "index_type": "IVF_FLAT"}
)
```

### 3. 浏览器缓存

持久化上下文避免重复登录，减少请求耗时。

---

## 日志配置

### 日志格式

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
```

### 日志级别

- `DEBUG`: 详细调试信息
- `INFO`: 正常运行信息
- `WARNING`: 警告信息
- `ERROR`: 错误信息

调整日志级别：

```python
logging.getLogger("ForgeWeb").setLevel(logging.DEBUG)
```

---

## 安全建议

### 1. API Key 保护

- 不要将 `.env` 文件提交到版本控制
- 使用环境变量或密钥管理服务

### 2. 网络隔离

生产环境建议：
- 使用防火墙限制访问
- 只开放必要端口（8000）

### 3. 数据备份

定期备份：
- 知识库数据（Milvus volumes）
- 浏览器缓存目录
- 输出文件目录

---

## 更新升级

### 更新代码

```bash
git pull origin main
pip install -r requirements.txt  # 更新依赖
```

### 更新 Milvus

```bash
docker-compose down
docker-compose pull
docker-compose up -d
```

### 清理缓存

如需重新登录知乎：

```bash
rm -rf ~/.forge/browser_data/zhihu
python login_zhihu.py
```

---

## 停止服务

### 停止 Web 服务

```bash
# 查找进程
ps aux | grep run_web

# 停止进程
kill <PID>
```

### 停止 Milvus

```bash
docker-compose down
```

---

## 检查清单

部署前检查：

- [ ] Python 3.10+ 已安装
- [ ] conda 环境已创建并激活
- [ ] 依赖已安装 `pip install -r requirements.txt`
- [ ] Playwright 浏览器已安装 `playwright install chromium`
- [ ] `.env` 文件已创建并配置 API Key
- [ ] Milvus 已启动 `docker-compose up -d`
- [ ] 知识库已初始化 `python import_docs.py`
- [ ] 知乎已登录 `python login_zhihu.py`

---

## 快速命令参考

```bash
# 环境激活
conda activate forge

# 启动 Milvus
docker-compose up -d

# 导入知识库
python import_docs.py

# 知乎登录
python login_zhihu.py

# 启动 Web
python run_web.py

# 查看日志
tail -f forge.log

# 停止服务
docker-compose down
```

---

## 版本信息

**部署指南版本**：1.0.0

**更新日期**：2026-04-13