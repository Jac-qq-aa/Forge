# 历史记录删除功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为历史记录功能添加删除能力（单删/批删/清空全部），采用软删除机制。

**Architecture:** 数据库新增 deleted_at 字段标记已删除，后端提供 3 个 API 端点，前端实现 checkbox 选择 + 删除按钮 + 操作栏 + 确认弹窗。

**Tech Stack:** PostgreSQL (asyncpg), FastAPI, JavaScript (原生), HTML/CSS

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `migrations/002_add_deleted_flag.sql` | 新建 | 数据库迁移：新增 deleted_at 字段 |
| `forge/storage/pg_client.py` | 修改 | PGSessionManager：软删除方法 + 修改查询 |
| `forge/deep_mode/session_manager.py` | 修改 | SessionManager：软删除方法（PG + Redis） |
| `forge/web/app.py` | 修改 | 新增 3 个 API 端点 |
| `forge/web/templates/index.html` | 修改 | 前端 UI：checkbox、删除按钮、操作栏、确认弹窗、JS 函数、CSS |

---

### Task 1: 数据库迁移 - 新增 deleted_at 字段

**Files:**
- Create: `migrations/002_add_deleted_flag.sql`

- [ ] **Step 1: 创建迁移文件**

```sql
-- migrations/002_add_deleted_flag.sql

-- 历史记录软删除功能

-- 新增 deleted_at 字段
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP DEFAULT NULL;

-- 索引：支持查询未删除记录
CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON sessions(deleted_at);

-- 注释
COMMENT ON COLUMN sessions.deleted_at IS '软删除时间戳，NULL 表示未删除';
```

- [ ] **Step 2: 执行迁移**

Run: `psql -h localhost -U postgres -d forge -f migrations/002_add_deleted_flag.sql`
Expected: `ALTER TABLE` 和 `CREATE INDEX` 成功

- [ ] **Step 3: 验证字段存在**

Run: `psql -h localhost -U postgres -d forge -c "\d sessions"`
Expected: 输出包含 `deleted_at` 列

---

### Task 2: PGSessionManager - 新增软删除方法

**Files:**
- Modify: `forge/storage/pg_client.py` (新增 3 个方法)

- [ ] **Step 1: 添加 soft_delete_session 方法**

在 `PGSessionManager` 类末尾（`_row_to_dict` 方法之后）添加：

```python
    # ---- 软删除操作 ----

    async def soft_delete_session(self, session_id: str) -> bool:
        """软删除单条会话。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE sessions SET deleted_at = $2
                WHERE id = $1 AND deleted_at IS NULL
                """,
                UUID(session_id),
                datetime.now(),
            )
        if result == "UPDATE 1":
            logger.info(f"[PG] Session soft deleted: {session_id}")
            return True
        logger.warning(f"[PG] Session not found or already deleted: {session_id}")
        return False

    async def soft_delete_sessions(self, session_ids: List[str]) -> int:
        """批量软删除会话。"""
        if not session_ids:
            return 0
        pool = await get_pg_pool()
        uuids = [UUID(sid) for sid in session_ids]
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE sessions SET deleted_at = $2
                WHERE id = ANY($1) AND deleted_at IS NULL
                """,
                uuids,
                datetime.now(),
            )
        count = int(result.split()[-1]) if result.startswith("UPDATE") else 0
        logger.info(f"[PG] Batch soft deleted {count} sessions")
        return count

    async def soft_delete_all_sessions(self) -> int:
        """软删除所有会话（清空历史）。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE sessions SET deleted_at = $1
                WHERE deleted_at IS NULL
                """,
                datetime.now(),
            )
        count = int(result.split()[-1]) if result.startswith("UPDATE") else 0
        logger.info(f"[PG] All sessions soft deleted: {count}")
        return count
```

- [ ] **Step 2: 添加导入**

在文件顶部的 `from typing import ...` 行添加 `List`（如果还没有）：

```python
from typing import Optional, Dict, Any, List
```

- [ ] **Step 3: 验证语法**

Run: `python -c "from forge.storage.pg_client import PGSessionManager; print('OK')"`
Expected: `OK`

