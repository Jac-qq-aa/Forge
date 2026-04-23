---
name: history-delete-feature
description: 历史记录删除功能 - 单删/批删/清空全部，软删除机制
type: project
---

# 历史记录删除功能设计

**Why:** 用户需要管理历史会话记录，删除不需要的内容。
**How to apply:** 实现软删除机制，前端提供单删/批删/清空操作。

---

## 概述

为历史记录功能添加删除能力：
- **单删**：每条记录右侧删除按钮，点击后弹窗确认
- **批删**：左侧 checkbox 选中多条，底部显示"删除选中"按钮
- **清空全部**：底部"清空全部"按钮，二次确认弹窗

采用**软删除机制**：新增 `deleted_at` 字段标记已删除记录，历史查询过滤掉已删除记录。

---

## 数据库层

### 新增字段

```sql
-- migrations/002_add_deleted_flag.sql
ALTER TABLE sessions ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL;
CREATE INDEX idx_sessions_deleted ON sessions(deleted_at);
```

### 修改查询

`get_history_sessions` 增加 `WHERE deleted_at IS NULL` 条件。

### 新增方法（PGSessionManager）

| 方法 | 功能 |
|------|------|
| `soft_delete_session(session_id)` | 设置单条 `deleted_at = NOW()` |
| `soft_delete_sessions(session_ids)` | 批量设置多条 `deleted_at` |
| `soft_delete_all_sessions()` | 设置所有记录 `deleted_at` |

---

## 后端 API 层

### 新增端点

| 端点 | 方法 | 功能 | 参数 |
|------|------|------|------|
| `/api/deep_mode/session/{session_id}` | DELETE | 单条删除 | session_id |
| `/api/deep_mode/sessions/batch_delete` | POST | 批量删除 | `{"session_ids": [...]}` |
| `/api/deep_mode/sessions/clear_all` | DELETE | 清空全部 | 无 |

### SessionManager 新增方法

| 方法 | 功能 |
|------|------|
| `soft_delete_session(session_id)` | 软删除单条（PG + Redis 清理） |
| `soft_delete_sessions(session_ids)` | 批量软删除 |
| `soft_delete_all_sessions()` | 清空全部 |

---

## 前端 UI 层

### UI 结构

```
历史记录弹窗
├── 顶部标题栏
│   ├── "历史记录" 标题
│   ├── 全选 checkbox
│   └── 关闭按钮 ×
├── 列表区域
│   └── history-item
│       ├── 左侧 checkbox
│       ├── 中间：标题 + 时间
│       └── 右侧：状态标签 + 删除按钮
├── 底部操作栏（选中时显示）
│   ├── "已选中 N 条" 提示
│   ├── "删除选中" 按钮（红色）
│   └── "清空全部" 按钮（灰色）
└── 确认弹窗
    ├── 提示文字
    ├── "取消" 按钮
    └── "确认删除" 按钮
```

### 新增 JS 函数

- `toggleSessionSelect(sessionId)` - 单条选择
- `toggleSelectAll()` - 全选/取消
- `updateSelectUI()` - 更新选中状态
- `showDeleteConfirm(count, callback)` - 确认弹窗
- `deleteSingleSession(sessionId)` - 单删
- `deleteSelectedSessions()` - 批删
- `clearAllHistory()` - 清空全部
- `refreshHistoryList()` - 刷新列表

---

## 边缘情况

| 场景 | 处理 |
|------|------|
| 已删除会话被直接访问 | 返回 404 "会话已删除" |
| Redis 清理失败 | 记录警告日志，不影响结果 |
| 批删时部分不存在 | 跳过不存在的，返回实际删除数量 |
| 清空全部无记录 | 返回 count=0 |