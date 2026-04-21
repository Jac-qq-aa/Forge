"""pytest 配置和共享 fixtures."""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_raw_content():
    """模拟爬取的原始内容."""
    return {
        "title": "提高工作效率的10个方法",
        "text": """
在现代职场中，工作效率直接决定了我们的产出和成就感。
以下是10个经过验证的高效工作方法：

1. 制定每日计划 - 早上花5分钟列出当天要完成的任务
2. 番茄工作法 - 25分钟专注+5分钟休息的循环
3. 减少干扰 - 关闭不必要的通知，创造专注环境
4. 定期休息 - 每小时站起来活动一下
5. 优先处理重要任务 - 先做最难或最重要的事
6. 批量处理相似任务 - 把同类工作集中处理
7. 学会说不 - 拒绝不必要的会议和请求
8. 保持工作区整洁 - 减少视觉干扰
9. 使用工具辅助 - 利用自动化工具节省时间
10. 反复盘总结 - 每周回顾优化工作流程

坚持这些方法，你会发现工作效率显著提升！
""",
        "author": "职场达人",
        "source_url": "https://example.com/article/test",
        "images": [],
        "likes": 1000,
        "comments": 50,
    }


@pytest.fixture
def mock_state(mock_raw_content):
    """模拟 workflow 的初始状态."""
    return {
        "topic": "https://example.com/article/test",
        "source_platform": "manual",
        "target_platform": "xhs_video",
        "raw_content": mock_raw_content,
        "revision_count": 0,
        "generate_video": False,
        "skip_publish": True,
    }


@pytest.fixture
def mock_state_with_video(mock_raw_content):
    """需要生成视频的状态."""
    return {
        "topic": "https://example.com/article/test",
        "source_platform": "manual",
        "target_platform": "xhs_video",
        "raw_content": mock_raw_content,
        "revision_count": 0,
        "generate_video": True,
        "skip_publish": True,
    }


@pytest.fixture
def mock_editor_result():
    """模拟编辑节点的输出."""
    return {
        "rewritten_draft": """
你敢信吗？一个番茄钟就能让效率翻倍！

说实话，我之前也不信。直到试了这套方法...

第一招：番茄工作法
25分钟专注+5分钟休息，循环往复
简单但真的有效！

第二招：每日计划
早上花5分钟列出任务清单
心里有数，干活不慌

第三招：学会说不
拒绝不必要的会议
时间留给真正重要的事

试试看，一周后你会发现变化！
""",
        "revision_count": 1,
    }


@pytest.fixture
def mock_llm_response():
    """模拟 LLM API 的响应."""
    return {
        "choices": [
            {
                "message": {
                    "content": "这是改写后的内容...",
                }
            }
        ]
    }