"""从原文建立可定位、可审核的结构化事实注册表。"""

from forge.facts.source_extractor import (
    LLMCandidateAdapter,
    SourceChunk,
    SourceFactExtraction,
    SourceFactExtractor,
)

__all__ = [
    "LLMCandidateAdapter",
    "SourceChunk",
    "SourceFactExtraction",
    "SourceFactExtractor",
]
