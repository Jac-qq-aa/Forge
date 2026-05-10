"""文章导入器 - 将公众号/知乎文章导入向量数据库。

支持：
1. 单篇文章导入
2. 目录批量导入
3. 进度显示
4. 重复检测
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any

from forge.knowledge.manager import get_knowledge_base, KnowledgeBase
from forge.knowledge.text_splitter import ChineseTextSplitter
from forge.knowledge.config import CATEGORIES

logger = logging.getLogger(__name__)


class ImportResult:
    """导入结果统计。"""

    def __init__(self):
        self.total_articles: int = 0
        self.successful_articles: int = 0
        self.successful_chunks: int = 0
        self.failed_articles: List[str] = []
        self.skipped_duplicates: int = 0
        self.errors: List[Dict[str, str]] = []

    def add_success(self, article_id: str, chunk_count: int):
        """记录成功导入。"""
        self.successful_articles += 1
        self.successful_chunks += chunk_count

    def add_failure(self, article_id: str, error: str):
        """记录失败。"""
        self.failed_articles.append(article_id)
        self.errors.append({"article_id": article_id, "error": error})

    def add_skip(self, article_id: str):
        """记录跳过（重复）。"""
        self.skipped_duplicates += 1

    def generate_report(self) -> str:
        """生成导入报告。"""
        lines = [
            "=" * 50,
            "导入结果报告",
            "=" * 50,
            f"总文章数: {self.total_articles}",
            f"成功导入: {self.successful_articles} ({self.successful_chunks} 片段)",
            f"跳过重复: {self.skipped_duplicates}",
            f"失败数量: {len(self.failed_articles)}",
        ]

        if self.failed_articles:
            lines.append("\n失败文章列表:")
            for article_id in self.failed_articles:
                lines.append(f"  - {article_id}")

        if self.errors:
            lines.append("\n错误详情:")
            for error in self.errors[:5]:  # 只显示前5个错误
                lines.append(f"  - {error['article_id']}: {error['error'][:100]}")

        lines.append("=" * 50)
        return "\n".join(lines)


class ArticleImporter:
    """文章导入向量数据库。"""

    def __init__(
        self,
        knowledge_base: Optional[KnowledgeBase] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ):
        """
        Args:
            knowledge_base: 知识库实例（默认使用全局单例）
            chunk_size: 分割片段大小
            chunk_overlap: 分割重叠大小
        """
        self.kb = knowledge_base or get_knowledge_base()
        self.splitter = ChineseTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def validate_article(self, article: Dict[str, Any]) -> Optional[str]:
        """验证文章数据格式。

        Returns:
            None if valid, error message if invalid
        """
        # 必填字段检查
        required_fields = ['id', 'title', 'content', 'category']
        for field in required_fields:
            if field not in article:
                return f"缺少必填字段: {field}"

        # category验证
        if article['category'] not in CATEGORIES:
            return f"无效的category: {article['category']}，可选值: {CATEGORIES}"

        # content长度检查
        if len(article['content']) < 50:
            return "content内容过短（少于50字符）"

        return None

    def check_duplicate(self, article_id: str) -> bool:
        """检查文章是否已导入（通过ID前缀搜索）。

        Args:
            article_id: 文章ID

        Returns:
            True if duplicate found
        """
        try:
            # 使用search检查是否有相同ID前缀的文档
            # 注意：这里使用模糊搜索，因为Milvus不支持直接ID查询
            results = self.kb.search(article_id, n_results=1)
            if results:
                # 检查返回结果的标题是否包含文章ID
                for result in results:
                    metadata = result.get('metadata', {})
                    if metadata.get('title', '').startswith(article_id):
                        return True
            return False
        except Exception as e:
            logger.warning(f"[Importer] Duplicate check failed for {article_id}: {e}")
            return False

    def import_article(self, article: Dict[str, Any]) -> int:
        """
        导入单篇文章。

        Args:
            article: 文章数据dict
                - id: 文章唯一标识
                - title: 文章标题
                - content: 文章内容
                - category: 分类
                - 其他可选字段: author, publish_date, source_url

        Returns:
            导入的片段数量

        Raises:
            ValueError: 数据格式验证失败
        """
        # 验证数据
        error = self.validate_article(article)
        if error:
            raise ValueError(error)

        article_id = article['id']
        title = article['title']
        content = article['content']
        category = article['category']

        logger.info(f"[Importer] Importing article: {article_id} ({title})")

        # 检查重复
        if self.check_duplicate(article_id):
            logger.info(f"[Importer] Skipping duplicate: {article_id}")
            return 0

        # 分割文本
        metadata = {
            'title': title,
            'category': category,
            'author': article.get('author', ''),
            'publish_date': article.get('publish_date', ''),
        }
        chunks = self.splitter.split_text(content, metadata)

        if not chunks:
            logger.warning(f"[Importer] No chunks generated for {article_id}")
            return 0

        # 构建文档列表
        documents = []
        for i, chunk in enumerate(chunks):
            doc_id = self._generate_chunk_id(article_id, i)
            doc = {
                'id': doc_id,
                'content': chunk['content'],
                'metadata': {
                    'category': category,
                    'title': title,
                    'chunk_index': i,
                    'total_chunks': len(chunks),
                }
            }
            documents.append(doc)

        # 批量插入
        self.kb.add_documents(documents)

        logger.info(f"[Importer] Imported {len(chunks)} chunks for {article_id}")
        return len(chunks)

    def import_from_file(self, file_path: str) -> ImportResult:
        """
        从JSON文件导入文章。

        Args:
            file_path: JSON文件路径

        Returns:
            ImportResult with import statistics
        """
        result = ImportResult()
        result.total_articles = 1

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                article = json.load(f)

            # 添加源文件信息
            article['_source_file'] = file_path

            chunk_count = self.import_article(article)
            result.add_success(article.get('id', 'unknown'), chunk_count)

        except json.JSONDecodeError as e:
            error_msg = f"JSON解析失败: {e}"
            result.add_failure(file_path, error_msg)
            logger.error(f"[Importer] {error_msg}")

        except ValueError as e:
            result.add_failure(file_path, str(e))
            logger.error(f"[Importer] Validation failed: {e}")

        except Exception as e:
            error_msg = f"导入失败: {e}"
            result.add_failure(file_path, error_msg)
            logger.error(f"[Importer] {error_msg}")

        return result

    def import_from_directory(
        self,
        dir_path: str,
        platform: str = "wechat",
        batch_size: int = 50
    ) -> ImportResult:
        """
        从目录批量导入文章。

        Args:
            dir_path: 文章目录路径
            platform: 平台类型（wechat/zhihu）
            batch_size: 批量处理大小

        Returns:
            ImportResult with import statistics
        """
        result = ImportResult()

        # 检查目录是否存在
        if not os.path.isdir(dir_path):
            result.add_failure(dir_path, f"目录不存在: {dir_path}")
            return result

        # 获取所有JSON文件
        json_files = list(Path(dir_path).glob("*.json"))
        result.total_articles = len(json_files)

        if not json_files:
            logger.warning(f"[Importer] No JSON files found in {dir_path}")
            return result

        logger.info(f"[Importer] Found {len(json_files)} JSON files in {dir_path}")

        # 批量处理
        batch_articles = []
        for i, file_path in enumerate(json_files):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    article = json.load(f)

                # 添加平台信息
                article['_source_file'] = str(file_path)
                article['_platform'] = platform

                # 验证数据
                error = self.validate_article(article)
                if error:
                    result.add_failure(str(file_path), error)
                    continue

                # 检查重复
                if self.check_duplicate(article['id']):
                    result.add_skip(article['id'])
                    continue

                batch_articles.append(article)

                # 达到批量大小时处理
                if len(batch_articles) >= batch_size:
                    self._process_batch(batch_articles, result)
                    batch_articles = []
                    logger.info(f"[Importer] Processed batch, progress: {i+1}/{len(json_files)}")

            except json.JSONDecodeError as e:
                result.add_failure(str(file_path), f"JSON解析失败: {e}")

            except Exception as e:
                result.add_failure(str(file_path), f"处理失败: {e}")

        # 处理剩余文章
        if batch_articles:
            self._process_batch(batch_articles, result)

        return result

    def _process_batch(
        self,
        articles: List[Dict[str, Any]],
        result: ImportResult
    ):
        """批量处理文章。"""
        all_documents = []

        for article in articles:
            try:
                # 分割文本
                metadata = {
                    'title': article['title'],
                    'category': article['category'],
                }
                chunks = self.splitter.split_text(article['content'], metadata)

                if not chunks:
                    continue

                # 构建文档
                for i, chunk in enumerate(chunks):
                    doc_id = self._generate_chunk_id(article['id'], i)
                    doc = {
                        'id': doc_id,
                        'content': chunk['content'],
                        'metadata': {
                            'category': article['category'],
                            'title': article['title'],
                            'chunk_index': i,
                        }
                    }
                    all_documents.append(doc)

                result.add_success(article['id'], len(chunks))

            except Exception as e:
                result.add_failure(article['id'], str(e))

        # 批量插入向量数据库
        if all_documents:
            self.kb.add_documents(all_documents)
            logger.info(f"[Importer] Batch inserted {len(all_documents)} documents")

    def _generate_chunk_id(self, article_id: str, chunk_index: int) -> str:
        """生成唯一片段ID。

        格式：{article_id}_chunk_{index:03d}
        """
        return f"{article_id}_chunk_{chunk_index:03d}"

    def verify_import(self) -> Dict[str, Any]:
        """验证导入结果。

        Returns:
            验证结果dict
        """
        try:
            total_count = self.kb.count()
            logger.info(f"[Importer] Total documents in KB: {total_count}")

            # 测试搜索
            test_queries = ["锐博", "招聘", "企业文化"]
            search_results = {}
            for query in test_queries:
                results = self.kb.search(query, n_results=3)
                search_results[query] = len(results)

            return {
                "total_documents": total_count,
                "search_tests": search_results,
                "status": "success",
            }

        except Exception as e:
            return {
                "total_documents": 0,
                "search_tests": {},
                "status": "failed",
                "error": str(e),
            }


__all__ = ["ArticleImporter", "ImportResult"]