---

### Task 3: PGSessionManager - 修改历史查询过滤已删除记录

**Files:**
- Modify: `forge/storage/pg_client.py:194-212` (get_history_sessions 方法)

- [ ] **Step 1: 修改 get_history_sessions 查询**

找到 `get_history_sessions` 方法（约第 194 行），修改 SQL 查询：

```python
    async def get_history_sessions(
        self,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取历史会话列表（过滤已删除）。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, source_article, stage, created_at, finalized_at
                FROM sessions
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [self._row_to_dict(row) for row in rows]
```

- [ ] **Step 2: 修改 get_session 方法检查已删除**

找到 `get_session` 方法（约第 81 行），修改为：

```python
    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话记录（已删除返回 None）。"""
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM sessions WHERE id = $1 AND deleted_at IS NULL
                """,
                UUID(session_id),
            )
        if not row:
            return None
        return self._row_to_dict(row)
```

- [ ] **Step 3: 验证语法**

Run: `python -c "from forge.storage.pg_client import PGSessionManager; print('OK')"`
Expected: `OK`

---

### Task 4: SessionManager - 新增软删除方法

**Files:**
- Modify: `forge/deep_mode/session_manager.py` (新增 3 个方法)

- [ ] **Step 1: 添加 soft_delete_session 方法**

在 `SessionManager` 类的 `cancel_session` 方法之后添加：

```python
    # ---- 软删除操作 ----

    async def soft_delete_session(self, session_id: str) -> bool:
        """软删除单条会话（双写清理）。"""
        # PG 软删除
        try:
            success = await self.pg.soft_delete_session(session_id)
            if not success:
                logger.warning(f"[SessionManager] Session not found: {session_id}")
                return False
        except Exception as e:
            logger.error(f"[SessionManager] PG soft delete failed: {e}")
            raise

        # Redis 清理（失败可忽略）
        try:
            await self.redis.delete_session(session_id)
        except Exception as e:
            logger.warning(f"[SessionManager] Redis cleanup failed: {e}")

        logger.info(f"[SessionManager] Session soft deleted: {session_id}")
        return True

    async def soft_delete_sessions(self, session_ids: List[str]) -> int:
        """批量软删除会话。"""
        if not session_ids:
            return 0

        # PG 批量软删除
        try:
            count = await self.pg.soft_delete_sessions(session_ids)
        except Exception as e:
            logger.error(f"[SessionManager] PG batch delete failed: {e}")
            raise

        # Redis 批量清理
        for sid in session_ids:
            try:
                await self.redis.delete_session(sid)
            except Exception as e:
                logger.warning(f"[SessionManager] Redis cleanup failed for {sid}: {e}")

        logger.info(f"[SessionManager] Batch soft deleted {count} sessions")
        return count

    async def soft_delete_all_sessions(self) -> int:
        """软删除所有会话（清空历史）。"""
        # PG 清空
        try:
            count = await self.pg.soft_delete_all_sessions()
        except Exception as e:
            logger.error(f"[SessionManager] PG clear all failed: {e}")
            raise

        # Redis 清空（可选，清理所有缓存）
        try:
            # 注意：这里不清理所有 Redis key，只清理已知的 session 缓存
            # Redis 使用 TTL 自动过期，不需要主动清理
            logger.info("[SessionManager] Redis sessions will expire by TTL")
        except Exception as e:
            logger.warning(f"[SessionManager] Redis cleanup skipped: {e}")

        logger.info(f"[SessionManager] All sessions soft deleted: {count}")
        return count
```

- [ ] **Step 2: 添加导入 List**

在文件顶部添加 `List` 到导入：

```python
from typing import Optional, Dict, Any, List
```

- [ ] **Step 3: 验证语法**

Run: `python -c "from forge.deep_mode.session_manager import SessionManager; print('OK')"`
Expected: `OK`

---

### Task 5: app.py - 新增 3 个 API 端点

**Files:**
- Modify: `forge/web/app.py` (新增 3 个端点 + 1 个 Pydantic 模型)

- [ ] **Step 1: 添加 Pydantic 模型**

在现有 Pydantic 模型区域（约第 100-130 行）添加：

