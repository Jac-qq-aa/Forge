"""Editor node - LLM-based content rewriting with knowledge base enhancement."""

import logging
from forge.graph.state import GraphState
from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


async def editor_node(state: GraphState) -> dict:
    """Rewrite content using Qwen LLM with knowledge base context.

    The editor retrieves relevant information from the Ruibo Group knowledge base
    and uses it to rewrite content in a way that relates to the company.

    Output format depends on target_platform:
    - zhihu_article: Pure text article (知乎回答/文章格式)
    - xhs_video: Short video script (短视频脚本格式)
    """
    raw_content = state.get("raw_content", {})
    reflection_feedback = state.get("reflection_feedback", "")
    rewritten_draft = state.get("rewritten_draft", "")
    revision_count = state.get("revision_count", 0)
    target_platform = state.get("target_platform", "xhs_video")

    logger.info(f"[Editor] Starting rewrite, revision count: {revision_count}")
    logger.info(f"[Editor] Target platform: {target_platform}")
    logger.info(f"[Editor] Has reflection_feedback: {bool(reflection_feedback)}")

    # Get knowledge base context
    knowledge_context = ""
    try:
        from forge.knowledge import get_knowledge_base
        kb = get_knowledge_base()

        # Extract topic from raw content for knowledge search
        title = raw_content.get("title", "")
        text = raw_content.get("text", "")

        # Search knowledge base using title and first part of content
        search_query = f"{title} {text[:200]}"
        knowledge_context = kb.get_context_for_topic(search_query, max_docs=3)

        if knowledge_context:
            logger.info(f"[Editor] Retrieved knowledge context ({len(knowledge_context)} chars)")
        else:
            logger.info("[Editor] No relevant knowledge found, using default rewrite")
    except Exception as e:
        logger.warning(f"[Editor] Knowledge base search failed: {e}")

    llm = LLMClient()

    original_text = raw_content.get("text", "")
    title = raw_content.get("title", "")

    # 根据原文长度确定改写篇幅
    original_len = len(original_text)
    if original_len > 3000:
        target_len = "1500-2000字，分多个段落深入阐述"
    elif original_len > 1500:
        target_len = "800-1200字"
    elif original_len > 500:
        target_len = "600-900字"
    else:
        target_len = "500-800字"

    logger.info(f"[Editor] Original length: {original_len}, target: {target_len}")

    # Determine output format based on target platform
    is_video_platform = target_platform in ["xhs_video", "zhihu_video"]
    is_article_platform = target_platform in ["zhihu_article", "wechat_article"]

    has_feedback = bool(reflection_feedback)

    # Build prompt based on platform type
    if knowledge_context:
        # With knowledge base context - rewrite to relate to Ruibo Group
        if is_article_platform:
            # 文章格式：纯文本、观点论证、段落清晰
            platform_style = "知乎回答" if target_platform == "zhihu_article" else "微信公众号文章"

            if has_feedback:
                prompt = f"""请根据以下反馈意见优化{platform_style}内容：

反馈意见：
{reflection_feedback}

当前改写草稿：
{rewritten_draft}

原始内容：
标题：{title}
内容：{original_text}

锐博集团参考资料（仅供参考，可适当引用）：
{knowledge_context}

要求：
1. 解决反馈中指出的问题
2. 必须保留原文的核心观点和论证逻辑
3. 可适当引用锐博集团相关案例或数据作为补充，引用时必须标注来源（如"据锐博集团官网介绍..."）
4. 保持{platform_style}的专业性和深度
5. 结构清晰，段落分明
6. ⚠️ 严禁编造具体信息（课程名、活动时间、数字等），没有确切来源的信息用模糊表述"""
                system_prompt = f"""你是一位{platform_style}创作者，擅长撰写专业、有深度的人力资源领域内容。
你正在帮助锐博集团优化{platform_style}。
重要：你的首要任务是保留原文的核心观点和内容，只是适当补充锐博集团相关信息。
不要把原文改写成锐博集团的广告，原文的观点和论证才是主体。
⚠️ 信息真实性红线：绝不编造具体信息（课程名、时间、数字等），引用知识库信息必须标注来源。"""
            else:
                wechat_extra = ""
                if target_platform == "wechat_article":
                    wechat_extra = """
8. 微信公众号风格：标题吸引人、开头有"钩子"、段落简短、适合手机阅读
9. 可在文末添加引导关注或互动的语句"""

                # 风格参考模板（模拟真实职场博主风格）
                style_reference = """
【风格参考示例】

"说实话，这事挺有意思的。表面上看是技术问题，实际上...

我不确定这算不算一种'行业通病'，但至少在我这些年接触的项目里，类似的情况屡见不鲜。

有个朋友前两天跟我吐槽，说他们公司花大价钱请了咨询团队，结果给的方案听起来很完美——流程优化、职责明确、考核量化。执行起来呢？三个月不到就没人提了。

为什么？因为没人真正回答过那个最简单的问题：'到底谁负责？'

别急着反驳说'肯定是XX部门啊'。你仔细想想，当问题真的出现时，第一个被问责的是谁？是流程设计者吗？是决策者吗？还是...那个刚好在场的倒霉蛋？

说句不好听的，很多时候我们讨论的'管理优化'，本质上都是在找更合理的方式推锅。

有意思的是，每次遇到这种情况，总有人会说'这届员工不行'。"

【风格要点】
- 口语化开头：用"说实话"、"有意思的是"、"说句不好听的"等引入
- 真实感：用具体场景、朋友吐槽等细节增加可信度
- 打破三段论：没有总结性结尾，用疑问或吐槽收尾
- 句式长短交错：短句制造节奏，长句展开论述
- 适度自嘲/毒舌：真实但不刻薄
"""

                prompt = f"""请将以下内容改写为{platform_style}，采用真实职场博主风格。

## 人设定义
你是一个有10年职场经验的互联网打工人，说话一针见血、甚至有点毒舌、经常自嘲。用略带疲惫但真实的口吻来写。

## 原始内容
标题：{title}
内容：{original_text}

## 锐博集团参考资料（仅供参考，自然融入即可）
{knowledge_context}

## 风格参考
{style_reference}

## 改写要求（严格遵守）
1. 【人设】用"说实话"、"有意思的是"、"说句不好听的"等口语化开头
2. 【真实感】加入具体场景、朋友吐槽等细节增加可信度
3. 【结构】打破三段论！不要写"首先其次最后"、不要写总结性结尾
4. 【结尾】用一个未解的疑问或者一句情绪化的吐槽来结尾
5. 【句式】长短句交错，短句制造节奏，少用排比句
6. 【核心观点】必须保留原文的核心观点和论证
7. 【锐博融入】自然提及，不要生硬广告，占比不超过10%
8. 【信息真实性】⚠️ 严禁编造任何具体信息！
   - 只能使用原文中已有的具体事实、数据、案例
   - 知识库中的信息如果引用，必须标注来源（如"据锐博集团官网介绍..."、"根据锐博集团培训资料显示..."）
   - 禁止编造课程名称、活动时间、具体数字等细节
   - 如果没有具体信息可用，用模糊表述替代（如"近期"、"某培训项目"而非"3月15日开课"、"《FPS玩家职业化路径》公开课"）
9. 【格式禁忌】不要用✔✅等符号，不要列"1.2.3.4"或"一、二、三"这样的点，用段落叙述代替
10. 【篇幅】控制在{target_len}{wechat_extra}

直接输出改写后的内容，不要解释你的改写策略。"""

                system_prompt = """你是一位资深互联网职场人，10年从业经验，经历过从大厂到创业公司的各种坑。
你的写作风格：
- 开头习惯用口语化引入："说实话"、"有意思的是"
- 喜欢用具体场景和朋友吐槽增加真实感
- 拒绝三段论结构，从不写"首先其次最后"
- 结尾从不升华，要么是疑问要么是吐槽
- 句式长短交错，节奏感强，少用排比句
- 适度毒舌但不过分刻薄
- 自嘲是常态
- 不用✔✅等符号，不列"1.2.3.4"点，用段落叙述

重要原则：
1. 原文核心观点必须保留，只是换一种表达方式
2. 锐博集团信息自然融入，不要变成广告
3. 真实感最重要
4. ⚠️ 信息真实性红线：绝不编造具体信息（课程名、时间、数字等），引用知识库信息必须标注来源"""

        elif is_video_platform:
            # 短视频脚本格式：带画面描述、适合配音
            if has_feedback:
                prompt = f"""请根据以下反馈意见优化短视频脚本：

反馈意见：
{reflection_feedback}

当前改写草稿：
{rewritten_draft}

原始内容：
标题：{title}
内容：{original_text}

锐博集团参考资料（仅供参考）：
{knowledge_context}

要求：
1. 解决反馈中指出的问题
2. 必须保留原文的核心观点
3. 可适当融入锐博集团相关信息，引用时必须标注来源
4. 保持短视频脚本的专业性和吸引力
5. ⚠️ 严禁编造具体信息（课程名、活动时间、数字等），没有确切来源的信息用模糊表述"""
                system_prompt = """你是一位短视频脚本创作专家，擅长企业品牌内容营销。
重要：保留原文核心观点，锐博集团信息只是补充。
⚠️ 信息真实性红线：绝不编造具体信息，引用必须标注来源。"""
            else:
                prompt = f"""请将以下内容改写为短视频脚本，保留核心观点：

原始内容：
标题：{title}
内容：{original_text}

锐博集团参考资料（仅供参考）：
{knowledge_context}

改写要求：
1. 【重要】必须保留原文的核心观点和价值
2. 使用新的表达方式改写，确保原创性
3. 可适当引用锐博集团信息作为补充，但引用时必须标注来源（如"据锐博集团资料显示..."）
4. 增加吸引人的开头，适合短视频传播
5. 添加适当的画面描述（用括号标注，如"（画面：xxx）")
6. 控制篇幅在300-500字
7. 在结尾可加入简单的引导语
8. ⚠️ 严禁编造具体信息（课程名、活动时间、具体数字等），没有确切来源的信息用模糊表述"""
                system_prompt = """你是一位短视频脚本创作专家。
重要原则：
1. 原文核心观点是主体，必须保留
2. 锐博集团信息只是补充，不要喧宾夺主
3. 改写是为了原创性，不是重新创作
4. ⚠️ 信息真实性红线：绝不编造具体信息，引用必须标注来源"""

    else:
        # Fallback: without knowledge base context
        if is_article_platform:
            if has_feedback:
                prompt = f"""请根据以下反馈意见优化知乎回答内容：

反馈意见：
{reflection_feedback}

当前改写草稿：
{rewritten_draft}

原始内容：
标题：{title}
内容：{original_text}

请改进内容，解决反馈中指出的问题，保持知乎回答的专业风格。
重要：保留原文的核心观点和论证逻辑。"""
                system_prompt = "你是一位知乎高赞回答创作者，擅长根据反馈改进内容。保留原文核心观点。"
            else:
                prompt = f"""请将以下知乎内容进行改写，保留核心观点，确保原创性：

原始内容：
标题：{title}
内容：{original_text}

改写要求：
1. 【重要】必须保留原文的核心观点、论证逻辑和主要内容
2. 使用新的表达方式改写，确保原创性，但不要改变原文的主旨
3. 开头要有吸引读者的"钩子"
4. 段落分明，逻辑清晰
5. 控制篇幅在{target_len}
6. 直接输出纯文本内容"""
                system_prompt = "你是一位知乎高赞回答创作者。重要：改写是为了原创性，必须保留原文核心观点，不要重新创作。"

        elif is_video_platform:
            if has_feedback:
                prompt = f"""请根据以下反馈意见优化短视频脚本：

反馈意见：
{reflection_feedback}

当前改写草稿：
{rewritten_draft}

原始内容：
{original_text}

请改进脚本，解决反馈中指出的问题。保留原文核心观点。"""
                system_prompt = "你是一位短视频脚本创作专家，擅长根据反馈改进内容。保留原文核心观点。"
            else:
                prompt = f"""请将以下内容改写为短视频脚本，保留核心观点：

原始内容：
标题：{title}
内容：{original_text}

改写要求：
1. 【重要】必须保留原文的核心信息价值
2. 使用新的表达方式改写，确保原创性
3. 增加吸引人的开头
4. 添加适当的画面描述
5. 控制篇幅在300-500字"""
                system_prompt = "你是一位短视频脚本创作专家。重要：改写是为了原创性，必须保留原文核心观点。"

    rewritten_draft_result = await llm.chat_with_retry(prompt, system_prompt)

    new_revision_count = revision_count + 1
    logger.info(f"[Editor] Generated draft ({len(rewritten_draft_result)} chars)")
    logger.info(f"[Editor] New revision count: {new_revision_count}")
    logger.info("[Editor] Node completed")

    return {
        "rewritten_draft": rewritten_draft_result,
        "revision_count": new_revision_count,
    }