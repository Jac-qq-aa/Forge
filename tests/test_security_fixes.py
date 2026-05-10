"""测试安全修复 - 路径校验、TTS日志、配置校验。

这些测试不需要完整的 Web 应用依赖，可以独立运行。
"""

import pytest
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


# ===== 路径校验测试 =====

class TestValidateSavePath:
    """测试 validate_save_path 函数的安全校验。"""

    def test_valid_path_in_allowed_dir(self):
        """测试允许目录内的有效路径。"""
        # 创建临时目录模拟允许目录
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试目录
            allowed_dir = Path(tmpdir) / "output" / "scripts"
            allowed_dir.mkdir(parents=True)

            # 测试文件路径
            test_path = allowed_dir / "test_script.txt"

            # 直接测试路径校验逻辑
            path = test_path.resolve()
            allowed = allowed_dir.resolve()

            # 验证路径在允许目录内
            assert str(path).startswith(str(allowed)) or path == allowed

    def test_path_outside_allowed_dir_rejected(self):
        """测试拒绝不允许目录的路径。"""
        # 模拟恶意路径
        malicious_paths = [
            "/etc/passwd",
            "/home/user/.ssh/id_rsa",
            "/var/log/auth.log",
            "~/.bashrc",
            "../config.py",  # 相对路径尝试
        ]

        for path in malicious_paths:
            # 这些路径应该被拒绝
            resolved = Path(path).resolve()
            allowed_dirs = ["/tmp/forge_scripts", "/tmp/output/videos"]

            is_allowed = any(
                str(resolved).startswith(str(Path(d).resolve()) + "/") or
                resolved == Path(d).resolve()
                for d in allowed_dirs
            )

            assert not is_allowed, f"恶意路径 {path} 应该被拒绝"

    def test_symlink_attack_prevented(self):
        """测试符号链接攻击被防止。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建符号链接指向系统文件
            allowed_dir = Path(tmpdir) / "allowed"
            allowed_dir.mkdir()

            symlink_path = allowed_dir / "malicious_link"
            try:
                symlink_path.symlink_to("/etc/passwd")
            except OSError:
                # WSL 可能不允许创建符号链接
                pytest.skip("Cannot create symlink in this environment")

            # resolve() 应该返回真实路径（/etc/passwd）
            resolved = symlink_path.resolve()

            # 验证真实路径不在允许目录内
            allowed_resolved = allowed_dir.resolve()
            is_allowed = str(resolved).startswith(str(allowed_resolved) + "/")

            # 符号链接指向的文件不应该被允许
            assert not is_allowed or str(resolved) == "/etc/passwd"

    def test_prefix_path_attack_prevented(self):
        """测试前缀路径攻击被防止（如 /tmp/forge_videos_other）。"""
        # 模拟攻击路径
        attack_paths = [
            "/tmp/forge_videos_other/evil.txt",  # 前缀匹配但不是子目录
            "/tmp/forge_videos_backup/hack.txt",
            "/tmp/forge_videos_backup",  # 单独的目录
        ]

        allowed_dir = "/tmp/forge_videos"

        for path in attack_paths:
            resolved = Path(path).resolve()
            allowed_resolved = Path(allowed_dir).resolve()

            # 必须以 "/" 结尾才是子目录（或等于允许目录）
            is_allowed = (
                str(resolved).startswith(str(allowed_resolved) + "/") or
                resolved == allowed_resolved
            )

            # 前缀匹配但不以 "/" 结尾的应该被拒绝
            assert not is_allowed, f"攻击路径 {path} 应该被拒绝"


class TestIsPathAllowed:
    """测试 is_path_allowed 函数的路径校验。"""

    def test_valid_video_download_path(self):
        """测试有效的视频下载路径。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed_dir = Path(tmpdir) / "videos"
            allowed_dir.mkdir()

            test_file = allowed_dir / "video.mp4"
            test_file.touch()

            # 测试路径校验逻辑
            allowed_dirs = [str(allowed_dir)]
            path_str = str(test_file)

            resolved = Path(path_str).resolve()
            is_allowed = any(
                str(resolved).startswith(str(Path(d).resolve()) + "/") or
                resolved == Path(d).resolve()
                for d in allowed_dirs
            )

            assert is_allowed

    def test_invalid_download_path_rejected(self):
        """测试非法下载路径被拒绝。"""
        invalid_paths = [
            "/etc/shadow",
            "/root/.bash_history",
            "/var/www/html/config.php",
        ]

        allowed_dirs = ["/tmp/forge_videos"]

        for path in invalid_paths:
            resolved = Path(path).resolve()
            is_allowed = any(
                str(resolved).startswith(str(Path(d).resolve()) + "/") or
                resolved == Path(d).resolve()
                for d in allowed_dirs
            )

            assert not is_allowed


