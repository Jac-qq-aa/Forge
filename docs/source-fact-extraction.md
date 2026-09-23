# 原文结构化事实抽取

## 模块接口

调用方只需要使用一个接口：

```python
extraction = await SourceFactExtractor().extract(source_article)
```

返回值包含：

- `source_text`：规范化选择后的完整原文；
- `chunks`：带标题路径和全局字符偏移的原文块；
- `evidence`：可被 Research Agent 引用的证据；
- `facts`：带逐字引用的结构化原文事实候选；
- `warnings`：模型失败、引用不存在、数字不一致或块数超限等问题。

模块同时接受 `text` 和 `content`，但不会把两个字段拼接。`text` 非空时使用 `text`，否则使用 `content`。

## 数据流

```text
完整原文
  -> 标题层级、句子与字符位置拆解
  -> 最多 80 个 Source Chunk
  -> 每块并发抽取原子事实
  -> quote 精确定位
  -> 数字一致性校验
  -> predicate/value/unit/qualifier 规范化
  -> 完全相同事实合并
  -> 同主体、关系和时间限定下不同值标记 disputed
  -> Research Agent 规划外部核验任务
  -> HITL 审核
```

## 事实状态

- `source_claim`：只能证明原文这样陈述，尚未被外部来源证实；
- `verified`：存在原文以外的直接支持证据；
- `disputed`：来源或值冲突；
- `unsupported`：没有足够证据；
- `expired`：事实曾经成立，但当前已经失效。

`source_claim` 不会静默进入写作。只有用户在事实审核暂停点明确接受后，才会进入大纲和 Generator。

## 引用与位置约束

每条原文事实必须带：

```json
{
  "evidence_id": "E001",
  "chunk_id": "SRC-C001",
  "quote": "原文逐字引用",
  "char_start": 120,
  "char_end": 139
}
```

系统会验证：

1. `quote` 必须存在于对应 Evidence；
2. 全局字符偏移必须能从 Evidence 的起始位置准确还原；
3. claim 和 object 中出现的阿拉伯数字必须存在于 quote；
4. 无法定位或数字不一致的候选不会进入事实清单。

## 时效与冲突

客户数、员工数、营收、业务范围和资质默认为高时效事实。出现年份时写入 `as_of_date` 和 `qualifiers.year`。

冲突键为：

```text
subject + predicate + unit + qualifiers
```

同一冲突键对应多个 `normalized_value` 时，相关事实全部标为 `disputed`，并分配同一个 `conflict_group`，等待人工处理。

## 模型调用边界

- 单块目标长度默认为 700 字；
- 最大 80 块；
- 默认最多 4 个并发抽取调用；
- 单块失败只记录 warning，不会伪造降级事实；
- 原文抽取失败后不会再把前五句话自动标成 `verified`。
