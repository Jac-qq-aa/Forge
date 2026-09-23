# Forge 文章表达质量评分契约

## 职责分工

| 检查内容 | 负责组件 |
| --- | --- |
| 事实清单一致性、来源依据、数字和引用正确性 | Critic |
| 文章整体结构、段落衔接和中心主线 | DeepEval / structure |
| 文笔、清晰度、节奏、可读性 | DeepEval / writing |
| 模板化表达、机械过渡、空泛总结、AI 腔 | DeepEval / naturalness |

DeepEval 不判断文章是否由 AI 创作，只评价文本是否存在明显模板化和机械化表达。

## 输入

```json
{
  "final_article": "最终定稿全文",
  "target_platform": "zhihu_article"
}
```

禁止增加事实清单、原始资料、RAG 上下文、引用来源或 Critic 报告。

## 输出

分数统一使用 `0.0-1.0`：

```json
{
  "overall_score": 0.82,
  "structure_score": 0.86,
  "writing_score": 0.80,
  "naturalness_score": 0.78,
  "ai_style_risk": 0.22,
  "metric_reasons": {
    "structure": "...",
    "writing": "...",
    "naturalness": "..."
  },
  "issues": [
    {"dimension": "naturalness", "reason": "..."}
  ],
  "model": "qwen3.7-max",
  "status": "completed"
}
```

总分公式：

```text
overall = structure * 0.40 + writing * 0.35 + naturalness * 0.25
```

## 评分标准

### Structure

- 是否存在清晰中心主线；
- 开头、主体、结尾是否承担明确作用；
- 章节和段落顺序是否合理；
- 过渡是否自然；
- 是否存在重复、跳跃或孤立段落。

### Writing

- 表达是否清晰、准确、具体；
- 句式和节奏是否有变化；
- 是否存在冗余、拗口、病句或堆砌；
- 阅读负担是否与目标平台匹配；
- 文字是否具有必要的表现力。

### Naturalness

- 是否使用套路化开头和结尾；
- 是否机械使用“首先、其次、最后”等枚举；
- 段落长度和句式是否过度整齐；
- 是否滥用通用过渡词、空泛总结和口号；
- 是否缺少具体表达和鲜明语气。

自然度高代表 AI 模板感低。不得把“自然度”解释为 AI 来源检测概率。

## 运行时链路

```text
最终文章写入 PostgreSQL
  -> Redis 队列 article_finalized 事件
  -> forge.evaluation.worker
  -> ArticleExpressionEvaluator
  -> article_expression_evaluations 表
  -> 查询接口
```

队列发送失败或评分失败均不得让定稿请求失败。相同 `session_id + article_hash` 的已完成评分不得重复调用模型。
