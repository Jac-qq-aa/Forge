# forge/deep_mode/tests/test_workflow.py

"""Tests for Deep Mode Workflow (deer-flow pattern)."""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock

from forge.deep_mode.workflow import (
    DeepModeState,
    WorkflowState,  # compatibility alias
    rag_search,
    generate_outline,
    revise_outline,
    generate_content,
    run_tuning_agent,
    TuningAgentFallback,  # deprecated but kept for compatibility
    get_tuning_agent,
)


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient for testing."""
    mock = AsyncMock()
    mock.chat_with_retry = AsyncMock(return_value="Mock response content")
    return mock


@pytest.fixture
def mock_sync_llm_client():
    """Mock SyncLLMClient for testing."""
    mock = Mock()
    mock.chat_with_retry = Mock(return_value="Mock sync response")
    return mock


@pytest.fixture
def mock_knowledge_base():
    """Mock KnowledgeBase for testing."""
    mock = Mock()
    mock.get_context_for_topic = Mock(return_value="Mock RAG context")
    return mock


@pytest.fixture
def sample_source_article():
    """Sample source article for testing."""
    return {
        "title": "Test Article",
        "text": "This is test content for the article.",
        "url": "https://example.com/test",
    }


class TestWorkflowNodes:
    """Test individual workflow node functions."""

    @pytest.mark.asyncio
    async def test_rag_search(self, sample_source_article, mock_knowledge_base):
        """Test RAG search function."""
        with patch('forge.deep_mode.workflow.get_knowledge_base', return_value=mock_knowledge_base):
            result = await rag_search(sample_source_article)
            assert result == "Mock RAG context"

    @pytest.mark.asyncio
    async def test_rag_search_timeout(self, sample_source_article):
        """Test RAG search handles timeout."""
        def slow_search(*args):
            # 模拟同步调用
            import time
            time.sleep(15)
            return "should not reach"

        mock_kb = Mock()
        mock_kb.get_context_for_topic = slow_search

        with patch('forge.deep_mode.workflow.get_knowledge_base', return_value=mock_kb):
            result = await rag_search(sample_source_article)
            assert result == ""

    @pytest.mark.asyncio
    async def test_rag_search_error(self, sample_source_article):
        """Test RAG search handles errors."""
        mock_kb = Mock()
        mock_kb.get_context_for_topic = Mock(side_effect=Exception("KB error"))

        with patch('forge.deep_mode.workflow.get_knowledge_base', return_value=mock_kb):
            result = await rag_search(sample_source_article)
            assert result == ""

    @pytest.mark.asyncio
    async def test_generate_outline(self, sample_source_article, mock_llm_client):
        """Test outline generation."""
        with patch('forge.deep_mode.workflow.LLMClient', return_value=mock_llm_client):
            result = await generate_outline(
                sample_source_article,
                "请改成知乎风格",
                "Mock RAG context"
            )
            assert result == "Mock response content"

    @pytest.mark.asyncio
    async def test_revise_outline(self, mock_llm_client):
        """Test outline revision."""
        with patch('forge.deep_mode.workflow.LLMClient', return_value=mock_llm_client):
            result = await revise_outline(
                "Original outline",
                "增加更多细节"
            )
            assert result == "Mock response content"

    @pytest.mark.asyncio
    async def test_generate_content(self, sample_source_article, mock_llm_client):
        """Test content generation."""
        with patch('forge.deep_mode.workflow.LLMClient', return_value=mock_llm_client):
            result = await generate_content(
                "Test outline",
                sample_source_article,
                "Mock RAG context"
            )
            assert result == "Mock response content"


class TestDeepModeState:
    """Test DeepModeState TypedDict."""

    def test_state_has_messages(self):
        """Test DeepModeState extends AgentState with messages."""
        state = DeepModeState(
            messages=[],
            current_draft="Test draft",
            stage="tuning",
        )
        assert "messages" in state
        assert state["current_draft"] == "Test draft"

    def test_workflow_state_alias(self):
        """Test WorkflowState is alias for DeepModeState."""
        state = WorkflowState(
            messages=[],
            current_draft="Test draft",
        )
        assert isinstance(state, dict)
        assert state["current_draft"] == "Test draft"


class TestTuningAgentFallback:
    """Test TuningAgentFallback (deprecated)."""

    @pytest.mark.asyncio
    async def test_process_request(self, mock_llm_client):
        """Test fallback processing."""
        fallback = TuningAgentFallback()

        with patch('forge.deep_mode.workflow.LLMClient', return_value=mock_llm_client):
            result = await fallback.process_request(
                "Current draft",
                "改得轻松点"
            )
            assert result == "Mock response content"


class TestTuningAgent:
    """Test Tuning Agent creation and execution."""

    def test_get_tuning_agent(self):
        """Test tuning agent getter."""
        # Reset global
        import forge.deep_mode.workflow as workflow_module
        workflow_module._tuning_agent = None

        with patch('forge.deep_mode.workflow.create_tuning_agent') as mock_create:
            mock_create.return_value = Mock()
            agent = get_tuning_agent()
            assert agent is not None

    @pytest.mark.asyncio
    async def test_run_tuning_agent_fallback(self, mock_llm_client):
        """Test run_tuning_agent with fallback."""
        import forge.deep_mode.workflow as workflow_module
        workflow_module._tuning_agent = None

        with patch('forge.deep_mode.workflow.create_tuning_agent', return_value=None):
            with patch('forge.deep_mode.workflow.LLMClient', return_value=mock_llm_client):
                result = await run_tuning_agent(
                    "Current draft content",
                    "把语气改轻松点"
                )
                assert result == "Mock response content"


class TestAPICompatibility:
    """Test API compatibility with old workflow."""

    @pytest.mark.asyncio
    async def test_run_plan_execute_outline_generation(self, mock_llm_client, mock_knowledge_base):
        """Test run_plan_execute for outline generation."""
        from forge.deep_mode.workflow import run_plan_execute

        mock_session_manager = AsyncMock()
        mock_session = {
            "session_id": "test123",
            "article_id": "article1",
            "source_article": {"title": "Test", "text": "Content"},
            "outline": "",
            "rag_context": "",
            "outline_version": 0,
        }
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        mock_session_manager.update_session = AsyncMock(return_value=mock_session)

        with patch('forge.deep_mode.session_manager.get_session_manager', return_value=mock_session_manager):
            with patch('forge.deep_mode.workflow.LLMClient', return_value=mock_llm_client):
                with patch('forge.deep_mode.workflow.get_knowledge_base', return_value=mock_knowledge_base):
                    result = await run_plan_execute(
                        "test123",
                        "outline_generation",
                        "请改成知乎风格"
                    )
                    assert result is not None

    @pytest.mark.asyncio
    async def test_run_plan_execute_content_generation(self, mock_llm_client):
        """Test run_plan_execute for content generation."""
        from forge.deep_mode.workflow import run_plan_execute

        mock_session_manager = AsyncMock()
        mock_session = {
            "session_id": "test123",
            "article_id": "article1",
            "source_article": {"title": "Test", "text": "Content"},
            "outline": "Test outline",
            "rag_context": "Mock context",
            "outline_version": 1,
        }
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        mock_session_manager.update_session = AsyncMock(return_value=mock_session)

        with patch('forge.deep_mode.session_manager.get_session_manager', return_value=mock_session_manager):
            with patch('forge.deep_mode.workflow.LLMClient', return_value=mock_llm_client):
                result = await run_plan_execute(
                    "test123",
                    "content_generation"
                )
                assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])