"""Test raw_content in workflow result."""

import asyncio
import sys
sys.path.insert(0, "/home/hugo/Forge")

from forge.graph import workflow, create_initial_state

async def test():
    print("=== 创建状态 ===")
    state = create_initial_state("人力资源")
    state["source_platform"] = "wechat"
    state["target_platform"] = "wechat_article"
    state["skip_publish"] = True

    print("=== 运行 workflow ===")
    result = await workflow.ainvoke(state)

    print("\n=== 结果中的所有键 ===")
    for key in result.keys():
        val = result[key]
        if isinstance(val, dict):
            print(f"  {key}: dict with keys {list(val.keys())}")
        elif isinstance(val, str):
            print(f"  {key}: str, len={len(val)}")
        else:
            print(f"  {key}: {type(val).__name__}")

    print("\n=== raw_content 详情 ===")
    raw = result.get("raw_content", {})
    if raw:
        print(f"  title: {raw.get('title', 'N/A')[:50]}...")
        print(f"  author: {raw.get('author', 'N/A')}")
        print(f"  text length: {len(raw.get('text', ''))}")
        print(f"  text preview: {raw.get('text', '')[:100]}...")
    else:
        print("  raw_content is empty or None!")

if __name__ == "__main__":
    asyncio.run(test())