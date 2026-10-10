import json
from pathlib import Path

import pytest

from forge.cli import _validate_official_source_url


def test_hr_seed_contains_verified_first_batch() -> None:
    path = Path(__file__).parents[1] / "seed" / "hr_business_terms.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["terms"]) == 27
    assert len(data["obligations"]) >= 10
    assert len(data["classifications"]) >= 9
    source_keys = {item["key"] for item in data["sources"]}
    assert all(item["source_key"] in source_keys for item in data["terms"])
    assert all(item["source_key"] in source_keys for item in data["obligations"])
    assert all(item["source_key"] in source_keys for item in data["classifications"])
    assert all(_validate_official_source_url(item["source_url"]) for item in data["sources"])


@pytest.mark.parametrize(
    "url",
    [
        "http://www.gov.cn/not-https",
        "https://example.com/article",
        "https://gov.cn.example.com/lookalike",
    ],
)
def test_hr_seed_rejects_non_official_sources(url: str) -> None:
    with pytest.raises(ValueError):
        _validate_official_source_url(url)
