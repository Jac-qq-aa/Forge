"""测试 Web API 端点."""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.fixture
async def client():
    """创建测试客户端."""
    from forge.web.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
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
        # Mock 爬虫
        mock_articles = [
            {
                "title": "测试文章标题",
                "summary": "文章摘要内容",
                "source_url": "https://zhuanlan.zhihu.com/p/test",
                "type": "article",
                "author": "测试作者",
            }
        ]

        with patch("forge.web.app.ZhihuScraper") as MockScraper:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.search_articles = AsyncMock(return_value=mock_articles)
            MockScraper.return_value = mock_instance

            response = await client.post(
                "/api/search",
                json={
                    "source": "工作效率",
                    "source_platform": "zhihu",
                    "max_results": 5,
                    "search_mode": "keyword",
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] == True
            assert len(data["articles"]) >= 1

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

        with patch("forge.web.app.WechatScraper") as MockScraper:
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


class TestProcessManualEndpoint:
    """手动输入处理端点测试."""

    @pytest.mark.asyncio
    async def test_process_manual_success(self, client):
        """测试手动输入处理."""
        # Mock workflow
        mock_result = {
            "script_path": "/tmp/test_script.txt",
            "video_path": "",
            "final_script": "改写后的文案内容",
        }

        with patch("forge.web.app.workflow.ainvoke") as mock_invoke:
            mock_invoke.return_value = mock_result

            response = await client.post(
                "/api/process_manual",
                json={
                    "title": "测试标题",
                    "text": "这是一段测试内容，用于验证手动输入功能是否正常工作。内容需要足够长才能触发完整的处理流程。大概需要一百多个字才行。",
                    "source_platform": "manual",
                    "target_platform": "xhs_article",
                    "generate_video": False,
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] == True
            assert "script_path" in data

    @pytest.mark.asyncio
    async def test_process_manual_with_video(self, client):
        """测试手动输入+视频生成."""
        mock_result = {
            "script_path": "/tmp/test_script.txt",
            "video_path": "/tmp/test_video.mp4",
            "final_script": "文案内容",
        }

        with patch("forge.web.app.workflow.ainvoke") as mock_invoke:
            mock_invoke.return_value = mock_result

            response = await client.post(
                "/api/process_manual",
                json={
                    "title": "视频测试",
                    "text": "这段内容用于测试视频生成功能。需要超过一百个字才能触发视频生成流程。",
                    "source_platform": "manual",
                    "target_platform": "xhs_video",
                    "generate_video": True,
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] == True


class TestSaveEndpoint:
    """保存端点测试."""

    @pytest.mark.asyncio
    async def test_save_content(self, client, tmp_path):
        """测试保存文案."""
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
        assert test_file.read_text() == "保存的测试内容"

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
        mock_result = {
            "question_title": "测试问题标题",
            "answers": [
                {
                    "url": "https://www.zhihu.com/answer/1",
                    "text_preview": "回答预览...",
                    "likes": 100,
                    "char_count": 500,
                    "author": "答主1",
                }
            ],
        }

        with patch("forge.web.app.ZhihuScraper") as MockScraper:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get_question_answers = AsyncMock(return_value=mock_result)
            MockScraper.return_value = mock_instance

            response = await client.post(
                "/api/get_answers",
                json={
                    "question_url": "https://www.zhihu.com/question/test",
                    "max_answers": 5,
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] == True
            assert "answers" in data