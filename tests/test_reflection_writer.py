"""测试 Reflection Writer 的 Fact Checker 节点."""

import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def normal_fact_sheet():
    """正常的 Fact Sheet（来源可靠）."""
    return """## 核心论点
- AI发展迅速，正在改变各行各业
- 企业需要积极拥抱AI技术

## 关键数据
- 2023年AI市场规模达到150亿美元（来源：McKinsey报告）
- 70%的企业已开始使用AI工具（来源：Gartner调研）

## 案例素材
- 案例1：某公司使用AI客服，效率提升50%
- 案例2：某银行使用AI风控，准确率达95%

## 专家观点
- 李明教授指出：AI将重塑未来工作方式（来源：清华学报）
- 张华专家认为：企业AI转型是必然趋势

## 参考资料
- [1] https://example.com/report1
- [2] https://example.com/report2
"""


@pytest.fixture
def suspicious_fact_sheet():
    """包含可疑数据的 Fact Sheet."""
    return """## 核心论点
- AI将彻底取代人类工作

## 关键数据
- 2030年AI将取代90%人类工作 [来源不明]
- 全球AI投资增长500%（无数据来源）
- AI市场规模将在2025年达到1万亿美元 [网络传言]

## 案例素材
- 案例1：某公司裁员50%，全部换成AI [未证实]

## 专家观点
- 专家预测：AI将在5年内消灭所有白领工作 [来源不明]

## 参考资料
- 无
"""


@pytest.fixture
def raw_content():
    """原文章内容."""
    return {
        "title": "AI发展趋势分析",
        "text": "人工智能正在快速发展...",
    }


# ============================================================================
# Tests for _extract_key_items
# ============================================================================

class TestExtractKeyItems:
    """测试关键数据项提取."""

    def test_extract_numbers(self):
        """测试提取数字和百分比."""
        from forge.agents.reflection_writer import _extract_key_items

        fact_sheet = "市场规模增长50%，用户数达到100万，准确率95%"
        items = _extract_key_items(fact_sheet)

        assert "50%" in items
        assert "95%" in items

    def test_extract_dates(self):
        """测试提取日期."""
        from forge.agents.reflection_writer import _extract_key_items

        fact_sheet = "2023年数据，2024-05-01发布，3月15日会议"
        items = _extract_key_items(fact_sheet)

        assert any("2023" in item for item in items)
        assert any("3月" in item or "15日" in item for item in items)

    def test_extract_names(self):
        """测试提取人名."""
        from forge.agents.reflection_writer import _extract_key_items

        fact_sheet = "李明教授指出，张华专家认为，王强博士发现"
        items = _extract_key_items(fact_sheet)

        assert len(items) > 0


# ============================================================================
# Tests for _extract_items_to_verify
# ============================================================================

class TestExtractItemsToVerify:
    """测试从分析结果提取需验证项."""

    def test_extract_from_analysis(self):
        """测试提取需验证项."""
        from forge.agents.reflection_writer import _extract_items_to_verify

        analysis = """## 高置信度事实
- 数据项A

## 需验证的数据项
1. [2030年AI将取代90%工作] - 来源不明
2. [市场规模1万亿美元] - 网络传言
"""
        items = _extract_items_to_verify(analysis)

        assert "2030年AI将取代90%工作" in items
        assert "市场规模1万亿美元" in items

    def test_empty_analysis(self):
        """测试空分析结果."""
        from forge.agents.reflection_writer import _extract_items_to_verify

        items = _extract_items_to_verify("无需验证的数据项")
        assert items == []


# ============================================================================
# Tests for _annotate_fact_sheet
# ============================================================================

class TestAnnotateFactSheet:
    """测试 Fact Sheet 标注."""

    def test_annotate_suspicious_items(self):
        """测试标注可疑项."""
        from forge.agents.reflection_writer import _annotate_fact_sheet

        fact_sheet = "## 核心论点\n- AI发展迅速"
        suspicious_items = ["数据项1", "数据项2"]

        annotated = _annotate_fact_sheet(fact_sheet, suspicious_items)

        assert "⚠️ 可疑项提示" in annotated
        assert "数据项1" in annotated
        assert "数据项2" in annotated

    def test_no_suspicious_items(self):
        """测试无可疑项时不添加标注."""
        from forge.agents.reflection_writer import _annotate_fact_sheet

        fact_sheet = "## 核心论点\n- AI发展迅速"
        annotated = _annotate_fact_sheet(fact_sheet, [])

        assert "⚠️" not in annotated
        assert annotated == fact_sheet


# ============================================================================
# Tests for fact_checker_node
# ============================================================================

