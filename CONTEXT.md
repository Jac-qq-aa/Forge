# Forge 内容事实治理

该上下文定义文档陈述、文章内采用事实与集团正式事实之间的业务边界，防止写作审核结果被误当作集团标准。

## Language

**Raw Claim（原始声明）**:
来源文档明确表达、并保留原文证据位置的陈述；它证明“文档这样说”，不证明内容真实。
_Avoid_: 原始事实、已验证事实

**Session-Accepted Claim（会话采用声明）**:
用户明确允许在当前文章任务中使用的 Raw Claim，只在该会话范围内有效。
_Avoid_: 已发布事实、集团事实

**Canonical Fact（规范事实）**:
经过目录归一化和治理审核、具有适用时间与来源证据的集团事实。
_Avoid_: 文章事实、抽取结果

**Semantic Catalog（语义目录）**:
集团维护的实体、谓词、指标、单位和法律术语标准及其别名集合；目录定义标准含义，不证明具体事实值。
_Avoid_: 事实库、Prompt 术语表

**Resolution Decision（归一化决定）**:
将 Raw Claim 映射到特定目录版本中标准概念的可审计判断，允许结果为 uncertain。
_Avoid_: 模型答案、候选召回结果

**Published Fact（已发布事实）**:
状态为 published、允许用于集团口径问答的 Canonical Fact。
_Avoid_: accepted fact、verified claim

**Article Scope（文章范围）**:
只读取指定会话的 Raw Claim 和 Session-Accepted Claim 的查询范围。

**Group Scope（集团范围）**:
只读取租户集团内 Published Fact 的查询范围。

**Combined Scope（组合范围）**:
同时读取文章范围和集团范围，并保留各自状态、来源及冲突的查询范围。

**Conflict（事实冲突）**:
在实体、标准谓词或指标、维度和适用时间可比较时出现的不兼容值；不同期间或口径的值不自动构成冲突。
_Avoid_: 任意不同值、历史更新