```python
class BatchDeleteRequest(BaseModel):
    """批量删除请求。"""
    session_ids: list[str]
```

- [ ] **Step 2: 添加单删端点**

在 `@app.delete("/api/deep_mode/session/{session_id}")` 端点（约第 941 行，api_cancel_session）之后添加：

注意：已有 `api_cancel_session` 使用 DELETE 方法，需要区分。添加新端点：

```python
@app.delete("/api/deep_mode/history/{session_id}")
async def api_delete_history_session(session_id: str):
    """软删除历史记录。"""
    logger.info(f"[API] Delete history session: {session_id}")

    session_manager = get_session_manager()

    try:
        success = await session_manager.soft_delete_session(session_id)
        if not success:
            return {"success": False, "error": "会话不存在或已删除"}, 404
        return {"success": True, "status": "deleted", "session_id": session_id}
    except Exception as e:
        logger.error(f"[API] Delete error: {e}")
        return {"success": False, "error": str(e)}
```

- [ ] **Step 3: 添加批删端点**

继续添加：

```python
@app.post("/api/deep_mode/history/batch_delete")
async def api_batch_delete_history(request: BatchDeleteRequest):
    """批量软删除历史记录。"""
    logger.info(f"[API] Batch delete {len(request.session_ids)} sessions")

    session_manager = get_session_manager()

    try:
        count = await session_manager.soft_delete_sessions(request.session_ids)
        return {"success": True, "status": "deleted", "count": count}
    except Exception as e:
        logger.error(f"[API] Batch delete error: {e}")
        return {"success": False, "error": str(e)}
```

- [ ] **Step 4: 添加清空全部端点**

继续添加：

```python
@app.delete("/api/deep_mode/history/clear_all")
async def api_clear_all_history():
    """清空所有历史记录。"""
    logger.info(f"[API] Clear all history")

    session_manager = get_session_manager()

    try:
        count = await session_manager.soft_delete_all_sessions()
        return {"success": True, "status": "deleted", "count": count}
    except Exception as e:
        logger.error(f"[API] Clear all error: {e}")
        return {"success": False, "error": str(e)}
```

- [ ] **Step 5: 验证语法**

Run: `python -c "from forge.web.app import app; print('OK')"`
Expected: `OK`

---

### Task 6: 前端 - 新增状态变量和 JS 函数

**Files:**
- Modify: `forge/web/templates/index.html` (JS 部分，约第 3008 行附近)

- [ ] **Step 1: 添加状态变量**

在 `let currentHistorySessionId = null;`（约第 3008 行）之后添加：

```javascript
        let selectedSessionIds = [];  // 已选中的 session_id 列表
```

- [ ] **Step 2: 添加 toggleSessionSelect 函数**

在 `closeHistoryModal` 函数之后添加：

```javascript
        function toggleSessionSelect(sessionId) {
            const index = selectedSessionIds.indexOf(sessionId);
            if (index > -1) {
                selectedSessionIds.splice(index, 1);
            } else {
                selectedSessionIds.push(sessionId);
            }
            updateSelectUI();
        }
```

- [ ] **Step 3: 添加 toggleSelectAll 函数**

继续添加：

```javascript
        function toggleSelectAll() {
            const checkboxes = document.querySelectorAll('.history-checkbox');
            const allChecked = selectedSessionIds.length === checkboxes.length;

            if (allChecked) {
                selectedSessionIds = [];
            } else {
                selectedSessionIds = [];
                checkboxes.forEach(cb => {
                    const sessionId = cb.getAttribute('data-session-id');
                    if (sessionId) selectedSessionIds.push(sessionId);
                });
            }
            updateSelectUI();
        }
```

- [ ] **Step 4: 添加 updateSelectUI 函数**

继续添加：