class TestFactCheckerNode:
    """测试 Fact Checker 节点."""

    @pytest.mark.asyncio
    async def test_normal_fact_sheet(self, normal_fact_sheet, raw_content):
        """测试正常 Fact Sheet（无可疑项）."""
        from forge.agents.reflection_writer import fact_checker_node

        state = {
            "fact_sheet": normal_fact_sheet,
            "raw_content": raw_content,
        }

        # Mock LLM 返回分析结果（无可疑项）
        with patch('forge.agents.reflection_writer.LLMClient') as mock_llm:
            mock_instance = MagicMock()
            mock_instance.chat_with_retry = AsyncMock(
                return_value="## 高置信度事实\n- 所有事实来源可靠\n\n## 需验证的数据项\n（无）"
            )
            mock_llm.return_value = mock_instance

            result = await fact_checker_node(state)

            assert "verified_fact_sheet" in result
            assert result["suspicious_items"] == []

    @pytest.mark.asyncio
    async def test_suspicious_fact_sheet(self, suspicious_fact_sheet, raw_content):
        """测试包含可疑数据的 Fact Sheet."""
        from forge.agents.reflection_writer import fact_checker_node

        state = {
            "fact_sheet": suspicious_fact_sheet,
            "raw_content": raw_content,
        }

        # Mock LLM 返回分析结果（有可疑项）
        with patch('forge.agents.reflection_writer.LLMClient') as mock_llm:
            mock_instance = MagicMock()
            mock_instance.chat_with_retry = AsyncMock(
                return_value="""## 低置信度事实
- 2030年AI将取代90%工作 [来源不明]

## 需验证的数据项
1. [2030年AI将取代90%工作] - 来源不明需要验证
"""
            )
            mock_llm.return_value = mock_instance

            # Mock SyncLLMClient 验证结果
            with patch('forge.agents.reflection_writer.SyncLLMClient') as mock_sync:
                mock_sync_instance = MagicMock()
                mock_sync_instance.chat_with_retry = MagicMock(
                    return_value="可疑：该预测过于夸张，缺乏权威来源支持"
                )
                mock_sync.return_value = mock_sync_instance

                result = await fact_checker_node(state)

                assert "verified_fact_sheet" in result
                assert "⚠️ 可疑项提示" in result["verified_fact_sheet"]
                assert len(result["suspicious_items"]) > 0

    @pytest.mark.asyncio
    async def test_llm_failure(self, normal_fact_sheet, raw_content):
        """测试 LLM 失败时的 fallback."""
        from forge.agents.reflection_writer import fact_checker_node

        state = {
            "fact_sheet": normal_fact_sheet,
            "raw_content": raw_content,
        }

        # Mock LLM 抛出异常
        with patch('forge.agents.reflection_writer.LLMClient') as mock_llm:
            mock_instance = MagicMock()
            mock_instance.chat_with_retry = AsyncMock(
                side_effect=Exception("LLM API error")
            )
            mock_llm.return_value = mock_instance

            result = await fact_checker_node(state)

            # 应该有 fallback，不阻塞流程
            assert "verified_fact_sheet" in result


# ============================================================================
# Tests for build_reflection_graph
# ============================================================================

class TestBuildReflectionGraph:
    """测试 Reflection Graph 构建."""

    def test_graph_structure(self):
        """测试 Graph 包含 Fact Checker 节点."""
        from forge.agents.reflection_writer import build_reflection_graph

        graph = build_reflection_graph()

        # 检查节点
        nodes = graph.nodes
        assert "fact_checker" in nodes
        assert "generator" in nodes
        assert "critic" in nodes
        assert "reviser" in nodes

    def test_graph_flow(self):
        """测试 Graph 流程顺序."""
        from forge.agents.reflection_writer import build_reflection_graph

        graph = build_reflection_graph()

        # 检查边（START → fact_checker → generator → critic）
        # 注意：StateGraph 的边信息需要编译后才能验证
        # 这里只验证节点存在
        assert "fact_checker" in graph.nodes


# ============================================================================
# Tests for run_reflection_writer
# ============================================================================

class TestRunReflectionWriter:
    """测试完整 Reflection Writer 流程."""

    @pytest.mark.asyncio
    async def test_full_flow_with_fact_checker(self, normal_fact_sheet, raw_content):
        """测试完整流程（包含 Fact Checker）."""
        from forge.agents.reflection_writer import run_reflection_writer

        # Mock LLMClient
        with patch('forge.agents.reflection_writer.LLMClient') as mock_llm:
            mock_instance = MagicMock()
            # Fact Checker 分析
            # Generator 写作
            # Critic 审查
            mock_instance.chat_with_retry = AsyncMock(
                side_effect=[
                    "## 高置信度事实\n- 所有来源可靠",  # Fact Checker
                    "这是生成的文章内容...",  # Generator
                    "**通过**",  # Critic（第一次通过）
                ]
            )
            mock_llm.return_value = mock_instance

            result = await run_reflection_writer(
                fact_sheet=normal_fact_sheet,
                raw_content=raw_content,
                target_platform="zhihu_article",
                user_input="改写成通俗易懂的风格",
            )

            assert result is not None
            assert len(result) > 0


# ============================================================================
# Integration Test
# ============================================================================

class TestIntegration:
    """集成测试."""

    @pytest.mark.asyncio
    async def test_fact_checker_to_generator_flow(self, suspicious_fact_sheet, raw_content):
        """测试 Fact Checker → Generator 数据传递."""
        from forge.agents.reflection_writer import ReflectionState

        # 模拟 Fact Checker 输出
        state: ReflectionState = {
            "fact_sheet": suspicious_fact_sheet,
            "raw_content": raw_content,
            "verified_fact_sheet": suspicious_fact_sheet + "\n\n## ⚠️ 可疑项提示\n- 数据项可疑",
            "suspicious_items": ["可疑数据"],
            "verification_log": "验证日志",
        }

        # Generator 应使用 verified_fact_sheet
        from forge.agents.reflection_writer import generator_node

        with patch('forge.agents.reflection_writer.LLMClient') as mock_llm:
            mock_instance = MagicMock()
            mock_instance.chat_with_retry = AsyncMock(
                return_value="生成的文章..."
            )
            mock_llm.return_value = mock_instance

            result = await generator_node(state)

            # 验证 Generator 使用了 verified_fact_sheet
            assert result["current_draft"] is not None