"""产业园区动态 / 展会会议沙龙 / 区域政府规划 —— 通用搜索聚合采集器

这类信息没有统一官方接口，采用 Bing 搜索聚合：每个板块配置一组查询词，
结果按板块关键词过滤，并打上 category 标签供日报分板块展示。

配置（config/sources.json -> signals）：
{
  "enabled": true,
  "categories": [
    {
      "name": "产业园区动态",
      "queries": ["上海 工业园区 开园", ...],
      "filter_keywords": ["园区", "产业园", ...],
      "exclude_keywords": ["出租", "厂房转租"]
    },
    ...
  ]
}

单板块失败不中断，健康状态如实上报。
"""
from datetime import date, timedelta

from ..util import extract_date, match_any
from .search_companies import NEGATIVE_WORDS, _query_grams, web_search

# 单条结果与查询词的 2-gram 覆盖率下限（查询词已编码板块意图，足够相关就收）
QUERY_RELEVANCE_MIN = 0.34
# 产业动态只关心近期：内容日期早于 N 天的一律丢弃
MAX_AGE_DAYS = 365


def _relevant(text: str, keywords: list[str], q: str) -> bool:
    """关键词命中，或与查询词足够相关（避免因标题不含"展会"等字样被误杀）"""
    if keywords and match_any(text, keywords):
        return True
    grams = _query_grams(q)
    if not grams:
        return False
    return sum(1 for g in grams if g in text) / len(grams) >= QUERY_RELEVANCE_MIN


def _collect_category(cat: dict) -> tuple[list[dict], str]:
    """采集单个板块：逐查询词搜索 -> 相关性过滤"""
    keywords = cat.get("filter_keywords", [])
    exclude = cat.get("exclude_keywords", [])
    items: list[dict] = []
    errors: list[str] = []
    for q in cat.get("queries", []):
        try:
            got = web_search(q)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}")
            continue
        n0 = len(items)
        cutoff = (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()
        for it in got:
            text = it["title"] + " " + it.get("snippet", "")
            if not _relevant(text, keywords, q):
                continue
            if any(n in text for n in NEGATIVE_WORDS) or any(x in text for x in exclude):
                continue
            d = extract_date(it["url"], it.get("snippet", ""))
            if d and d < cutoff:  # 旧闻（如 2024 年的开园报道）直接丢
                continue
            it["source"] = f"{cat.get('name', '?')}·搜索"
            it["date"] = d
            items.append(it)
        if len(items) == n0:
            errors.append("0条")
    note = f"{len(cat.get('queries', []))}个查询, 命中{len(items)}条"
    if not items and errors and all(e == "0条" for e in errors):
        pass  # 查询都成功但无命中，属正常
    return items, note


def collect_signals(cfg: dict) -> tuple[list[dict], list[dict]]:
    """采集园区/展会/规划信息。返回 (条目, 健康状态)。条目含 category 字段。"""
    if not cfg.get("enabled", True):
        return [], [{"source": "signals", "ok": False, "note": "已禁用"}]

    all_items: list[dict] = []
    health: list[dict] = []
    seen_urls = set()

    for cat in cfg.get("categories", []):
        name = cat.get("name", "?")
        try:
            items, note = _collect_category(cat)
            kept = 0
            for it in items:
                if it["url"] in seen_urls:
                    continue
                seen_urls.add(it["url"])
                it["category"] = name
                all_items.append(it)
                kept += 1
            health.append({"source": f"信号·{name}", "ok": True,
                           "note": f"{note}，保留{kept}条"})
        except Exception as e:  # noqa: BLE001
            health.append({"source": f"信号·{name}", "ok": False,
                           "note": f"{type(e).__name__}: {e}"})

    return all_items, health
