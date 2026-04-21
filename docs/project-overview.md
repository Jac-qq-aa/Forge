# Forge 项目概述

## 项目简介

Forge 是一个多平台内容转换工作流系统，支持：
- 从知乎/微信公众号抓取内容
- AI 改写生成原创内容（结合锐博集团知识库）
- 输出为知乎文章/微信公众号文章格式

## 平台支持

| 平台 | 作为来源 | 作为目标 |
|------|----------|----------|
| 知乎 | ✓ | ✓ (文章) |
| 微信公众号 | ✓ | ✓ (文章) |

## 功能特性

### 1. 搜索功能
- **关键词搜索**：输入关键词搜索相关文章
- **博主文章**：选择博主获取其近期文章（知乎）
- **博主管理**：添加/删除博主，支持备注名

### 2. 回答筛选功能（新增）
知乎问题支持智能回答筛选：
- **点赞排序**：按点赞数从高到低排序
- **字数过滤**：只展示字数 ≥ 300 的回答
- **多回答合并**：选中多个回答后合并洗稿
- **信息展示**：显示点赞数👍、字数📝、作者👤

### 3. 内容处理
- **AI 改写**：使用 Qwen LLM 改写内容，保留核心观点
- **知识库结合**：自动搜索锐博集团知识库，自然融入品牌信息
- **原文对比**：左边原文，右边改写内容，方便对比
- **动态篇幅**：根据原文长度自动调整改写篇幅

### 4. 内容编辑
- 支持编辑改写后的内容
- 保存修改后的文案

## 项目结构

```
forge/
├── config.py                 # 配置管理
├── tools/
│   ├── llm_client.py         # Qwen LLM 封装
│   ├── zhihu_scraper_persistent.py  # 知乎爬虫（持久化上下文）
│   ├── wechat_scraper.py     # 微信公众号爬虫（搜狗搜索）
│   ├── tts_generator.py      # Edge TTS
│   ├── video_composer.py     # FFmpeg 合成
│   └── zhihu_publisher.py    # 知乎发布
├── agents/
│   ├── scout.py              # 内容抓取节点
│   ├── editor.py             # AI改写节点
│   ├── reviewer.py           # 审核节点
│   ├── director.py           # 输出生成节点
│   └── publisher.py          # 发布节点
├── knowledge/
│   ├── config.py             # Milvus 配置
│   └── manager.py            # 知识库管理
├── graph/
│   ├── state.py              # 状态定义
│   └── workflow.py           # LangGraph 工作流
└── web/
    ├── app.py                # FastAPI 后端
    ├── templates/index.html  # 前端页面
    └── static/style.css      # 样式（腾讯风格）
```

## 知识库

### 数据存储
- **向量数据库**：Milvus
- **向量模型**：all-MiniLM-L6-v2 (384维)
- **Collection**：ruibo_knowledge

### 文档导入
```bash
# 导入 docx 文档
python import_docs.py

# 查询知识库
python query_knowledge.py
```

### 文档切分策略
- **按段落切分**：保持语义完整性
- **短段落合并**：多个短段落合并为一条
- **长段落切分**：按句号/问号切分
- **片段长度**：100-500字

## Web 界面

### UI设计（腾讯风格）
- **淡蓝色渐变背景**
- **居中大标题**：蓝色渐变header区块
- **锐博Logo**：右上角固定显示
- **卡片设计**：白色背景、细边框
- **按钮风格**：蓝色主按钮、白色边框次要按钮

### 访问地址
http://localhost:8000

### 启动方式
```bash
python run_web.py
```

### 界面功能
1. **配置参数**
   - 来源平台：知乎/微信公众号
   - 目标平台：知乎文章/微信公众号文章
   - 搜索方式：关键词/博主文章
   - 博主管理：添加/删除/备注

2. **搜索文章**
   - 显示搜索结果列表
   - 显示公众号名称（微信文章）

3. **回答筛选**（知乎问题）
   - 显示回答列表（点赞、字数、作者）
   - 多选回答合并洗稿
   - 返回文章列表

4. **生成结果**
   - 左右对比：原文 vs 改写
   - 编辑功能：修改改写内容
   - 保存功能：保存修改后的文案

