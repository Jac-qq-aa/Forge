# Forge API 接口文档

## 接口概览

Forge Web 服务基于 FastAPI 构建，提供以下核心接口：

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/search` | POST | 搜索文章 |
| `/api/get_answers` | POST | 获取回答列表 |
| `/api/process` | POST | 处理单篇文章 |
| `/api/process_multi` | POST | 合并多回答处理 |
| `/api/save` | POST | 保存编辑内容 |
| `/api/status` | GET | 服务状态 |

## 基础信息

**服务地址**：`http://localhost:8000`

**请求格式**：JSON

**响应格式**：JSON

---

## 1. 搜索文章

### 接口

```
POST /api/search
```

### 请求参数

```json
{
  "source": "关键词或博主ID",
  "source_platform": "zhihu | wechat",
  "max_results": 5,
  "search_mode": "keyword | blogger"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| source | string | 是 | 搜索关键词或博主ID |
| source_platform | string | 是 | 来源平台：zhihu 或 wechat |
| max_results | integer | 否 | 最大结果数，默认5 |
| search_mode | string | 否 | 搜索模式：keyword 或 blogger |

### 响应示例

```json
{
  "success": true,
  "articles": [
    {
      "id": 1,
      "title": "文章标题",
      "summary": "摘要内容...",
      "source_url": "https://zhihu.com/question/xxx",
      "type": "知乎问答",
      "author": "作者名"
    }
  ],
  "count": 1
}
```

### 错误响应

```json
{
  "success": false,
  "error": "错误信息",
  "articles": []
}
```

### 使用示例

**关键词搜索**：
```javascript
fetch('/api/search', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    source: "人力资源管理",
    source_platform: "zhihu",
    max_results: 10,
    search_mode: "keyword"
  })
})
```

**博主文章**：
```javascript
fetch('/api/search', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    source: "user-id-xxx",
    source_platform: "zhihu",
    max_results: 5,
    search_mode: "blogger"
  })
})
```

---

## 2. 获取回答列表

### 接口

```
POST /api/get_answers
```

### 请求参数

```json
{
  "question_url": "知乎问题URL",
  "max_answers": 10
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| question_url | string | 是 | 知乎问题完整URL |
| max_answers | integer | 否 | 最大回答数，默认10 |

### 响应示例

```json
{
  "success": true,
  "question_title": "问题标题",
  "answers": [
    {
      "id": 1,
      "text": "回答内容摘要...",
      "likes": 1234,
      "author": "作者名",
      "char_count": 500,
      "source_url": "https://zhihu.com/question/xxx/answer/yyy"
    },
    {
      "id": 2,
      "text": "回答内容摘要...",
      "likes": 856,
      "author": "作者名2",
      "char_count": 800,
      "source_url": "https://zhihu.com/question/xxx/answer/zzz"
    }
  ]
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| question_title | string | 问题标题 |
| answers | array | 回答列表，按点赞排序 |
| id | integer | 回答序号 |
| text | string | 回答内容摘要 |
| likes | integer | 点赞数 |
| author | string | 作者名 |
| char_count | integer | 字数统计 |
| source_url | string | 回答完整URL |

### 使用示例

```javascript
// 用户点击知乎问题后获取回答列表
async function getAnswers(questionUrl) {
  const response = await fetch('/api/get_answers', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      question_url: questionUrl,
      max_answers: 10
    })
  });
  return response.json();
}
```

---

## 3. 处理单篇文章

### 接口

```
POST /api/process
```

### 请求参数

```json
{
  "source_url": "文章URL",
  "source_platform": "zhihu | wechat",
  "target_platform": "zhihu_article | wechat_article"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| source_url | string | 是 | 文章完整URL |
| source_platform | string | 是 | 来源平台 |
| target_platform | string | 是 | 目标平台格式 |

### 响应示例

```json
{
  "success": true,
  "script_path": "/path/to/output.txt",
  "video_path": "",
  "script_content": "改写后的完整内容...",
  "final_script": "最终文案摘要...",
  "original_title": "原文标题",
  "original_text": "原文完整内容",
  "original_author": "来源公众号或作者"
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| script_path | string | 保存的文案文件路径 |
| video_path | string | 视频文件路径（视频平台时） |
| script_content | string | 改写后的完整内容 |
| final_script | string | 最终文案（可能截断） |
| original_title | string | 原文标题 |
| original_text | string | 原文完整内容 |
| original_author | string | 原文作者或公众号名 |

### 使用示例

```javascript
async function processArticle(url) {
  const response = await fetch('/api/process', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      source_url: url,
      source_platform: "zhihu",
      target_platform: "zhihu_article"
    })
  });
  return response.json();
}
```

---

## 4. 合并多回答处理

### 接口

```
POST /api/process_multi
```

### 请求参数

```json
{
  "question_url": "问题URL",
  "answer_urls": ["回答URL1", "回答URL2", "回答URL3"],
  "source_platform": "zhihu",
  "target_platform": "zhihu_article | wechat_article"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| question_url | string | 是 | 知乎问题URL |
| answer_urls | array | 是 | 选中的回答URL列表 |
| source_platform | string | 是 | 来源平台 |
| target_platform | string | 是 | 目标平台 |

### 响应示例

```json
{
  "success": true,
  "script_path": "/path/to/output.txt",
  "script_content": "合并改写后的内容...",
  "final_script": "最终文案",
  "original_title": "问题标题",
  "original_text": "合并后的所有回答原文",
  "answer_count": 3
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| answer_count | integer | 合并的回答数量 |
| original_text | string | 所有回答原文合并（用分隔符连接） |

### 使用示例

```javascript
// 用户选择多个回答后合并处理
async function processMultiAnswers(questionUrl, selectedUrls) {
  const response = await fetch('/api/process_multi', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      question_url: questionUrl,
      answer_urls: selectedUrls,
      source_platform: "zhihu",
      target_platform: "zhihu_article"
    })
  });
  return response.json();
}
```

---

## 5. 保存编辑内容

### 接口

```
POST /api/save
```

### 请求参数

```json
{
  "script_path": "文案文件路径",
  "content": "编辑后的内容"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| script_path | string | 是 | 文案文件路径 |
| content | string | 是 | 编辑后的完整内容 |

### 响应示例

```json
{
  "success": true,
  "script_path": "/path/to/output.txt"
}
```

### 错误响应

```json
{
  "success": false,
  "error": "没有文案路径"
}
```

### 使用示例

```javascript
async function saveContent(path, editedContent) {
  const response = await fetch('/api/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      script_path: path,
      content: editedContent
    })
  });
  return response.json();
}
```

---

## 6. 服务状态

### 接口

```
GET /api/status
```

### 响应示例

```json
{
  "status": "ok",
  "service": "Forge Web"
}
```

### 用途

用于健康检查和服务状态监控。

---

## 接口调用流程

### 单篇文章处理流程

```
1. 调用 /api/search 搜索文章
2. 用户选择文章，获取 source_url
3. 调用 /api/process 处理文章
4. 获取改写结果，展示原文 vs 改写对比
5. 用户编辑后调用 /api/save 保存
```

### 多回答合并处理流程

```
1. 调用 /api/search 搜索，返回知乎问题
2. 用户点击问题，调用 /api/get_answers 获取回答列表
3. 用户多选回答
4. 调用 /api/process_multi 合并处理
5. 获取改写结果，展示对比
6. 用户编辑后调用 /api/save 保存
```

## 错误处理

### 错误类型

| 错误 | 说明 | 处理建议 |
|------|------|----------|
| 爬虫失败 | 网络问题或反爬检测 | 检查登录状态，重试 |
| 平台不支持 | 指定平台未实现 | 检查平台参数 |
| URL 无效 | 无法识别的URL格式 | 检查URL格式 |
| LLM 错误 | API调用失败 | 检查API Key配置 |
| 知识库错误 | Milvus连接失败 | 检查Milvus服务状态 |

### 错误响应格式

所有接口在出错时返回统一格式：

```json
{
  "success": false,
  "error": "具体错误信息描述"
}
```

前端应根据 `success` 字段判断请求是否成功，失败时展示 `error` 信息。

---

## 请求示例（curl）

### 搜索知乎文章

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{
    "source": "人力资源管理",
    "source_platform": "zhihu",
    "max_results": 5
  }'
```

### 获取回答列表

```bash
curl -X POST http://localhost:8000/api/get_answers \
  -H "Content-Type: application/json" \
  -d '{
    "question_url": "https://www.zhihu.com/question/123456",
    "max_answers": 10
  }'
```

### 处理单篇文章

```bash
curl -X POST http://localhost:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{
    "source_url": "https://zhuanlan.zhihu.com/p/123456",
    "source_platform": "zhihu",
    "target_platform": "zhihu_article"
  }'
```

### 合并多回答

```bash
curl -X POST http://localhost:8000/api/process_multi \
  -H "Content-Type: application/json" \
  -d '{
    "question_url": "https://www.zhihu.com/question/123456",
    "answer_urls": [
      "https://www.zhihu.com/question/123456/answer/111",
      "https://www.zhihu.com/question/123456/answer/222"
    ],
    "source_platform": "zhihu",
    "target_platform": "zhihu_article"
  }'
```

---

## 注意事项

1. **知乎登录状态**：使用知乎功能前需先运行 `python login_zhihu.py` 扫码登录
2. **Milvus服务**：知识库功能依赖 Milvus，需先启动 `docker-compose up -d`
3. **并发限制**：LLM API 有速率限制，大量请求可能需要等待
4. **爬虫稳定性**：反爬机制可能导致抓取失败，建议保存登录状态

---

## 版本信息

**API版本**：1.0.0

**更新日期**：2026-04-13

**服务端口**：8000