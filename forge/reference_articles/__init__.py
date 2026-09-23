"""参考文章库：把文章结构与风格转换为可检索的 ReferencePack。"""

from forge.reference_articles.analyzer import analyze_reference_article
from forge.reference_articles.library import ReferenceArticleLibrary, get_reference_article_library
from forge.reference_articles.models import ReferencePack, ReferenceRequest

__all__ = [
    "ReferenceArticleLibrary",
    "ReferencePack",
    "ReferenceRequest",
    "analyze_reference_article",
    "get_reference_article_library",
]