```javascript
        function updateSelectUI() {
            // 更新 checkbox 状态
            const checkboxes = document.querySelectorAll('.history-checkbox');
            checkboxes.forEach(cb => {
                const sessionId = cb.getAttribute('data-session-id');
                cb.checked = selectedSessionIds.includes(sessionId);
            });

            // 更新全选 checkbox
            const selectAllCb = document.getElementById('history-select-all');
            if (selectAllCb) {
                selectAllCb.checked = selectedSessionIds.length > 0 &&
                    selectedSessionIds.length === checkboxes.length;
            }

            // 显示/隐藏操作栏
            const actionBar = document.getElementById('history-action-bar');
            if (actionBar) {
                actionBar.style.display = selectedSessionIds.length > 0 ? 'flex' : 'none';
            }

            // 更新选中数量
            const selectedCount = document.getElementById('history-selected-count');
            if (selectedCount) {
                selectedCount.textContent = selectedSessionIds.length;
            }
        }
```

- [ ] **Step 5: 添加 showDeleteConfirm 函数**

继续添加：

```javascript
        function showDeleteConfirm(count, onConfirm) {
            const modal = document.getElementById('delete-confirm-modal');
            const message = document.getElementById('delete-confirm-message');
            const confirmBtn = document.getElementById('delete-confirm-btn');

            message.textContent = `确定删除 ${count} 条记录？此操作不可恢复。`;
            modal.style.display = 'flex';

            // 移除旧的事件监听器
            const newConfirmBtn = confirmBtn.cloneNode(true);
            confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);

            newConfirmBtn.addEventListener('click', () => {
                modal.style.display = 'none';
                onConfirm();
            });
        }

        function closeDeleteConfirmModal() {
            document.getElementById('delete-confirm-modal').style.display = 'none';
        }
```

- [ ] **Step 6: 添加 deleteSingleSession 函数**

继续添加：

```javascript
        async function deleteSingleSession(sessionId) {
            showDeleteConfirm(1, async () => {
                try {
                    const response = await fetch(`/api/deep_mode/history/${sessionId}`, {
                        method: 'DELETE'
                    });
                    const data = await response.json();

                    if (data.success) {
                        refreshHistoryList();
                    } else {
                        alert('删除失败: ' + data.error);
                    }
                } catch (error) {
                    alert('删除失败: ' + error.message);
                }
            });
        }
```

- [ ] **Step 7: 添加 deleteSelectedSessions 函数**

继续添加：

```javascript
        async function deleteSelectedSessions() {
            if (selectedSessionIds.length === 0) return;

            showDeleteConfirm(selectedSessionIds.length, async () => {
                try {
                    const response = await fetch('/api/deep_mode/history/batch_delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ session_ids: selectedSessionIds })
                    });
                    const data = await response.json();

                    if (data.success) {
                        selectedSessionIds = [];
                        refreshHistoryList();
                    } else {
                        alert('删除失败: ' + data.error);
                    }
                } catch (error) {
                    alert('删除失败: ' + error.message);
                }
            });
        }
```

- [ ] **Step 8: 添加 clearAllHistory 函数**

继续添加：

```javascript
        async function clearAllHistory() {
            showDeleteConfirm('所有', async () => {
                try {
                    const response = await fetch('/api/deep_mode/history/clear_all', {
                        method: 'DELETE'
                    });
                    const data = await response.json();

                    if (data.success) {
                        selectedSessionIds = [];
                        refreshHistoryList();
                    } else {
                        alert('清空失败: ' + data.error);
                    }
                } catch (error) {
                    alert('清空失败: ' + error.message);
                }
            });
        }
```

- [ ] **Step 9: 添加 refreshHistoryList 函数**

继续添加：

```javascript
        function refreshHistoryList() {
            showHistoryModal();
        }
```

- [ ] **Step 10: 修改 showHistoryModal 函数渲染列表**

找到 `showHistoryModal` 函数（约第 3010 行），修改 `html` 生成部分，在 `history-item` 中添加 checkbox 和删除按钮：

