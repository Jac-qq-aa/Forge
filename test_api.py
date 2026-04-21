"""Test API process endpoint."""

import asyncio
import httpx

async def test_api():
    print("=== 测试 API ===")

    # 使用一个测试URL
    test_url = "人力资源"  # 关键词

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "http://localhost:8000/api/process",
            json={
                "source_url": test_url,
                "source_platform": "wechat",
                "target_platform": "wechat_article",
            }
        )

        print(f"Status: {response.status_code}")
        data = response.json()

        print("\n=== 返回数据 ===")
        for key in data.keys():
            val = data[key]
            if isinstance(val, str):
                print(f"  {key}: len={len(val)}")
            else:
                print(f"  {key}: {val}")

        print("\n=== 原文内容 ===")
        print(f"  original_title: {data.get('original_title', 'N/A')[:50]}...")
        print(f"  original_author: {data.get('original_author', 'N/A')}")
        print(f"  original_text length: {len(data.get('original_text', ''))}")
        print(f"  original_text preview: {data.get('original_text', '')[:100]}...")

if __name__ == "__main__":
    asyncio.run(test_api())