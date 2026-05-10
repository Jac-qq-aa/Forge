#!/usr/bin/env python
"""批量导入公众号和知乎文章到向量数据库。

使用方法：
    python scripts/import_articles.py --wechat-dir data/articles/wechat
    python scripts/import_articles.py --zhihu-dir data/articles/zhihu
    python scripts/import_articles.py --wechat-dir data/articles/wechat --zhihu-dir data/articles/zhihu --clear
"""

import argparse
import logging
import sys
import os
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from forge.knowledge.manager import get_knowledge_base
from forge.knowledge.article_importer import ArticleImporter, ImportResult
from forge.knowledge.config import CATEGORIES

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='批量导入公众号和知乎文章到向量数据库',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""支持的Category类型:
  company_intro, recruitment, culture, success_cases, industry_insights,
  employee_stories, policy_updates, training, news, faq

文章JSON格式示例:
  {
    "id": "wechat_article_001",
    "title": "锐博集团2024校园招聘启动",
    "content": "文章全文内容...",
    "category": "recruitment",
    "author": "锐博人力资源",
    "publish_date": "2024-03-15"
  }
        """
    )

    parser.add_argument(
        '--wechat-dir',
        type=str,
        help='公众号文章目录路径'
    )
    parser.add_argument(
        '--zhihu-dir',
        type=str,
        help='知乎文章目录路径'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=50,
        help='批量处理大小（默认50）'
    )
    parser.add_argument(
        '--chunk-size',
        type=int,
        default=500,
        help='文本分割片段大小（默认500）'
    )
    parser.add_argument(
        '--chunk-overlap',
        type=int,
        default=50,
        help='文本分割重叠大小（默认50）'
    )
    parser.add_argument(
        '--clear',
        action='store_true',
        help='清空现有数据后重新导入'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅验证数据不实际导入'
    )
    parser.add_argument(
        '--verify',
        action='store_true',
        help='导入完成后验证结果'
    )

    args = parser.parse_args()

    # 打印标题
    print("=" * 60)
    print("文章导入向量数据库")
    print("=" * 60)

    # 初始化知识库
    kb = get_knowledge_base()

    # 清空数据（可选）
    if args.clear and not args.dry_run:
        print("\n[警告] 清空现有数据...")
        kb.clear()
        print("数据已清空")

    # 创建导入器
    importer = ArticleImporter(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )

    # 统计结果
    total_result = ImportResult()

    # 导入公众号文章
    if args.wechat_dir:
        print(f"\n导入公众号文章: {args.wechat_dir}")
        if args.dry_run:
            print("  [dry-run] 仅验证数据格式")
            # 验证数据格式
            json_files = list(Path(args.wechat_dir).glob("*.json"))
            print(f"  找到 {len(json_files)} 个JSON文件")
            for f in json_files[:5]:  # 只显示前5个
                print(f"    - {f.name}")
        else:
            result = importer.import_from_directory(
                args.wechat_dir,
                platform="wechat",
                batch_size=args.batch_size
            )
            print(result.generate_report())
            total_result.total_articles += result.total_articles
            total_result.successful_articles += result.successful_articles
            total_result.successful_chunks += result.successful_chunks
            total_result.skipped_duplicates += result.skipped_duplicates

    # 导入知乎文章
    if args.zhihu_dir:
        print(f"\n导入知乎文章: {args.zhihu_dir}")
        if args.dry_run:
            print("  [dry-run] 仅验证数据格式")
            json_files = list(Path(args.zhihu_dir).glob("*.json"))
            print(f"  找到 {len(json_files)} 个JSON文件")
        else:
            result = importer.import_from_directory(
                args.zhihu_dir,
                platform="zhihu",
                batch_size=args.batch_size
            )
            print(result.generate_report())
            total_result.total_articles += result.total_articles
            total_result.successful_articles += result.successful_articles
            total_result.successful_chunks += result.successful_chunks
            total_result.skipped_duplicates += result.skipped_duplicates

    # 验证导入
    if args.verify and not args.dry_run:
        print("\n验证导入结果...")
        verify_result = importer.verify_import()
        print(f"  总文档数: {verify_result.get('total_documents', 0)}")
        print(f"  搜索测试: {verify_result.get('search_tests', {})}")

    # 打印总计
    if not args.dry_run:
        print("\n" + "=" * 60)
        print("导入完成！")
        print(f"总计: {total_result.successful_articles} 文章, {total_result.successful_chunks} 片段")
        print("=" * 60)


if __name__ == "__main__":
    main()