## 爬虫实现

### 知乎爬虫
- **持久化浏览器上下文**：保存登录状态
- **数据目录**：~/.forge/browser_data/zhihu
- **支持功能**：
  - 关键词搜索
  - 博主文章获取
  - 文章内容抓取
  - 问题回答列表获取

### 登录方式
```bash
# 运行登录脚本
python login_zhihu.py
```
弹出浏览器窗口，扫码登录后自动保存状态。

### 微信公众号爬虫
- **搜索入口**：搜狗微信搜索 (weixin.sogou.com)
- **持久化浏览器上下文**：避免反爬虫
- **数据目录**：~/.forge/browser_data/wechat
- **特殊处理**：
  - 点击搜索结果进入文章（避免直接访问跳转链接）
  - 从搜狗URL提取关键词重新搜索

## API 接口

### /api/search
搜索文章
```json
{
  "source": "关键词或博主ID",
  "source_platform": "zhihu|wechat",
  "max_results": 5,
  "search_mode": "keyword|blogger"
}
```

### /api/get_answers（新增）
获取知乎问题的回答列表
```json
{
  "question_url": "知乎问题URL",
  "max_answers": 10
}
```

返回：
```json
{
  "success": true,
  "question_title": "问题标题",
  "answers": [
    {
      "id": 1,
      "text": "回答内容",
      "likes": 1234,
      "author": "作者名",
      "char_count": 500,
      "source_url": "回答链接"
    }
  ]
}
```

### /api/process_multi（新增）
合并多个回答洗稿
```json
{
  "question_url": "问题URL",
  "answer_urls": ["回答URL1", "回答URL2"],
  "source_platform": "zhihu",
  "target_platform": "zhihu_article"
}
```

### /api/process
处理单篇文章
```json
{
  "source_url": "文章URL",
  "source_platform": "zhihu|wechat",
  "target_platform": "zhihu_article|wechat_article"
}
```

返回：
```json
{
  "success": true,
  "script_content": "改写内容",
  "original_title": "原文标题",
  "original_text": "原文内容",
  "original_author": "来源公众号"
}
```

### /api/save
保存编辑内容
```json
{
  "script_path": "文件路径",
  "content": "修改后的内容"
}
```

## 改写篇幅策略

根据原文长度动态调整：

| 原文长度 | 目标篇幅 |
|---------|---------|
| >3000字 | 1500-2000字，多段落深入 |
| >1500字 | 800-1200字 |
| >500字 | 600-900字 |
| ≤500字 | 500-800字 |

## 配置

### 环境变量 (.env)
```
QWEN_API_KEY=your-api-key
QWEN_MODEL=qwen3.5-plus
```

### Milvus 配置
```yaml
# docker-compose.yml
services:
  milvus:
    ports: 19530:19530
```

启动：
```bash
docker-compose up -d
```

## 常用命令

```bash
# 激活环境
conda activate forge

# 启动 Web 服务
python run_web.py

# 知乎登录
python login_zhihu.py

# 启动 Milvus GUI (Attu)
docker run -d --name attu -p 3000:3000 -e MILVUS_URL=host.docker.internal:19530 zilliz/attu:latest

# 导入知识库文档
python import_docs.py

# 查询知识库
python query_knowledge.py

# 测试知乎爬虫
python test_zhihu_blogger.py

# 测试微信爬虫
python test_wechat_scraper.py
```

## 注意事项

1. **反爬虫处理**
   - 知乎/搜狗可能触发反爬虫
   - 使用持久化浏览器上下文保存登录状态
   - 避免直接访问跳转链接（微信）

2. **知识库搜索**
   - Editor节点自动搜索相关知识
   - 知识库信息作为"参考资料"融入改写
   - 保持原文核心观点，锐博信息自然补充

3. **目标平台**
   - 微信公众号无公开API，生成文本文件供手动发布
   - 知乎文章同样生成文本文件

## 待办事项

- [ ] 移除小红书相关代码
- [ ] 添加百家号支持
- [ ] 语义分割文档导入
- [ ] 接入微信公众号API（如有服务号）