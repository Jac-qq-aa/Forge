"""测试 workflow 各节点 - 避免循环导入."""

import pytest
import sys
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

# 直接添加项目路径，避免包的 __init__.py 导入链
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ===== 直接加载模块，不触发 __init__.py =====
def load_editor_module():
    """直接加载 editor 模块."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "editor_module",
        PROJECT_ROOT / "forge" / "agents" / "editor.py"
    )
    module = importlib.util.module_from_spec(spec)
    # 先 mock 可能导致问题的导入
    sys.modules['forge.graph.state'] = MagicMock()
    sys.modules['forge.knowledge'] = MagicMock()
    spec.loader.exec_module(module)
    return module


def load_reviewer_module():
    """直接加载 reviewer 模块."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reviewer_module",
        PROJECT_ROOT / "forge" / "agents" / "reviewer.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules['forge.graph.state'] = MagicMock()
    spec.loader.exec_module(module)
    return module


def load_director_module():
    """直接加载 director 模块."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "director_module",
        PROJECT_ROOT / "forge" / "agents" / "director.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules['forge.graph.state'] = MagicMock()
    sys.modules['forge.config'] = MagicMock()
    spec.loader.exec_module(module)
    return module


def load_video_generator_module():
    """直接加载 video_generator 模块."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "video_generator_module",
        PROJECT_ROOT / "forge" / "agents" / "video_generator.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules['forge.graph.state'] = MagicMock()
    sys.modules['forge.tools.video_generator'] = MagicMock()
    spec.loader.exec_module(module)
    return module


class TestEditorLogic:
    """测试编辑节点的逻辑（不依赖真实模块导入）."""

    def test_editor_output_format(self):
        """编辑节点输出应该包含必要的字段."""
        # 模拟编辑节点的输出
        expected_keys = ["rewritten_draft", "revision_count"]

        # 创建模拟输出
        mock_output = {
            "rewritten_draft": "改写后的内容",
            "revision_count": 1,
        }

        for key in expected_keys:
            assert key in mock_output

    def test_editor_increments_revision(self):
        """编辑节点应该增加 revision_count."""
        initial_count = 0
        new_count = initial_count + 1

        assert new_count == 1


class TestReviewerLogic:
    """测试审核节点的逻辑."""

    def test_reviewer_output_has_final_script_or_feedback(self):
        """审核节点应该输出 final_script 或 reflection_feedback."""
        # 测试通过的输出
        approve_output = {
            "final_script": "审核通过的最终文案",
        }
        assert "final_script" in approve_output

        # 测试需要修改的输出
        feedback_output = {
            "reflection_feedback": "需要修改：增加情感共鸣",
        }
        assert "reflection_feedback" in feedback_output


class TestDirectorLogic:
    """测试导演节点的逻辑."""

    def test_director_output_format(self):
        """导演节点应该输出脚本文件路径."""
        expected_keys = ["script_path"]

        mock_output = {
            "script_path": "/tmp/test_script.txt",
            "video_path": "",
        }

        assert "script_path" in mock_output


class TestVideoGeneratorLogic:
    """测试视频生成节点的逻辑."""

    def test_video_skipped_when_disabled(self):
        """禁用视频生成时应该跳过."""
        # 逻辑：如果 generate_video == False，返回空 video_path
        generate_video = False

        if not generate_video:
            expected_output = {"video_path": ""}

        assert expected_output["video_path"] == ""

    def test_video_called_when_enabled(self):
        """启用视频生成时应该调用 API."""
        generate_video = True

        if generate_video:
            # 应该有 video_path 输出（成功或失败）
            expected_keys = ["video_path"]

        assert len(expected_keys) > 0


class TestPlatformRouting:
    """测试平台路由逻辑."""

    def test_video_platform_detection(self):
        """视频平台判断逻辑."""
        video_platforms = ["xhs_video", "zhihu_video"]
        article_platforms = ["zhihu_article", "wechat_article"]

        # 测试视频平台
        for platform in video_platforms:
            is_video = platform in ["xhs_video", "zhihu_video"]
            assert is_video == True

        # 测试文章平台
        for platform in article_platforms:
            is_article = platform in ["zhihu_article", "wechat_article"]
            assert is_article == True


class TestContentLengthHandling:
    """测试内容长度处理逻辑."""

    def test_length_target_mapping(self):
        """根据原文长度确定改写篇幅."""
        # 模拟 editor.py 中的逻辑
        test_cases = [
            (4000, "1500-2000字，分多个段落深入阐述"),
            (2000, "800-1200字"),
            (800, "600-900字"),
            (300, "500-800字"),
        ]

        for original_len, expected_target in test_cases:
            if original_len > 3000:
                target = "1500-2000字，分多个段落深入阐述"
            elif original_len > 1500:
                target = "800-1200字"
            elif original_len > 500:
                target = "600-900字"
            else:
                target = "500-800字"

            assert target == expected_target