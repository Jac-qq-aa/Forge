"""Test full WeChat API flow."""

import asyncio
import sys
sys.path.insert(0, "/home/hugo/Forge")

async def test_api_flow():
    # 模拟 Web API 流程
    from forge.graph import workflow, create_initial_state

    source_url = "https://weixin.sogou.com/link?url=dn9a_-gY295K0Rci_xozVXfdMkSQTLW6cwJThYulHEtVjXrGTiVgS3KZKBG3T9-gQYMjgjCSTVqGqn3WhpcTb0VqXa8Fplpd9AwsytHsQc3JAPN41C0ymKIgio9mTKFJJXdjBXSWN3YjAuiI476GutxGMS2rlkC76O4y90QTOf6ZTVfZxoip7vpCmWSvEHn87JdGFOGHeXy8lqjeIq5zO4F5EDwz3d92lPdgCr9manCHiVbBL6ZqWiGiUiFf9SPXyghqUu0gB3tXRtmyIGw2VAg..&type=2&query=%E4%BA%BA%E5%8A%9B%E8%B5%84%E6%BA%90&token=7F9E4AA9FE8BECF2FBFDB4EEFAD3A594FC38D0C969D89494"
    source_platform = "wechat"
    target_platform = "wechat_article"

    print("=== 创建初始状态 ===")
    state = create_initial_state(source_url)
    state["source_platform"] = source_platform
    state["target_platform"] = target_platform
    state["skip_publish"] = True

    print(f"topic: {state.get('topic', '')[:80]}...")
    print(f"source_platform: {state.get('source_platform')}")
    print(f"target_platform: {state.get('target_platform')}")

    print("\n=== 运行 workflow ===")
    try:
        result = await workflow.ainvoke(state)
        print("\n=== 结果 ===")
        raw_content = result.get("raw_content", {})
        print(f"标题: {raw_content.get('title', 'N/A')}")
        print(f"作者: {raw_content.get('author', 'N/A')}")
        print(f"内容长度: {len(raw_content.get('text', ''))}")

        rewritten = result.get("rewritten_draft", "")
        print(f"\n改写后内容长度: {len(rewritten)}")
        print(f"改写预览: {rewritten[:300]}...")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_api_flow())