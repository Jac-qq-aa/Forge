"""Initialize knowledge base with Ruibo Group test data.

Run this script to populate the knowledge base with sample data.

Prerequisites:
1. Milvus Docker must be running:
   docker run -d --name milvus-standalone -p 19530:19530 -p 9091:9091 milvusdb/milvus:latest standalone

2. Install dependencies:
   pip install pymilvus sentence-transformers
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge.knowledge import get_knowledge_base, CATEGORIES
from forge.knowledge.config import MILVUS_HOST, MILVUS_PORT, COLLECTION_NAME


# Test data for Ruibo Group (锐博集团)
TEST_DATA = [
    # 公司介绍
    {
        "id": "company_intro_001",
        "content": """锐博集团成立于2010年，是一家专注于人力资源服务的综合性企业集团。
集团总部位于深圳，在全国30多个城市设有分支机构，服务网络覆盖全国主要经济区域。
业务范围涵盖：人力资源外包、招聘流程外包(RPO)、灵活用工、薪酬福利管理、培训发展等全链条人力资源服务。
集团年服务企业超过5000家，管理员工人数超过10万人，是行业内领先的人力资源服务商。""",
        "metadata": {
            "category": "company_intro",
            "title": "公司简介"
        }
    },
    {
        "id": "company_intro_002",
        "content": """锐博集团秉承"以人为本，服务至上"的企业理念，致力于成为企业最值得信赖的人力资源合作伙伴。
集团拥有专业团队超过1000人，其中80%以上具有本科及以上学历，50%拥有人力资源管理师等专业资质。
锐博集团连续多年被评为"中国人力资源服务机构100强"、"广东省诚信示范企业"等荣誉称号。""",
        "metadata": {
            "category": "company_intro",
            "title": "企业理念与荣誉"
        }
    },

    # 招聘信息
    {
        "id": "recruitment_001",
        "content": """【人力资源专员】
岗位职责：
1. 负责客户企业的人员招聘、筛选和入职办理
2. 维护招聘渠道，发布职位信息
3. 组织面试安排，跟进招聘进度

岗位要求：
- 本科及以上学历，人力资源管理相关专业优先
- 1-3年招聘工作经验
- 良好的沟通能力和抗压能力

福利待遇：五险一金、带薪年假、节日福利、职业培训、晋升通道清晰""",
        "metadata": {
            "category": "recruitment",
            "title": "人力资源专员招聘"
        }
    },
    {
        "id": "recruitment_002",
        "content": """【RPO项目经理】
岗位职责：
1. 负责RPO项目的整体运营和管理
2. 组建和管理招聘团队，达成项目交付目标
3. 维护客户关系，挖掘客户需求

岗位要求：
- 本科及以上学历，5年以上人力资源行业经验
- 具备团队管理经验
- 优秀的项目管理和客户服务能力

福利待遇：有竞争力的薪酬、项目奖金、股权激励、年度旅游""",
        "metadata": {
            "category": "recruitment",
            "title": "RPO项目经理招聘"
        }
    },
    {
        "id": "recruitment_003",
        "content": """锐博集团2024校园招聘火热进行中！
面向对象：2024届本科及以上应届毕业生
招聘岗位：人力资源管培生、招聘专员、客户服务专员
培养计划：轮岗实习→定岗培养→管理晋升
我们提供：完善的新人培训、一对一导师指导、快速成长通道
网申地址：www.ruibo.com/campus""",
        "metadata": {
            "category": "recruitment",
            "title": "2024校园招聘"
        }
    },

    # 企业文化
    {
        "id": "culture_001",
        "content": """锐博集团核心价值观：
【专业】以专业能力为客户创造价值，持续学习，精益求精
【诚信】诚实守信，言行一致，建立长期信任关系
【创新】拥抱变化，勇于突破，推动行业进步
【共赢】客户成功、员工成长、企业发展，实现多方共赢""",
        "metadata": {
            "category": "culture",
            "title": "核心价值观"
        }
    },
    {
        "id": "culture_002",
        "content": """锐博集团员工活动丰富多彩：