```javascript
        async function showHistoryModal() {
            document.getElementById('history-modal').style.display = 'flex';
            document.getElementById('history-list').innerHTML = '<p class="loading-text">加载中...</p>';

            // 重置选中状态
            selectedSessionIds = [];
            updateSelectUI();

            try {
                const response = await fetch('/api/deep_mode/history?limit=20');
                const data = await response.json();

                if (data.sessions && data.sessions.length > 0) {
                    let html = '';
                    data.sessions.forEach(session => {
                        const title = session.source_article?.title || '无标题';
                        const date = session.created_at ? new Date(session.created_at).toLocaleString('zh-CN') : '';
                        const stageText = {
                            'completed': '已完成',
                            'tuning': '改写中',
                            'waiting_outline': '大纲待确认',
                            'cancelled': '已取消',
                        }[session.stage] || session.stage;

                        html += `
                            <div class="history-item" data-session-id="${session.session_id}">
                                <input type="checkbox" class="history-checkbox"
                                    data-session-id="${session.session_id}"
                                    onclick="toggleSessionSelect('${session.session_id}')">
                                <div class="history-item-info" onclick="showSessionDetail('${session.session_id}')">
                                    <div class="history-item-title">${escapeHtml(title.substring(0, 50))}</div>
                                    <div class="history-item-meta">${date}</div>
                                </div>
                                <span class="history-item-stage">${stageText}</span>
                                <button class="history-delete-btn" onclick="event.stopPropagation(); deleteSingleSession('${session.session_id}')">
                                    🗑️
                                </button>
                            </div>
                        `;
                    });
                    document.getElementById('history-list').innerHTML = html;
                    updateSelectUI();
                } else {
                    document.getElementById('history-list').innerHTML = '<p class="empty-text">暂无历史记录</p>';
                    document.getElementById('history-action-bar').style.display = 'none';
                }
            } catch (error) {
                document.getElementById('history-list').innerHTML = '<p class="empty-text">加载失败: ' + error.message + '</p>';
            }
        }
```

---

### Task 7: 前端 - 新增 UI 元素

**Files:**
- Modify: `forge/web/templates/index.html` (HTML 部分，约第 21-46 行)

- [ ] **Step 1: 修改历史记录弹窗标题栏**

找到历史记录弹窗（约第 21-46 行），修改标题栏添加全选 checkbox：

```html
    <div id="history-modal" class="modal" style="display: none;">
        <div class="modal-content history-modal-content">
            <div class="modal-header">
                <div class="history-header-left">
                    <input type="checkbox" id="history-select-all" onclick="toggleSelectAll()">
                    <h3>📜 历史记录</h3>
                </div>
                <button class="modal-close" onclick="closeHistoryModal()">×</button>
            </div>
            <div id="history-list" class="history-list">
                <!-- 动态内容 -->
            </div>
            <div id="history-action-bar" class="history-action-bar" style="display: none;">
                <span>已选中 <span id="history-selected-count">0</span> 条</span>
                <button class="btn btn-danger btn-small" onclick="deleteSelectedSessions()">删除选中</button>
                <button class="btn btn-secondary btn-small" onclick="clearAllHistory()">清空全部</button>
            </div>
            <div class="modal-footer" id="history-detail-footer" style="display: none;">
                <button class="btn btn-secondary" onclick="closeSessionDetail()">返回列表</button>
                <button id="history-download-text-btn" class="btn btn-secondary" onclick="downloadHistoryText()" style="display: none;">📥 下载文本</button>
                <button id="history-download-video-btn" class="btn btn-secondary" onclick="downloadHistoryVideo()" style="display: none;">🎬 下载视频</button>
            </div>
        </div>
    </div>
```

- [ ] **Step 2: 添加删除确认弹窗**

在历史记录弹窗之后添加：

```html
    <div id="delete-confirm-modal" class="modal" style="display: none;">
        <div class="modal-content delete-confirm-content">
            <div class="modal-header">
                <h3>⚠️ 确认删除</h3>
            </div>
            <div class="modal-body">
                <p id="delete-confirm-message">确定删除记录？此操作不可恢复。</p>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeDeleteConfirmModal()">取消</button>
                <button id="delete-confirm-btn" class="btn btn-danger">确认删除</button>
            </div>
        </div>
    </div>
```

---

### Task 8: 前端 - 新增 CSS 样式

**Files:**
- Modify: `forge/web/templates/index.html` (CSS 部分，style 标签内)

- [ ] **Step 1: 添加历史记录相关样式**

在 `<style>` 标签内添加新样式：