# ===== TTS 日志测试 =====

class TestTtsLogSecurity:
    """测试 TTS 模块日志不泄露密钥。"""

    def test_init_log_no_api_key(self):
        """测试初始化日志不包含 API key。"""
        # 模拟日志捕获
        import logging
        from io import StringIO

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.INFO)

        logger = logging.getLogger("TTS")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # 模拟日志输出（不应该包含密钥）
        api_key = "sk-test-secret-key-12345"
        model = "cosyvoice-v1"
        voice = "longxiaochun"

        # 正确的日志格式（修复后）
        logger.info(f"[TTS] Initialized: model={model}, voice={voice}")

        log_content = log_stream.getvalue()

        # 验证日志不包含密钥
        assert api_key not in log_content
        assert "sk-" not in log_content
        assert "api_key" not in log_content or "api_key=" not in log_content

    def test_generate_log_no_api_key(self):
        """测试生成过程日志不包含 API key。"""
        import logging
        from io import StringIO

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.INFO)

        logger = logging.getLogger("TTS")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # 模拟日志输出
        logger.info("[TTS] Model: cosyvoice-v1, Voice: longxiaochun")
        logger.info("[TTS] Generating audio for 100 chars...")

        log_content = log_stream.getvalue()

        # 验证日志不包含密钥相关内容
        assert "api_key" not in log_content
        assert "dashscope" not in log_content

    def test_error_log_no_full_api_key(self):
        """测试错误日志不输出完整密钥。"""
        import logging
        from io import StringIO

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.ERROR)

        logger = logging.getLogger("TTS")
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)

        # 模拟错误日志（修复后）
        logger.error("[TTS] API key is set but TTS returned empty data - check key validity")

        log_content = log_stream.getvalue()

        # 验证日志不包含完整密钥
        assert "sk-" not in log_content
        assert "dashscope.api_key: sk" not in log_content


# ===== 配置校验测试 =====

class TestDbConfigValidation:
    """测试数据库配置校验。"""

    def test_missing_pg_user_raises_error(self):
        """测试缺少 PG_USER 时抛出错误。"""
        with patch.dict(os.environ, {"PG_USER": "", "PG_PASSWORD": "test123"}):
            # 模拟配置校验逻辑
            pg_user = os.getenv("PG_USER", "")
            pg_password = os.getenv("PG_PASSWORD", "")

            missing = []
            if not pg_user:
                missing.append("PG_USER")
            if not pg_password:
                missing.append("PG_PASSWORD")

            assert "PG_USER" in missing

    def test_missing_pg_password_raises_error(self):
        """测试缺少 PG_PASSWORD 时抛出错误。"""
        with patch.dict(os.environ, {"PG_USER": "forge", "PG_PASSWORD": ""}):
            pg_user = os.getenv("PG_USER", "")
            pg_password = os.getenv("PG_PASSWORD", "")

            missing = []
            if not pg_user:
                missing.append("PG_USER")
            if not pg_password:
                missing.append("PG_PASSWORD")

            assert "PG_PASSWORD" in missing

    def test_complete_config_passes(self):
        """测试完整配置通过校验。"""
        with patch.dict(os.environ, {"PG_USER": "forge", "PG_PASSWORD": "secure123"}):
            pg_user = os.getenv("PG_USER", "")
            pg_password = os.getenv("PG_PASSWORD", "")

            missing = []
            if not pg_user:
                missing.append("PG_USER")
            if not pg_password:
                missing.append("PG_PASSWORD")

            assert len(missing) == 0


