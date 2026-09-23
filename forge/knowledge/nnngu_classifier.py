"""Deterministic classification for content scraped from nnngu.com."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable


STAGE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "correction_supplement",
        ("更正", "补充公告", "补充通知", "延期", "取消", "变更", "调整公告"),
    ),
    (
        "admission_publicity",
        ("拟录用", "录用人员", "录用名单", "录用公示", "政审资料"),
    ),
    (
        "results_and_list",
        ("成绩", "名单", "入围", "合格人员", "考核结果", "排名", "结果公示"),
    ),
    (
        "process_notice",
        (
            "资格审查",
            "资格复审",
            "笔试",
            "面试",
            "体能测试",
            "体测",
            "心理测试",
            "体检",
            "政考",
            "政治考核",
            "准考证",
            "现场确认",
            "岗位适应性测试",
        ),
    ),
    (
        "recruitment_posting",
        ("招聘", "招录", "招聘简章", "招募", "岗位", "职位"),
    ),
)

TOPIC_TITLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fire_emergency", ("消防", "应急救援", "消防员")),
    (
        "medical_on_site",
        ("医院", "院内", "医疗", "医药", "卫生院", "护士", "护理", "药品服务"),
    ),
    ("labor_dispatch", ("劳务派遣", "派遣员工", "派遣制")),
    ("outsourcing_bpo", ("外包", "BPO", "承揽")),
)

# 正文中的泛化词容易描述招聘流程而非业务主题，例如“到指定医院体检”。
# 因此正文只接受能明确表达业务场景的短语，标题仍使用上面的宽规则。
TOPIC_CONTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fire_emergency", ("消防服务", "应急救援服务", "消防救援支队")),
    (
        "medical_on_site",
        (
            "院内服务",
            "医疗服务",
            "医院招聘",
            "医院岗位",
            "卫生院招聘",
            "医护人员",
            "药品服务",
        ),
    ),
    ("labor_dispatch", ("劳务派遣", "派遣员工", "派遣制")),
    ("outsourcing_bpo", ("外包服务", "外包项目", "BPO", "业务流程外包")),
)

MUNICIPALITIES = ("北京", "上海", "天津", "重庆")
PROVINCE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("云南", ("云南",)),
    ("海南", ("海南",)),
    ("湖北", ("湖北",)),
    ("湖南", ("湖南",)),
    ("广东", ("广东",)),
    ("广西", ("广西",)),
    ("四川", ("四川",)),
    ("贵州", ("贵州",)),
    ("江苏", ("江苏",)),
    ("浙江", ("浙江",)),
    ("山东", ("山东",)),
    ("河南", ("河南",)),
    ("河北", ("河北",)),
    ("福建", ("福建",)),
    ("陕西", ("陕西",)),
    ("安徽", ("安徽",)),
    ("江西", ("江西",)),
    ("辽宁", ("辽宁",)),
    ("吉林", ("吉林",)),
    ("黑龙江", ("黑龙江",)),
    ("山西", ("山西",)),
    ("甘肃", ("甘肃",)),
    ("青海", ("青海",)),
    ("宁夏", ("宁夏",)),
    ("新疆", ("新疆",)),
    ("西藏", ("西藏",)),
    ("内蒙古", ("内蒙古",)),
)

CITY_TO_PROVINCE = {
    "昆明": "云南",
    "曲靖": "云南",
    "玉溪": "云南",
    "楚雄": "云南",
    "红河": "云南",
    "大理": "云南",
    "海口": "海南",
    "三亚": "海南",
    "武汉": "湖北",
    "长沙": "湖南",
    "广州": "广东",
    "深圳": "广东",
    "南宁": "广西",
    "成都": "四川",
    "贵阳": "贵州",
    "南京": "江苏",
    "杭州": "浙江",
    "济南": "山东",
    "郑州": "河南",
    "石家庄": "河北",
    "福州": "福建",
    "西安": "陕西",
    "合肥": "安徽",
    "南昌": "江西",
    "沈阳": "辽宁",
    "长春": "吉林",
    "哈尔滨": "黑龙江",
    "太原": "山西",
    "兰州": "甘肃",
}

DISTRICT_TO_REGION = {
    "北辰区": ("天津", "天津"),
    "静海区": ("天津", "天津"),
    "河北区": ("天津", "天津"),
    "滨海新区": ("天津", "天津"),
    "洪山区": ("湖北", "武汉"),
    "湘东区": ("江西", "萍乡"),
    "乐东": ("海南", ""),
    "洋浦": ("海南", ""),
    "马村港": ("海南", "海口"),
}

CLASSIFICATION_FIELDS = (
    "secondary_category",
    "stage_tags",
    "topic_tags",
    "province",
    "city",
    "district",
    "region_raw",
    "published_at",
    "registration_start",
    "registration_end",
    "event_date",
    "validity_status",
)

FULL_DATE_RE = re.compile(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日")
SHORT_DATE_RE = re.compile(r"(?<!\d)(?P<month>\d{1,2})月(?P<day>\d{1,2})日")
ISO_DATE_RE = re.compile(r"(?P<year>20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})")


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term.lower() in text.lower() for term in terms)


def _parse_published_at(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _extract_dates(text: str, default_year: int | None) -> list[date]:
    matches: list[tuple[int, int, date]] = []
    occupied: list[tuple[int, int]] = []

    for pattern in (FULL_DATE_RE, ISO_DATE_RE):
        for match in pattern.finditer(text):
            try:
                parsed = date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
            matches.append((match.start(), match.end(), parsed))
            occupied.append((match.start(), match.end()))

    if default_year:
        for match in SHORT_DATE_RE.finditer(text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            try:
                parsed = date(
                    default_year,
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
            matches.append((match.start(), match.end(), parsed))

    return [item[2] for item in sorted(matches, key=lambda item: item[0])]


def _dates_near_keywords(
    text: str,
    keywords: Iterable[str],
    default_year: int | None,
) -> list[date]:
    dates: list[date] = []
    for keyword in keywords:
        start = 0
        while True:
            position = text.find(keyword, start)
            if position < 0:
                break
            window = text[max(0, position - 40): position + 220]
            dates.extend(_extract_dates(window, default_year))
            start = position + len(keyword)
    return sorted(set(dates))


def _classify_stage(title: str, category: str) -> tuple[str, list[str]]:
    if category != "recruitment":
        return "", []

    tags = [name for name, terms in STAGE_RULES if _contains_any(title, terms)]
    return (tags[0] if tags else "other"), tags


def _classify_topics(title: str, content: str) -> list[str]:
    content_rules = dict(TOPIC_CONTENT_RULES)
    return [
        name
        for name, title_terms in TOPIC_TITLE_RULES
        if _contains_any(title, title_terms)
        or _contains_any(content, content_rules.get(name, ()))
    ]


def _region_mentions(text: str) -> list[tuple[str, str, str, str]]:
    mentions: list[tuple[str, str, str, str]] = []
    for district, (province, city) in DISTRICT_TO_REGION.items():
        if district in text:
            mentions.append((province, city, district, district))
    for municipality in MUNICIPALITIES:
        if municipality in text:
            mentions.append((municipality, municipality, "", municipality))
    for city, province in CITY_TO_PROVINCE.items():
        if city in text:
            mentions.append((province, city, "", city))
    for province, keywords in PROVINCE_KEYWORDS:
        if _contains_any(text, keywords):
            mentions.append((province, "", "", province))
    return list(dict.fromkeys(mentions))


def _extract_region(title: str, content: str, category: str) -> dict[str, str]:
    mentions = _region_mentions(title)
    if not mentions and category == "recruitment":
        mentions = _region_mentions(content[:1200])

    provinces = list(dict.fromkeys(item[0] for item in mentions if item[0]))
    cities = list(dict.fromkeys(item[1] for item in mentions if item[1]))
    districts = list(dict.fromkeys(item[2] for item in mentions if item[2]))
    raw_matches = list(dict.fromkeys(item[3] for item in mentions if item[3]))

    province = provinces[0] if len(provinces) == 1 else ("多地区" if provinces else "")
    city = cities[0] if len(cities) == 1 and len(provinces) == 1 else ""
    district = districts[0] if len(districts) == 1 else ""

    return {
        "province": province,
        "city": city,
        "district": district,
        "region_raw": ",".join(raw_matches),
    }


def _extract_timeline(
    article: dict,
    secondary_category: str,
    reference_date: date,
) -> dict[str, str]:
    published = _parse_published_at(article.get("date", ""))
    year = published.year if published else reference_date.year
    content = article.get("content", "")

    registration_dates = _dates_near_keywords(
        content,
        ("报名时间", "报名日期", "报名截止", "截止时间", "截止"),
        year,
    )
    event_dates = _dates_near_keywords(
        content,
        ("笔试时间", "面试时间", "体检时间", "体测时间", "心理测试", "考试时间"),
        year,
    )

    registration_start = registration_dates[0] if registration_dates else None
    registration_end = registration_dates[-1] if registration_dates else None
    event_date = event_dates[0] if event_dates else None

    category = article.get("category", "")
    combined = f"{article.get('title', '')}\n{content}"
    if category != "recruitment":
        status = "long_term"
    elif _contains_any(combined, ("长期招聘", "长期有效", "长期招募")):
        status = "long_term"
    elif secondary_category in {"results_and_list", "admission_publicity"}:
        status = "expired"
    elif registration_start and registration_start > reference_date:
        status = "upcoming"
    elif registration_end:
        status = "active" if registration_end >= reference_date else "expired"
    elif event_date:
        status = "upcoming" if event_date > reference_date else (
            "active" if event_date == reference_date else "expired"
        )
    elif published and (reference_date - published).days > 180:
        status = "expired"
    else:
        status = "unknown"

    return {
        "published_at": published.isoformat() if published else "",
        "registration_start": registration_start.isoformat() if registration_start else "",
        "registration_end": registration_end.isoformat() if registration_end else "",
        "event_date": event_date.isoformat() if event_date else "",
        "validity_status": status,
    }


def classify_nnngu_article(
    article: dict,
    reference_date: date | None = None,
) -> dict[str, str]:
    """Return stable metadata for one Ruibo website article."""
    reference_date = reference_date or date.today()
    title = article.get("title", "")
    content = article.get("content", "")
    category = article.get("category", "")
    secondary_category, stage_tags = _classify_stage(title, category)
    topic_tags = _classify_topics(title, content)

    result = {
        "secondary_category": secondary_category,
        "stage_tags": ",".join(stage_tags),
        "topic_tags": ",".join(topic_tags),
    }
    result.update(_extract_region(title, content, category))
    result.update(_extract_timeline(article, secondary_category, reference_date))
    return result


__all__ = ["CLASSIFICATION_FIELDS", "classify_nnngu_article"]
