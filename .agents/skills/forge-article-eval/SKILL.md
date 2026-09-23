---
name: forge-article-eval
description: Evaluate Forge final articles with DeepEval for structure, writing quality, and naturalness. Use when implementing, running, testing, or debugging Forge post-generation article scoring; never use it for factual correctness or fact-list validation.
---

# Forge 最终文章表达质量评估

## 使用流程

1. 先完整阅读 [references/scoring-contract.md](references/scoring-contract.md)。
2. 只把 `final_article` 与 `target_platform` 交给评估器。
3. 使用 `forge.evaluation.article_expression.ArticleExpressionEvaluator` 执行三项 G-Eval：
   - 整体结构与连贯性：40%
   - 文笔与可读性：35%
   - 自然度与低 AI 模板感：25%
4. 最终文章持久化成功后，发送 `article_finalized` 队列事件；由评估 Worker 异步消费、评分和持久化。
5. 通过文章表达质量查询接口读取结果。评分失败不得阻断定稿，也不得自动修改文章。

## 强制边界

- 事实清单、引用、来源、数字、grounding 与事实正确性只由 Critic 检查。
- 不得把事实清单、RAG 上下文、原文或 Critic 报告传给 DeepEval。
- 自然度高表示表达自然；`ai_style_risk = 1 - naturalness_score`。
- 不接入 Confident AI，不增加 DeepEval tracing；保留项目已有 LangSmith 链路。
- 评估模型复用 Forge 的 `JUDGE_MODEL`、`JUDGE_API_KEY` 和 `JUDGE_API_URL`。

## 验证

运行文章表达质量相关单元测试，并确认：

- 三项指标可独立返回分数与理由；
- 加权总分计算正确；
- 同一文章定稿事件可幂等处理；
- Redis 或评估模型故障不影响定稿主流程；
- 评估输入中不存在事实清单或来源上下文。
