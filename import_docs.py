"""Import Ruibo Group introduction document to knowledge base.

按段落切分文档，导入到 Milvus 向量数据库。
"""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docx import Document
from forge.knowledge import get_knowledge_base
from forge.knowledge.config import MILVUS_HOST, MILVUS_PORT, COLLECTION_NAME


def parse_docx_paragraphs(file_path: str) -> list:
    """Parse docx file and extract paragraphs.

    Args:
        file_path: Path to docx file.

    Returns:
        List of paragraphs (non-empty, filtered).
    """
    doc = Document(file_path)
    paragraphs = []

    for para in doc.paragraphs:
        text = para.text.strip()
        # Filter out empty paragraphs and very short ones (titles/labels)
        if text and len(text) > 20:
            paragraphs.append(text)

    return paragraphs


def smart_split_paragraphs(paragraphs: list, min_len: int = 100, max_len: int = 500) -> list:
    """Smart split paragraphs into chunks.

    策略：
    1. 短段落（<min_len）：合并相邻短段落
    2. 长段落（>max_len）：按句子切分
    3. 中等段落：保持原样

    Args:
        paragraphs: List of paragraph texts.
        min_len: Minimum chunk length.
        max_len: Maximum chunk length.

    Returns:
        List of chunks with metadata.
    """
    chunks = []
    pending_short = []  # 待合并的短段落

    for para in paragraphs:
        if len(para) < min_len:
            # 短段落：暂存，等待合并
            pending_short.append(para)
        else:
            # 先处理之前积累的短段落
            if pending_short:
                merged = "\n".join(pending_short)
                if len(merged) >= min_len:
                    chunks.append({
                        "content": merged,
                        "type": "merged_short"
                    })
                pending_short = []

            # 处理当前段落
            if len(para) > max_len:
                # 长段落：按句子切分
                sentences = split_by_sentences(para, max_len)
                for sent in sentences:
                    chunks.append({
                        "content": sent,
                        "type": "split_long"
                    })
            else:
                # 中等段落：保持原样
                chunks.append({
                    "content": para,
                    "type": "original"
                })

    # 处理最后剩余的短段落
    if pending_short:
        merged = "\n".join(pending_short)
        chunks.append({
            "content": merged,
            "type": "merged_short"
        })

    return chunks


def split_by_sentences(text: str, max_len: int) -> list:
    """Split long text by sentence boundaries.

    中文句子边界：句号、问号、感叹号、换行等。
    """
    import re

    # Split by Chinese sentence endings and newlines
    parts = re.split(r'([。！？\n]+)', text)

    sentences = []
    current = ""

    for part in parts:
        if re.match(r'[。！？\n]+', part):
            # 这是分隔符，加到当前句子末尾
            current += part
            if len(current.strip()) > 50:  # 忽略太短的
                sentences.append(current.strip())
            current = ""
        else:
            current += part

            # 如果超过最大长度，强制切分
            if len(current) > max_len:
                sentences.append(current.strip())
                current = ""

    # 处理剩余
    if current.strip() and len(current.strip()) > 50:
        sentences.append(current.strip())

    return sentences


def extract_title_from_content(content: str) -> str:
    """Extract a brief title from content.

    尝试从内容开头提取关键词作为标题。
    """
    # 取前50字作为标题基础
    preview = content[:50]

    # 尝找关键词
    keywords = ["锐博集团", "公司", "企业", "业务", "服务", "文化", "理念", "团队", "客户", "案例"]

    for kw in keywords:
        if kw in preview:
            return f"{kw}相关介绍"

    # 默认：取前20字
    return preview[:20] + "..."


def import_docx_to_knowledge_base(file_path: str, category: str = "company_intro"):
    """Import docx file to knowledge base.

    Args:
        file_path: Path to docx file.
        category: Document category.
    """
    print("=" * 60)
    print("导入锐博集团文档到知识库")
    print("=" * 60)
    print(f"文件: {file_path}")
    print(f"分类: {category}")
    print(f"Milvus: {MILVUS_HOST}:{MILVUS_PORT}")

    # 1. 解析文档
    print("\n[步骤1] 解析文档...")
    paragraphs = parse_docx_paragraphs(file_path)
    print(f"  提取到 {len(paragraphs)} 个段落")

    # 显示段落长度分布
    lengths = [len(p) for p in paragraphs]
    print(f"  段落长度: 最短 {min(lengths)}, 最长 {max(lengths)}, 平均 {sum(lengths)//len(lengths)}")

    # 2. 智能切分
    print("\n[步骤2] 智能切分...")
    chunks = smart_split_paragraphs(paragraphs, min_len=100, max_len=500)
    print(f"  切分后 {len(chunks)} 个片段")

    for i, chunk in enumerate(chunks[:5]):
        print(f"  片段{i+1}: [{chunk['type']}] {len(chunk['content'])}字 - {chunk['content'][:30]}...")

    # 3. 连接知识库并导入
    print("\n[步骤3] 连接 Milvus...")
    try:
        kb = get_knowledge_base()
        current_count = kb.count()
        print(f"  当前知识库文档数: {current_count}")
    except Exception as e:
        print(f"  ❌ 连接失败: {e}")
        print("\n请确保 Milvus 正在运行:")
        print("  docker-compose up -d")
        print("  或单独启动: docker run -d --name milvus-standalone -p 19530:19530 milvusdb/milvus:latest standalone")
        return

    # 4. 批量导入
    print("\n[步骤4] 批量导入...")

    # 生成文档ID和metadata
    doc_name = Path(file_path).stem
    documents = []

    for i, chunk in enumerate(chunks):
        doc_id = f"{doc_name}_{i+1:03d}"
        title = extract_title_from_content(chunk["content"])

        documents.append({
            "id": doc_id,
            "content": chunk["content"],
            "metadata": {
                "category": category,
                "title": title,
                "source": doc_name,
                "chunk_type": chunk["type"],
            }
        })

    kb.add_documents(documents)

    # 5. 验证
    print("\n[步骤5] 验证导入结果...")
    new_count = kb.count()
    print(f"  导入后文档数: {new_count} (新增 {new_count - current_count})")

    # 测试搜索
    print("\n[步骤6] 测试搜索...")
    test_queries = ["锐博集团", "人力资源", "公司业务", "企业文化"]
    for query in test_queries:
        results = kb.search(query, n_results=2)
        print(f"\n  搜索 '{query}' 结果:")
        for r in results:
            title = r['metadata'].get('title', '')
            preview = r['content'][:50]
            print(f"    - [{title}] {preview}...")

    print("\n" + "=" * 60)
    print("✅ 文档导入完成！")
    print("=" * 60)


if __name__ == "__main__":
    # 默认文件路径
    default_file = "锐博集团简介-详细版（20250312）.docx"

    # 支持命令行参数
    file_path = sys.argv[1] if len(sys.argv) > 1 else default_file

    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        sys.exit(1)

    import_docx_to_knowledge_base(file_path)