```css
        /* 历史记录弹窗 */
        .history-modal-content {
            max-width: 600px;
        }

        .history-header-left {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .history-header-left input[type="checkbox"] {
            width: 18px;
            height: 18px;
            cursor: pointer;
        }

        /* 历史列表项 */
        .history-item {
            display: flex;
            align-items: center;
            padding: 12px 15px;
            border-bottom: 1px solid #eee;
            gap: 10px;
            transition: background 0.2s;
        }

        .history-item:hover {
            background: #f9f9f9;
        }

        .history-checkbox {
            width: 16px;
            height: 16px;
            cursor: pointer;
            flex-shrink: 0;
        }

        .history-item-info {
            flex: 1;
            cursor: pointer;
        }

        .history-item-stage {
            font-size: 12px;
            color: #666;
            background: #f0f0f0;
            padding: 2px 8px;
            border-radius: 4px;
            flex-shrink: 0;
        }

        .history-delete-btn {
            background: none;
            border: none;
            font-size: 16px;
            cursor: pointer;
            padding: 4px 8px;
            opacity: 0.5;
            transition: opacity 0.2s, color 0.2s;
            flex-shrink: 0;
        }

        .history-delete-btn:hover {
            opacity: 1;
            color: #e74c3c;
        }

        /* 操作栏 */
        .history-action-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 15px;
            background: #fff3cd;
            border-top: 1px solid #ffc107;
            gap: 10px;
        }

        .history-action-bar span {
            color: #856404;
            font-weight: 500;
        }

        /* 删除确认弹窗 */
        .delete-confirm-content {
            max-width: 400px;
        }

        #delete-confirm-message {
            font-size: 14px;
            color: #333;
            text-align: center;
        }

        /* 按钮样式 */
        .btn-danger {
            background: #e74c3c;
            color: white;
        }

        .btn-danger:hover {
            background: #c0392b;
        }
```

---

### Task 9: 集成测试验证

- [ ] **Step 1: 启动服务器**

Run: `cd /home/hugo/Forge && python -m forge.web.app`
Expected: 服务器启动在 localhost:8000

- [ ] **Step 2: 测试历史记录 API**

Run: `curl http://localhost:8000/api/deep_mode/history?limit=5`
Expected: 返回 JSON 包含 sessions 数组

- [ ] **Step 3: 测试单删 API**

Run: `curl -X DELETE http://localhost:8000/api/deep_mode/history/<session_id>`
Expected: 返回 `{"success": true, "status": "deleted"}`

- [ ] **Step 4: 测试批删 API**

Run: `curl -X POST http://localhost:8000/api/deep_mode/history/batch_delete -H "Content-Type: application/json" -d '{"session_ids": ["id1", "id2"]}'`
Expected: 返回 `{"success": true, "count": N}`

- [ ] **Step 5: 测试清空 API**

Run: `curl -X DELETE http://localhost:8000/api/deep_mode/history/clear_all`
Expected: 返回 `{"success": true, "count": N}`

- [ ] **Step 6: 前端手动测试**

打开浏览器访问 http://localhost:8000：
1. 点击"历史记录"按钮
2. 测试单删：点击垃圾桶图标，确认弹窗，删除后列表刷新
3. 测试批删：勾选多条，点击"删除选中"，确认后删除
4. 测试清空：点击"清空全部"，确认后清空

---

### Task 10: 提交代码

- [ ] **Step 1: 添加所有修改文件**

```bash
git add migrations/002_add_deleted_flag.sql
git add forge/storage/pg_client.py
git add forge/deep_mode/session_manager.py
git add forge/web/app.py
git add forge/web/templates/index.html
git add docs/superpowers/specs/2026-04-23-history-delete-design.md
git add docs/superpowers/plans/2026-04-23-history-delete.md
```

- [ ] **Step 2: 提交**

```bash
git commit -m "$(cat <<'EOF'
feat: add history delete functionality (soft delete)

- Database: add deleted_at column for soft delete
- Backend: 3 new API endpoints (single/batch/clear all delete)
- Frontend: checkbox selection, delete buttons, action bar, confirm modal

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```