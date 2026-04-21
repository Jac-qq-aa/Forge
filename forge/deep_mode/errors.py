# forge/deep_mode/errors.py

"""深度生成模式异常定义。"""


class DeepModeError(Exception):
    """深度生成模式基础异常。"""
    pass


class SessionNotFoundError(DeepModeError):
    """会话不存在。"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class InvalidStageError(DeepModeError):
    """操作与当前阶段不匹配。"""
    def __init__(self, current_stage: str, expected_stage: str):
        self.current_stage = current_stage
        self.expected_stage = expected_stage
        super().__init__(f"Invalid stage: current={current_stage}, expected={expected_stage}")


class OutlineRevisionLimitError(DeepModeError):
    """大纲修改次数已达上限。"""
    def __init__(self, max_revisions: int):
        self.max_revisions = max_revisions
        super().__init__(f"Outline revision limit reached: {max_revisions}")


class AgentTimeoutError(DeepModeError):
    """Agent 执行超时。"""
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Agent execution timeout: {timeout_seconds}s")


class RAGSearchFailedError(DeepModeError):
    """知识库搜索失败。"""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"RAG search failed: {reason}")