- 每月生日会：为当月寿星庆祝生日，温馨有爱
- 季度团建：部门组织出游、聚餐，增进团队凝聚力
- 年度旅游：优秀员工享受国内外旅游奖励
- 运动俱乐部：篮球、羽毛球、跑步等兴趣小组
- 志愿者活动：参与公益，回馈社会""",
        "metadata": {
            "category": "culture",
            "title": "员工活动"
        }
    },
    {
        "id": "culture_003",
        "content": """在锐博，我们相信每一位员工都是公司的宝贵财富。
我们提供：
- 完善的培训体系：新人培训、专业技能培训、管理培训
- 清晰的晋升通道：专业路线与管理路线双通道发展
- 有竞争力的薪酬：基本工资+绩效奖金+年终奖
- 全面的福利保障：五险一金、补充医疗、年度体检""",
        "metadata": {
            "category": "culture",
            "title": "员工关怀"
        }
    },

    # 成功案例
    {
        "id": "success_cases_001",
        "content": """【案例】某知名互联网企业批量招聘项目
客户需求：3个月内完成500名技术岗位招聘
解决方案：锐博组建专项招聘团队，采用RPO模式，整合多渠道资源
项目成果：按时完成520人入职，候选人质量达标率95%，客户满意度高分
客户评价："锐博团队专业高效，是我们最可靠的招聘合作伙伴。\"""",
        "metadata": {
            "category": "success_cases",
            "title": "互联网企业批量招聘"
        }
    },
    {
        "id": "success_cases_002",
        "content": """【案例】某制造企业灵活用工项目
客户需求：生产旺季需要临时增加300名生产线工人
解决方案：锐博提供灵活用工解决方案，快速调配人员，灵活管理
项目成果：2周内完成300人到岗，用工成本降低20%，生产效率提升15%
服务亮点：一站式用工管理，降低企业用工风险和成本""",
        "metadata": {
            "category": "success_cases",
            "title": "制造企业灵活用工"
        }
    },
    {
        "id": "success_cases_003",
        "content": """【案例】某上市公司薪酬外包项目
客户需求：解决多地分支机构薪酬计算复杂、效率低的问题
解决方案：锐博提供薪酬外包服务，统一管理全国薪酬核算与发放
项目成果：薪酬核算准确率99.9%，发放效率提升50%，人力成本降低30%
服务优势：专业团队、系统化管理、合规保障""",
        "metadata": {
            "category": "success_cases",
            "title": "上市公司薪酬外包"
        }
    },
]


def init_knowledge_base():
    """Initialize the knowledge base with test data."""
    print("=" * 60)
    print("初始化锐博集团知识库 (Milvus)")
    print("=" * 60)
    print(f"Milvus 地址: {MILVUS_HOST}:{MILVUS_PORT}")
    print(f"Collection: {COLLECTION_NAME}")

    try:
        kb = get_knowledge_base()

        # Clear existing data
        print("\n清空现有数据...")
        kb.clear()

        # Add test data
        print(f"\n添加 {len(TEST_DATA)} 条测试数据...")
        kb.add_documents(TEST_DATA)

        # Verify
        print(f"\n知识库文档总数: {kb.count()}")

        # Test search
        print("\n测试搜索功能:")
        test_queries = ["招聘", "企业文化", "人力资源服务", "成功案例"]
        for query in test_queries:
            results = kb.search(query, n_results=2)
            print(f"\n搜索 '{query}':")
            for r in results:
                print(f"  - [{r['metadata'].get('category')}] {r['metadata'].get('title')}")

        print("\n" + "=" * 60)
        print("知识库初始化完成！")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 初始化失败: {e}")
        print("\n请确保 Milvus Docker 正在运行:")
        print("  docker run -d --name milvus-standalone -p 19530:19530 -p 9091:9091 milvusdb/milvus:latest standalone")
        raise


if __name__ == "__main__":
    init_knowledge_base()