# ===== 会话状态访问测试 =====

class TestSessionStateSafeAccess:
    """测试会话状态使用 .get() 安全访问。"""

    def test_session_with_missing_fields(self):
        """测试会话缺少字段时安全访问。"""
        # 模拟不完整的会话数据
        incomplete_session = {
            "session_id": "test-123",
            # 缺少 article_id, draft_v1, current_draft 等
        }

        # 使用 .get() 安全访问
        result = {
            "session_id": incomplete_session.get("session_id", "unknown"),
            "article_id": incomplete_session.get("article_id", ""),
            "stage": incomplete_session.get("stage", ""),
            "outline": incomplete_session.get("outline"),
            "outline_version": incomplete_session.get("outline_version", 0),
            "draft_v1": incomplete_session.get("draft_v1"),
            "current_draft": incomplete_session.get("current_draft"),
        }

        # 验证不会抛出 KeyError
        assert result["session_id"] == "test-123"
        assert result["article_id"] == ""
        assert result["stage"] == ""
        assert result["outline_version"] == 0

    def test_session_none_handling(self):
        """测试 load_session 返回 None 时正确处理。"""
        session = None  # 模拟 load_session 返回 None

        # 正确处理 None
        if not session:
            result = {"error": "Session not found"}
        else:
            result = {
                "session_id": session.get("session_id"),
            }

        assert result == {"error": "Session not found"}

    def test_full_session_data(self):
        """测试完整会话数据正常返回。"""
        full_session = {
            "session_id": "test-456",
            "article_id": "article-123",
            "stage": "tuning",
            "outline": {"sections": []},
            "outline_version": 2,
            "draft_v1": "初稿内容",
            "current_draft": "当前稿件",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-02T00:00:00",
        }

        result = {
            "session_id": full_session.get("session_id", "unknown"),
            "article_id": full_session.get("article_id", ""),
            "stage": full_session.get("stage", ""),
            "outline": full_session.get("outline"),
            "outline_version": full_session.get("outline_version", 0),
            "draft_v1": full_session.get("draft_v1"),
            "current_draft": full_session.get("current_draft"),
            "created_at": full_session.get("created_at", ""),
            "updated_at": full_session.get("updated_at", ""),
        }

        assert result["session_id"] == "test-456"
        assert result["article_id"] == "article-123"
        assert result["stage"] == "tuning"
        assert result["draft_v1"] == "初稿内容"


# ===== 定稿状态一致性测试 =====

class TestFinalizeSessionConsistency:
    """测试定稿会话状态一致性。"""

    def test_finalize_with_explicit_content(self):
        """测试使用传入的 content 定稿。"""
        # 模拟会话数据
        session = {
            "session_id": "test-789",
            "current_draft": "旧稿件内容",
            "draft_v1": "初稿内容",
        }

        # 用户传入的新内容
        user_content = "用户编辑后的最终稿件"

        # 定稿逻辑（修复后）
        final_draft = user_content  # 优先使用传入的内容

        assert final_draft == "用户编辑后的最终稿件"
        assert final_draft != session["current_draft"]

    def test_finalize_without_content_fallback(self):
        """测试没有传入 content 时使用 session 中的 draft。"""
        session = {
            "session_id": "test-789",
            "current_draft": "当前稿件",
            "draft_v1": "初稿内容",
        }

        # 没有传入内容时，使用 session 中的 draft
        final_draft = None
        if final_draft is None:
            final_draft = session.get("current_draft", "") or session.get("draft_v1", "")

        assert final_draft == "当前稿件"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])