"""招标公告采集：自动化相关招投标信息

数据源（均可在 config/sources.json -> tenders 配置）：
1. type=ccgp_search  中国政府采购网搜索接口（kw 多关键词轮询）
2. type=bing_search  搜索引擎聚合（各地招标平台公告快照，兜底）

单源失败不中断；结果按「优先地区 > 关键词命中」排序。
"""
from urllib.parse import quote

from ..util import extract_date, http_get, match_any, strip_tags

CCGP_URL = (
    "https://search.ccgp.gov.cn/bxsearch?searchtype=1&page_index=1"
    "&bidSort=0&pinMu=0&bidType=0&kw={kw}&start_time={start}&end_time={end}"
)


def _date_range(days: int) -> tuple[str, str]:
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=days)
    return start.strftime("%Y:%m:%d"), end.strftime("%Y:%m:%d")


def _collect_ccgp(source: dict, keywords: list[str]) -> tuple[list[dict], str]:
    """中国政府采购网：按多个关键词各搜一次"""
    start, end = _date_range(int(source.get("days", 2)))
    items: list[dict] = []
    errors: list[str] = []
    for kw in source.get("queries", []):
        url = CCGP_URL.format(kw=quote(kw), start=start, end=end)
        try:
            html = http_get(url, timeout=25)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{kw}: {type(e).__name__}")
            continue
        n0 = len(items)
        for href, text in _iter_ccgp_links(html, url):
            title = strip_tags(text)
            if not title or not match_any(title, keywords):
                continue
            items.append({
                "title": title,
                "url": href,
                "date": extract_date(href, ""),
                "source": f"政府采购网·{kw}",
                "snippet": "",
            })
        if len(items) == n0:  # 该关键词无结果也记录
            errors.append(f"{kw}: 0条")
    if not items and errors and all("0条" not in e for e in errors):
        raise RuntimeError("全部关键词失败: " + "; ".join(errors))
    return items, f"{len(source.get('queries', []))}个关键词, 命中{len(items)}条"


def _iter_ccgp_links(html: str, base_url: str):
    """政府采购网结果链接特征: /cggg/.../t2026xxxx_xxx.htm"""
    from ..util import extract_links

    for href, text in extract_links(html, base_url):
        if "/cggg/" in href and href.endswith(".htm") and len(text) >= 8:
            yield href, text


def _collect_bing(source: dict, keywords: list[str]) -> tuple[list[dict], str]:
    """搜索引擎聚合招标公告（搜狗优先、Bing 兜底）"""
    from ..collectors.search_companies import NEGATIVE_WORDS, web_search

    items: list[dict] = []
    for q in source.get("queries", []):
        for it in web_search(q):
            if not match_any(it["title"] + it["snippet"], keywords):
                continue
            if any(n in it["title"] + it["snippet"] for n in NEGATIVE_WORDS):
                continue
            it["source"] = f"web-search·{q[:20]}"
            it["date"] = extract_date(it["url"], it.get("snippet", ""))
            items.append(it)
    return items, f"{len(source.get('queries', []))}个查询, 命中{len(items)}条"


def collect_tenders(cfg: dict) -> tuple[list[dict], list[dict]]:
    """采集招标公告。返回 (条目, 健康状态)。"""
    if not cfg.get("enabled", True):
        return [], [{"source": "tenders", "ok": False, "note": "已禁用"}]

    keywords = cfg.get("filter_keywords", ["自动化", "机器人", "智能"])
    exclude = cfg.get("exclude_keywords", ["废标", "流标", "终止", "更正"])
    prefer = cfg.get("prefer_regions", ["上海"])

    all_items: list[dict] = []
    health: list[dict] = []
    seen_urls = set()

    handlers = {"ccgp_search": _collect_ccgp, "bing_search": _collect_bing}
    for source in cfg.get("sources", []):
        name = source.get("name", source.get("type", "?"))
        handler = handlers.get(source.get("type"))
        if not handler:
            health.append({"source": name, "ok": False, "note": f"未知类型 {source.get('type')}"})
            continue
        try:
            items, note = handler(source, keywords)
            n_new = 0
            for it in items:
                if it["url"] in seen_urls:
                    continue
                if any(x in it["title"] for x in exclude):
                    continue
                seen_urls.add(it["url"])
                text = it["title"] + it.get("snippet", "")
                it["region"] = "上海" if match_any(text, prefer) else "全国"
                all_items.append(it)
                n_new += 1
            health.append({"source": name, "ok": True, "note": f"{note}，保留{n_new}条"})
        except Exception as e:  # noqa: BLE001
            health.append({"source": name, "ok": False,
                           "note": f"{type(e).__name__}: {e}"})

    # 上海优先，其余按标题关键词密度粗排
    all_items.sort(key=lambda it: (it.get("region") != "上海",))
    return all_items, health
