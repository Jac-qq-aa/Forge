# 中文文章质量评估开源方案调研

> 核验日期：2026-08-22  
> 范围：中文长文章的连贯性、事实性、完整性、平台风格和“AI 感”评估；仅引用项目官方仓库、官方文档或原论文。  
> 说明：GitHub 活跃度以核验日仓库页面及 GitHub API 的最近推送时间为准，它只能反映维护迹象，不能代表生产成熟度。

## 结论

Forge 最合适的落地组合是：

1. **DeepEval + G-Eval 作为文章离线评测主框架**：用中文评分细则分别评估事实忠实度、内容完整性、跨段连贯性、平台风格一致性和自然表达，并输出分项分数及理由。DeepEval 已直接提供 G-Eval、自定义 rubric、RAG Faithfulness、Summarization、CI/CD 测试和自定义 Judge 模型能力，适合 Forge 当前 Python/LangGraph 技术栈。[DeepEval 官方仓库](https://github.com/confident-ai/deepeval)；[G-Eval 官方文档](https://deepeval.com/docs/metrics-llm-evals)
2. **Ragas 作为 RAG 与事实约束补充**：继续评估 Context Precision/Recall、Response Relevancy、Faithfulness、Factual Correctness；不要让它单独承担长文结构和文风评估。Ragas 同时提供通用 criteria/rubric 评分，但核心优势仍在 RAG。[Ragas 官方仓库](https://github.com/vibrantlabsai/ragas)；[官方指标目录](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/general_purpose/)
3. **“AI 感”定义为可解释的风格问题，而非 AI 来源概率**：评分项应拆成套话密度、句式重复、段落节奏、空泛结论、过度列表化、衔接生硬、平台语体不匹配等。AI 文本检测器解决的是“来源分类”，不能证明文章是否优质，也容易受模型、领域、改写和时间漂移影响。
4. **不建议将 DetectGPT、RAID 或中文 AI 检测器的概率并入文章总质量分**。如业务确实要展示“疑似 AI 生成”信号，可以将中文 `AIGC_detector_zhv3` 作为独立、低置信度提示，并用公司自己的官网/公众号/知乎历史文章重新校准阈值。[中文检测器官方仓库](https://github.com/YuchuanTian/AIGC_text_detector)

## 生成质量评估框架

| 项目 | 能力与适用性 | 中文支持 | 活跃度与许可证 | 接入方式 | Forge 建议 |
|---|---|---|---|---|---|
| [DeepEval](https://github.com/confident-ai/deepeval) | G-Eval 可按任意自然语言 criteria/evaluation steps 打分；内置 Faithfulness、Answer Relevancy、Summarization 等指标，可返回 `score` 和 `reason`，也可在 CI 中做回归门禁。[官方 RAG 指南](https://github.com/confident-ai/deepeval/blob/main/docs/guides/guides-rag-evaluation.mdx) | 没有官方“中文文章质量模型”，但可使用中文 rubric、中文样本和支持中文的自定义 Judge；因此是“框架支持、效果需中文人工校准”。官方文档允许覆盖模板并接入任意 `DeepEvalBaseLLM`。[自定义模板说明](https://deepeval.com/docs/metrics-llm-evals#customize-your-template) | 核验时约 17.8k stars，2026-08-21 仍有推送；Apache-2.0。[许可证](https://github.com/confident-ai/deepeval/blob/main/LICENSE) | `pip install deepeval`；将文章、事实清单、已确认大纲、平台要求分别映射到 `actual_output`、`retrieval_context`、`expected_output/context`；Qwen 可通过自定义 Judge 适配。 | **首选**。最省力地实现多维 LLM-as-a-Judge、解释、阈值和回归评测。 |
| [Ragas](https://github.com/vibrantlabsai/ragas) | 强项是 Context Precision/Recall、Faithfulness、Response Relevancy、Factual Correctness；也支持 AspectCritic、离散区间和 rubric 评分。[官方指标目录](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/general_purpose/) | 官方提供非英语测试集生成和 prompt 语言适配思路，但示例主要是西班牙语，不等于已验证中文质量。[非英语测试集官方教程](https://docs.ragas.io/en/stable/howtos/customizations/testgenerator/_language_adaptation/) | 核验时约 15.4k stars，最近可见推送为 2026-02-24；Apache-2.0。[许可证](https://github.com/vibrantlabsai/ragas/blob/main/LICENSE) | `pip install ragas`；把检索切片、事实清单和文章声明送入 RAG/Factual 指标；通用文风可用 `DiscreteMetric`。 | **作为 DeepEval 的检索与事实性补充**，不建议独立承担长文综合评分。 |
| [TruLens](https://github.com/truera/trulens) | 提供 Groundedness、Answer Relevance、Context Relevance（RAG Triad）、自定义反馈函数、记录和可视化；适合将评分与 trace 绑定。[官方指标文档](https://www.trulens.org/getting_started/core_concepts/feedback_functions/) | LLM Judge 路径可换成支持中文的模型和中文 criteria，但没有官方中文长文基准；仍需人工校准。 | 核验时约 3.5k stars，2026-08-21 仍有推送；MIT。[官方仓库与许可证](https://github.com/truera/trulens) | `pip install trulens`，另装 LLM provider 和 `trulens-apps-langchain`；可接 LangGraph 并记录反馈。 | 如果 Forge 下一步优先建设**线上可观测性和评估看板**，再选 TruLens；只做离线文章打分时 DeepEval 更直接。 |
| [OpenAI Evals](https://github.com/openai/evals) | 通用 LLM/系统评测框架和 benchmark registry，可用 JSON 数据、YAML 配置创建 basic 或 model-graded eval。[官方 build-eval](https://github.com/openai/evals/blob/main/docs/build-eval.md) | 可自建中文数据集和评分 prompt，但仓库没有开箱即用的中文文章连贯性/“AI 感”评测。 | 核验时约 19.2k stars，最近可见推送为 2026-04-14；仓库说明评测逻辑与数据采用 MIT。[官方说明](https://github.com/openai/evals#disclaimer) | 克隆仓库，按 JSONL/YAML 注册自定义 eval；更偏评测基础设施和基准注册。 | 可借鉴数据集/版本组织方式，但 Forge 当前没有必要为文章评分优先引入它。 |
| [G-Eval 原始实现](https://github.com/nlpyang/geval) | 原论文以 CoT 和 form-filling 评估摘要的一致性、连贯性、流畅性、相关性，以及对话质量；论文报告其摘要任务与人工评分的 Spearman 相关系数为 0.514，同时提醒 Judge 会偏好 LLM 生成文本。[原论文](https://arxiv.org/abs/2303.16634) | 原始实验和 prompt 以英文为主；方法可配中文 Judge，但没有原论文级中文验证。 | 约 431 stars，最近推送为 2024-02-04；MIT。[许可证](https://github.com/nlpyang/geval/blob/main/LICENSE) | 直接运行论文代码，或使用 DeepEval 已封装的 G-Eval。 | **使用 DeepEval 封装，不直接集成论文仓库**。 |
| [UniEval](https://github.com/maszhongming/UniEval) | 预训练 evaluator 可给摘要的 coherence、consistency、fluency、relevance 和 overall 分，也有 factual consistency evaluator；默认 overall 是维度平均值。[官方示例](https://github.com/maszhongming/UniEval#get-multi-dimenisonal-scores) | 官方预训练器和示例面向英文 NLG，没有官方中文 checkpoint 或中文长文验证。 | 约 217 stars，仅 9 次提交，最近推送为 2024-02-10；MIT。[仓库许可证](https://github.com/maszhongming/UniEval) | 克隆仓库并下载任务预训练模型，本地推理。 | 指标设计值得参考，但中文适配和维护成本高，**不建议用于 Forge 主链路**。 |

### 为什么首选 DeepEval/G-Eval

- 可将“连贯性”和“AI 感”写成业务可解释的中文 rubric，而不是依赖英文摘要模型。
- 可以分别输入文章、事实清单、检索证据、已确认大纲和平台规范，覆盖 Forge 已存在的数据结构。
- 能返回评分理由和具体问题，方便后续传给 Revise；也能把阈值放进自动回归测试。
- DeepEval 官方明确说明 G-Eval 非确定性；显式锁定 `evaluation_steps` 和 rubric 比每次只给一句 criteria 更稳定。[官方稳定性建议](https://deepeval.com/docs/metrics-llm-evals#faqs)

## AI 文本检测项目：与“文章质量”分开使用

| 项目 | 实际解决的问题 | 中文支持 | 活跃度/许可证与限制 | Forge 建议 |
|---|---|---|---|---|
| [AIGC_text_detector](https://github.com/YuchuanTian/AIGC_text_detector) | 判断文本是否由 AI 生成，不评价事实、结构或文风质量。官方提供中文 `zhv2`，并在 2025-06/07 发布面向 DeepSeek-V3、GPT-4 和 reasoning models 的 `zh-v3`/短文本版本。[官方发布记录](https://github.com/YuchuanTian/AIGC_text_detector#news) | **明确支持中文**；官方称覆盖新闻、诗歌、作文等文本，但仍需在 Forge 自有文章上复测。 | 约 461 stars；最新明确发布记录为 2025-07-24；Apache-2.0。[许可证](https://github.com/YuchuanTian/AIGC_text_detector/blob/main/LICENSE) | 仅可作为独立实验信号。若使用，应按平台和长度分别验证 Precision/Recall/FPR，不能并入质量总分。 |
| [HC3](https://github.com/Hello-SimpleAI/chatgpt-comparison-detection) | 人类与 ChatGPT 文本对比语料及分类器，包含 QA、单文本和语言学特征检测。 | 官方提供 HC3-Chinese 和中文 RoBERTa 检测器。[官方模型说明](https://github.com/Hello-SimpleAI/chatgpt-comparison-detection#chatgpt-detectors--内容检测器) | 约 1.4k stars；语料各来源许可证不一，部分来源许可证仍标为 Unknown，商用前必须逐项核验。[官方数据版权表](https://github.com/Hello-SimpleAI/chatgpt-comparison-detection#dataset-copyright) | 适合研究和构建内部对照集，不建议原样作为生产评分器。训练数据主要代表早期 ChatGPT 分布。 |
| [DetectGPT](https://github.com/eric-mitchell/detect-gpt) | 原论文根据模型概率函数的负曲率做零样本来源检测，不是自然度或质量评分。[原论文](https://arxiv.org/abs/2301.11305) | 官方代码与实验没有中文专项验证，且需要能计算目标/代理模型概率。 | 约 476 stars，最近推送为 2023-03-04；MIT。[许可证](https://github.com/eric-mitchell/detect-gpt/blob/main/LICENSE) | 对当前 Qwen/中文多平台文章不够稳健，不建议生产接入。 |
| [RAID](https://github.com/liamdugan/raid) | 面向 AI 文本检测器的 benchmark，不是直接给文章打质量分的 detector；覆盖 11 个模型、11 个领域、4 种采样和 12 种对抗攻击。[官方仓库](https://github.com/liamdugan/raid) | 官方数据列出的语言以英语为主，extra 含 Czech/German，没有中文。[ACL 2024 原论文](https://aclanthology.org/2024.acl-long.674/) | 约 207 stars，2026-08-20 仍有推送；MIT。安装为 `pip install raid-bench`。 | 可用于理解检测器的跨域/对抗失效，不适合作为 Forge 中文文章的直接分数。 |
| [M4](https://github.com/mbzuai-nlp/M4) | 多生成器、多领域、多语言的 AI 文本检测 benchmark；原论文结论之一是检测器对未见领域或未见模型泛化较差。[EACL 2024 原论文](https://aclanthology.org/2024.eacl-long.83/) | 多语言研究，但官方仓库不是面向 Forge 中文长文的即插即用评分 API。 | 约 52 stars；仓库页面没有清晰开源许可证标识，商用前需向作者确认。 | 主要用来支撑“AI 检测不能代替质量评估”的风险判断。 |

## 可安装 Skill

Skill 本质上是评测方法和工作流说明，并不等于可部署的评分引擎。

| Skill | 核验结果 | 安装方式 | 建议 |
|---|---|---|---|
| [github/awesome-copilot@agentic-eval](https://www.skills.sh/github/awesome-copilot/agentic-eval) | GitHub 官方仓库；核验时约 10.1k 安装、仓库约 38.1k stars。提供 evaluator-optimizer、rubric 加权、结构化 JSON、迭代上限和收敛判断。 | `npx skills add https://github.com/github/awesome-copilot --skill agentic-eval` | **推荐作为设计参考**，与 Forge 的 Critic–Revise 最契合；它不自带中文评分模型。 |
| [wshobson/agents@llm-evaluation](https://www.skills.sh/wshobson/agents/llm-evaluation) | 核验时约 10.3k 安装、仓库约 39k stars，安全审计页面均显示通过；内容覆盖自动指标、人工评估和 A/B 测试。 | `npx skills add https://github.com/wshobson/agents --skill llm-evaluation` | 可作为建设完整 eval 流程的通用参考，但不是文章专用 evaluator。 |
| [humanizerai/agent-skills@detect-ai](https://www.skills.sh/humanizerai/agent-skills/detect-ai) | 调用 HumanizerAI 外部 API；核验时约 827 安装、仓库仅约 40 stars。输出属于黑盒来源检测，且页面未给出中文长文校准证据。 | `npx skills add https://github.com/humanizerai/agent-skills --skill detect-ai` | **不推荐用于 Forge 主评估**，尤其不应把黑盒 AI 概率当作质量分。 |

## Forge 建议评分卡

建议保留分项分数，同时给出综合分；事实性应设硬门槛，避免“文风很好”抵消事实错误。

| 维度 | 权重 | Judge 输入 | 评分要点 |
|---|---:|---|---|
| 事实忠实度 | 35% | 文章 + 已确认事实清单 + 检索证据 | 每项声明是否有证据、是否矛盾、数字/时间/主体是否准确；低于阈值直接不通过 |
| 内容完整性 | 20% | 文章 + 已确认大纲 | 是否覆盖必写章节和关键论点，是否遗漏用户确认内容 |
| 结构与连贯性 | 20% | 完整文章 | 主题推进、段落关系、指代、因果、重复、跨章节一致性 |
| 平台风格一致性 | 15% | 文章 + 官网/公众号/知乎规范 + 优质历史样本 | 受众、语气、篇幅、标题和表达方式是否匹配平台 |
| 自然表达（“AI 感”） | 10% | 完整文章 + 人工定义的反例/正例 | 套话、机械排比、段落同构、过度总结、空泛连接词、缺少具体细节；只评价风格，不判断作者身份 |

综合分可按权重计算，但应额外输出：`hard_fail`、各维度 `score/reason/evidence`、问题段落、建议工具（`fix_fact`、`rewrite_section`、`adjust_tone`）。

## 评测可信度要求

1. Judge 温度设为 0 或尽可能低，锁定模型版本、中文 `evaluation_steps`、rubric 和评测数据集版本。
2. 新旧版本采用匿名成对比较，并交换 A/B 顺序，降低位置偏差；G-Eval 原论文也提示 LLM Judge 可能偏好 LLM 生成文本。[G-Eval 原论文](https://arxiv.org/abs/2303.16634)
3. 先选 50–100 篇覆盖官网、公众号、知乎的文章，由至少两名业务人员独立标注；计算 Judge 与人工的一致率/相关性，再决定上线阈值。
4. 对事实性同时保留规则核验和证据引用。LLM Judge 适合发现疑点，不应作为事实真相来源。
5. 每次 Prompt、模型、检索参数或工作流变化都跑固定回归集，记录均分、分位数、失败样本和调用成本；不能只比较综合均值。

## 最小落地顺序

1. 先接入 DeepEval 的 5 个中文 G-Eval 指标，复用 Forge 已有事实清单、大纲、平台和文章状态。
2. 将现有检索评测保留，并用 Ragas 增加 Faithfulness/Factual Correctness；检索指标与文章质量指标分别展示。
3. 建立人工校准集，完成 Judge 相关性、位置偏差和重复运行方差测试。
4. 把分项问题传给 Revise，复评时同时检查“问题是否解决”和“是否产生修改回归”。
5. 只有存在明确合规/来源识别需求时，才单独试验中文 AI 检测器；默认不进入综合质量分。
