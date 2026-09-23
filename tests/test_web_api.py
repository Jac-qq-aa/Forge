"""测试 Web API 端点."""

import pytest
import jwt
import time
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.fixture
async def client(monkeypatch):
    """创建测试客户端."""
    from forge.web.app import app

    secret = "a" * 32
    monkeypatch.setenv("FORGE_JWT_SECRET", secret)
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "scope": "jobs admin",
            "iat": now,
            "exp": now + 3600,
            "iss": "forge-auth",
            "aud": "forge-api",
        },
        secret,
        algorithm="HS256",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac


class TestStatusEndpoint:
    """状态检查端点测试."""

    @pytest.mark.asyncio
    async def test_status_returns_ok(self, client):
        """状态端点应该返回 ok."""
        response = await client.get("/api/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "service" in data


class TestSearchEndpoint:
    """搜索端点测试."""

    @pytest.mark.asyncio
    async def test_search_zhihu_articles(self, client):
        """测试知乎文章搜索."""
        async def fake_submit_job(**kwargs):
            return ({"id": str(uuid4()), "operation": kwargs["operation"], "status": "queued"}, True)

        with patch("forge.async_jobs.service.submit_job", side_effect=fake_submit_job):
            response = await client.post(
                "/api/search",
                json={
                    "source": "工作效率",
                    "source_platform": "zhihu",
                    "max_results": 5,
                    "search_mode": "keyword",
                }
            )

            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "queued"
            assert data["operation"] == "zhihu_search"

    @pytest.mark.asyncio
    async def test_search_wechat_articles(self, client):
        """测试微信文章搜索."""
        mock_articles = [
            {
                "title": "微信测试文章",
                "summary": "微信摘要",
                "source_url": "https://mp.weixin.qq.com/s/test",
                "type": "wechat_article",
                "author": "测试公众号",
            }
        ]

        with patch("forge.tools.wechat_scraper.WechatScraper") as MockScraper:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.search_articles = AsyncMock(return_value=mock_articles)
            MockScraper.return_value = mock_instance

            response = await client.post(
                "/api/search",
                json={
                    "source": "职场技巧",
                    "source_platform": "wechat",
                    "max_results": 3,
                    "search_mode": "keyword",
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] == True

    @pytest.mark.asyncio
    async def test_search_invalid_platform(self, client):
        """无效平台应该返回错误."""
        response = await client.post(
            "/api/search",
            json={
                "source": "test",
                "source_platform": "invalid_platform",
                "max_results": 5,
            }
        )

        # 应该返回 400 或 success=false
        assert response.status_code == 400 or response.json().get("success") == False


class TestSaveEndpoint:
    """保存端点测试."""

    @pytest.mark.asyncio
    async def test_save_content(self, client, tmp_path, monkeypatch):
        """测试保存文案."""
        monkeypatch.setattr("forge.config.SCRIPT_OUTPUT_DIR", str(tmp_path))
        test_file = tmp_path / "test_script.txt"

        response = await client.post(
            "/api/save",
            json={
                "script_path": str(test_file),
                "content": "保存的测试内容",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] == True

        # 验证文件已保存
        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "保存的测试内容"

    @pytest.mark.asyncio
    async def test_save_without_path(self, client):
        """无路径应该返回错误."""
        response = await client.post(
            "/api/save",
            json={
                "script_path": "",
                "content": "测试内容",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] == False


class TestGetAnswersEndpoint:
    """获取知乎回答列表测试."""

    @pytest.mark.asyncio
    async def test_get_answers_success(self, client):
        """测试获取回答列表."""
        async def fake_submit_job(**kwargs):
            return ({"id": str(uuid4()), "operation": kwargs["operation"], "status": "queued"}, True)

        with patch("forge.async_jobs.service.submit_job", side_effect=fake_submit_job):
            response = await client.post(
                "/api/get_answers",
                json={
                    "question_url": "https://www.zhihu.com/question/test",
                    "max_answers": 5,
                }
            )

            assert response.status_code == 202
            data = response.json()
            assert data["operation"] == "zhihu_question_answers"
