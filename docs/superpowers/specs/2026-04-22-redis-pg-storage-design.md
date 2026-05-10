# Redis + PostgreSQL 存储架构设计

> **创建日期：** 2026-04-22
> **目标：** 将 Deep Mode 会话存储从 SQLite 改为 Redis + PostgreSQL 双存储架构

---

## 背景

当前 Deep Mode 使用 SQLite (`sessions.db`) 存储会话数据，存在以下问题：
1. 不支持多实例共享（单机存储）
2. 高并发写入性能有限
3. 无会话历史记录列表功能（类似 ChatGPT）
4. 无自动清理机制

---

## 设计目标

1. **多实例共享** - 多个后端实例共享同一份会话状态
2. **高并发性能** - Redis 作为活跃会话缓存层
3. **历史记录列表** - PostgreSQL 持久化，支持查询历史会话
4. **数据安全** - 关键节点备份 + 异断恢复机制

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                     WebSocket Handler                        │
│                  (forge/deep_mode/websocket_handler.py)      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Session Manager                           │
│                 (forge/deep_mode/session_manager.py)         │
│                                                             │
│  ┌─────────────────┐    ┌─────────────────┐                │
│  │  Redis Client   │    │  PostgreSQL     │                │
│  │  (活跃会话)     │    │  (持久化)       │                │
│  └─────────────────┘    └─────────────────┘                │
│        │                       │                            │
│        │   关键节点同步写入     │                            │
│        └───────────────────────┘                            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Workflow Agent                            │
│                 (forge/deep_mode/workflow.py)                │
└─────────────────────────────────────────────────────────────┘
```

---

## 存储职责划分

### Redis（活跃层）

| Key | 类型 | 内容 | TTL |
|-----|------|------|-----|
| `session:{id}` | Hash | 会话状态（stage, current_draft, outline等） | 30分钟 |
| `session:{id}:messages` | List | 消息队列（最近消息） | 30分钟 |
| `ws:{session_id}` | String | WebSocket连接映射（server_id） | 随WS断开删除 |

**配置要求：**
- `appendonly yes` - 开启 AOF 持久化
- `appendfsync everysec` - 每秒同步，最多丢1秒数据

### PostgreSQL（持久层）

| 表 | 职责 |
|----|------|
| `sessions` | 会话元数据、最终版本 |
| `session_messages` | 所有消息历史 |
| `session_versions` | 文章版本记录 |

---

## PostgreSQL 表结构

```sql
-- 会话元数据表
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_article JSONB NOT NULL,
    user_input TEXT,
    stage VARCHAR(20) NOT NULL,
    outline JSONB,                      -- 结构化大纲
    outline_version INT DEFAULT 0,
    rag_context TEXT,
    current_draft TEXT,                 -- 当前草稿
    is_active BOOLEAN DEFAULT TRUE,     -- 是否活跃
    last_heartbeat TIMESTAMP,           -- 最后心跳时间
    lock_version INT DEFAULT 1,         -- 乐观锁
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    finalized_at TIMESTAMP,
    final_draft TEXT
);

-- 消息历史表
CREATE TABLE session_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR(10) NOT NULL,
    content TEXT NOT NULL,
    is_question BOOLEAN DEFAULT FALSE,
    token_count INT,                    -- Token统计
    metadata JSONB,                     -- 扩展字段
    created_at TIMESTAMP DEFAULT NOW()
);

-- 文章版本表
CREATE TABLE session_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    version INT NOT NULL,
    draft TEXT NOT NULL,
    token_count INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_sessions_stage ON sessions(stage);
CREATE INDEX idx_sessions_created ON sessions(created_at DESC);
CREATE INDEX idx_sessions_active ON sessions(is_active, last_heartbeat);
CREATE INDEX idx_messages_session ON session_messages(session_id, created_at);
CREATE INDEX idx_versions_session ON session_versions(session_id, version);
```

---

## 数据结构示例

### Outline JSONB 结构

```json
{
  "sections": [
    {
      "id": "section_1",
      "title": "一、引言",
      "keywords": ["背景", "现状"],
      "word_count": 300,
      "subsections": [
        {"title": "1.1 问题背景", "points": ["..."]}
      ]
    }
  ],
  "total_word_count": 1500,
  "tone": "专业"
}
```

### Metadata JSONB 结构

```json
{
  "search_queries": ["AI写作"],
  "thought_process": "用户要求修改第二段...",
  "tool_calls": [{"tool": "rewrite_section", "args": {"section": "第二段"}}],
  "model": "qwen-plus",
  "latency_ms": 1500
}
```

---

## 关键节点写入策略

| 节点 | Redis | PostgreSQL | 说明 |
|------|-------|------------|------|
| 创建session | ✅ 立即 | ✅ 立即 | 元数据双写 |
| 生成大纲完成 | ✅ 更新 | ✅ 备份 | outline写入PG |
| 确认/修改大纲 | ✅ 更新 | ✅ 备份 | outline_version更新 |
| 生成全文完成 | ✅ 更新 | ✅ 备份 | draft写入versions表 |
| 微调对话每条消息 | ✅ 追加 | ✅ 追加 | messages增量写入 |
| 定稿 | ✅ 删除 | ✅ 最终保存 | final_draft写入 |
| WebSocket断开 | - | ✅ 保存当前状态 | 异常备份 |

---

## 异常处理流程

### 心跳检测
- WebSocket 每次交互时更新 `last_heartbeat`
- Redis TTL 自动清理不活跃会话

### 优雅降级
```python
async def on_websocket_disconnect(session_id):
    # 立即保存当前状态到PG
    await save_session_to_postgres(session_id)
    # 标记为非活跃
    await pg_update_session(session_id, is_active=False)
```

### 恢复上下文
```python
async def restore_session(session_id):
    # 优先查Redis
    session = await redis_get_session(session_id)
    if session:
        return session
    
    # Redis无数据，从PG恢复
    session = await pg_get_session(session_id)
    if session and session['is_active']:
        # 重建Redis缓存
        await redis_restore_session(session)
        return session
    
    return None
```

---

## 文件改造清单

| 文件 | 改造内容 |
|------|----------|
| `forge/deep_mode/session_manager.py` | 重写为双存储管理器 |
| `forge/deep_mode/session_state.py` | 更新数据结构定义 |
| `forge/deep_mode/websocket_handler.py` | 添加心跳、断开保存逻辑 |
| `forge/deep_mode/workflow.py` | 适配新的session接口 |
| `forge/web/app.py` | 添加历史列表API、恢复API |
| `forge/config.py` | 添加Redis/PG配置 |
| 新增 `forge/storage/redis_client.py` | Redis连接管理 |
| 新增 `forge/storage/pg_client.py` | PostgreSQL连接管理 |
| 新增 `migrations/001_redis_pg_storage.sql` | 数据库迁移脚本 |

---

## 新增API端点

| 端点 | 功能 |
|------|------|
| `GET /api/deep_mode/history` | 获取历史会话列表 |
| `GET /api/deep_mode/session/{id}/messages` | 获取会话完整消息历史 |
| `POST /api/deep_mode/session/{id}/restore` | 恢复中断的会话 |

---

## 依赖新增

```toml
# pyproject.toml 或 requirements.txt
redis = "^5.0"
asyncpg = "^0.29"  # 异步PostgreSQL驱动
```

---

## 验收标准

1. 多实例部署时，WebSocket可共享会话状态
2. 会话历史列表可正确展示所有历史会话
3. WebSocket异常断开后，用户可恢复会话
4. 定稿后的数据完整保存到PG
5. Token统计功能可用
6. 历史会话可查询消息详情