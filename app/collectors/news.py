"""新闻采集（每日新增）—— 三个来源，稳定优先

1. 政策/公示：上海市经信委等官方网站（结构化、最可靠）
2. 上海动态：上海本地官方新闻栏目 + 搜索引擎聚合（含"上海"约束）
3. 行业动态：工业自动化垂直媒体栏目页（不强制上海，反映行业趋势）

直连栏目页是稳定基座，搜索引擎作为覆盖面补充（免费引擎对机房 IP 有限流）。
只保留近期内容（默认 30 天），按日期倒序。
"""
from datetime import date, timedelta

from ..util import extract_date, match_any
from .gov_notices import _parse_page, collect_gov_notices
from .search_companies import NEGATIVE_WORDS, _is_junk_url, _is_stale, _query_grams, web_search

# 单条结果与查询词的 2-gram 覆盖率下限（查询词已编码意图，够相关就收）
QUERY_RELEVANCE_MIN = 0.30


def _relevant(text: str, keywords: list[str], q: str) -> bool:
    """关键词命中，或与查询词足够相关（避免标题不含关键词被误杀）"""
    if keywords and match_any(text, keywords):
        return True
    grams = _query_grams(q)
    if not grams:
        return False
    return sum(1 for g in grams if g in text) / len(grams) >= QUERY_RELEVANCE_MIN


def _has_current_year(url: str, title: str) -> bool:
    """URL 或标题里能看出是今年内容（保留给需要严格时效的场景）"""
    y = str(date.today().year)
    return y in url or y in title


def _collect_news_sites(sites: list[dict], keywords: list[str],
                        per_site: int = 10) -> tuple[list[dict], list[dict]]:
    """直连栏目页：通用链接抽取 + 关键词过滤（稳定，不依赖搜索引擎）"""
    items: list[dict] = []
    health: list[dict] = []
    for src in sites:
        name = src.get("name", src.get("url", "?"))
        if not src.get("enabled", True):
            health.append({"source": name, "ok": False, "note": "已禁用"})
            continue
        try:
            from ..util import http_get

            html = http_get(src["url"], timeout=20)
            got = _parse_page(html, src["url"], name)
        except Exception as e:  # noqa: BLE001
            health.append({"source": name, "ok": False,
                           "note": f"抓取失败: {type(e).__name__}"})
            continue

        require = src.get("require", "")
        kept = 0
        for it in got:
            if kept >= per_site:
                break
            title = it["title"]
            if not match_any(title, keywords):
                continue
            if require and require not in title:
                continue
            if _is_stale(title, it["url"]):
                continue
            if any(n in title for n in NEGATIVE_WORDS):
                continue
            it["kind"] = src.get("group", "行业动态")
            it["source"] = name
            items.append(it)
            kept += 1
        health.append({"source": name, "ok": True,
                       "note": f"{len(got)}条链接，命中{kept}条"})
    return items, health


def _collect_news_search(cfg: dict) -> tuple[list[dict], list[dict]]:
    """行业新闻搜索聚合"""
    queries = cfg.get("queries", [])
    keywords = cfg.get("filter_keywords", [])
    exclude = cfg.get("exclude_keywords", [])
    max_age = int(cfg.get("max_age_days", 30))
    cutoff = (date.today() - timedelta(days=max_age)).isoformat()

    items: list[dict] = []
    health: list[dict] = []
    seen_urls: set[str] = set()

    for q in queries:
        try:
            got = web_search(q)
        except Exception as e:  # noqa: BLE001
            health.append({"source": f"新闻搜索「{q[:16]}」", "ok": False,
                           "note": f"搜索失败: {type(e).__name__}"})
            continue
        n_before = len(items)
        for it in got:
            url = it.get("url") or ""
            if not url or url in seen_urls or _is_junk_url(url):
                continue
            text = it["title"] + " " + it.get("snippet", "")
            if not _relevant(text, keywords, q):
                continue
            if any(n in text for n in NEGATIVE_WORDS) or any(x in text for x in exclude):
                continue
            # 旧闻过滤：标题/URL 出现早年（2025 及以前）一律丢弃
            if _is_stale(it["title"], url):
                continue
            d = extract_date(url, it.get("snippet", ""))
            if d and d < cutoff:  # 超出时效窗口
                continue
            # 注：搜索给的是加密跳转链接，采集阶段拿不到真实 URL/日期，
            # 无日期的条目先保留，由 main.py 解析出真实链接后再做第二轮时效判定
            seen_urls.add(url)
            it["kind"] = "上海动态"
            it["date"] = d
            it["source"] = "新闻搜索"
            items.append(it)
        health.append({"source": f"新闻搜索「{q[:16]}」", "ok": True,
                       "note": f"结果{len(got)}条，新增{len(items) - n_before}条"})

    return items, health


def collect_news(news_cfg: dict, gov_sites: list[dict],
                 automation_keywords: list[str]) -> tuple[list[dict], list[dict]]:
    """采集每日新闻。条目 kind 字段：政策公示 / 上海动态 / 行业动态。"""
    items: list[dict] = []
    health: list[dict] = []
    max_age = int(news_cfg.get("max_age_days", 30))
    cutoff = (date.today() - timedelta(days=max_age)).isoformat()

    # ---------- 1. 政府公示/政策 ----------
    if gov_sites:
        gov_items, gov_health = collect_gov_notices(gov_sites, automation_keywords)
        kept = 0
        for it in gov_items:
            d = it.get("date") or ""
            if d and d < cutoff:  # 公示页里挂着的旧文件不算"今天新发的"
                continue
            it["kind"] = "政策公示"
            items.append(it)
            kept += 1
        if kept != len(gov_items):
            gov_health.append({"source": "政策公示·时效过滤", "ok": True,
                               "note": f"剔除{len(gov_items) - kept}条超{max_age}天的旧公示"})
        health.extend(gov_health)

    # ---------- 2. 直连新闻栏目页（稳定基座） ----------
    site_kw = news_cfg.get("site_keywords") or news_cfg.get("filter_keywords", [])
    if news_cfg.get("news_sites"):
        site_items, site_health = _collect_news_sites(news_cfg["news_sites"], site_kw)
        items.extend(site_items)
        health.extend(site_health)

    # ---------- 3. 搜索引擎聚合（覆盖面补充） ----------
    if news_cfg.get("enabled", True):
        news_items, news_health = _collect_news_search(news_cfg)
        items.extend(news_items)
        health.extend(news_health)
    else:
        health.append({"source": "行业新闻搜索", "ok": False, "note": "已禁用"})

    # ---------- 4. 去重 + 按日期倒序 ----------
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        uniq.append(it)
    uniq.sort(key=lambda it: (it.get("date") or "0000-00-00"), reverse=True)
    return uniq, health
