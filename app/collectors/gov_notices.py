"""政府网站公示公告采集器（通用 HTML 解析，不依赖具体站点结构，纯标准库）"""
from ..util import extract_date, extract_links, http_get, match_any

# 这些链接是导航/栏目页，不是公告正文
NOISE_HREFS = ("index.html", "javascript:", "#", "map/", "/sybz/", "mailto:")


def collect_gov_notices(sources: list[dict], keywords: list[str]) -> tuple[list[dict], list[dict]]:
    """抓取各公示页。返回 (命中条目, 数据源健康状态)。

    每条: {source, title, url, date}
    单个源失败只记 warning，不影响其他源。
    """
    items: list[dict] = []
    health: list[dict] = []

    for src in sources:
        name, url = src.get("name", src["url"]), src["url"]
        if not src.get("enabled", True):
            health.append({"source": name, "ok": False, "note": "已禁用"})
            continue
        try:
            html = http_get(url)
            got = _parse_page(html, url, name)
            hit = [it for it in got if match_any(it["title"], keywords)]
            items.extend(hit)
            health.append({"source": name, "ok": True,
                           "note": f"共{len(got)}条链接，命中{len(hit)}条"})
        except Exception as e:  # noqa: BLE001 网站改版/临时不可达都不应中断整体
            health.append({"source": name, "ok": False,
                           "note": f"抓取失败: {type(e).__name__}: {e}"})

    return items, health


def _parse_page(html: str, base_url: str, source_name: str) -> list[dict]:
    items = []
    seen_urls = set()

    for href, title in extract_links(html, base_url):
        if not title or len(title) < 8:          # 太短的基本是导航
            continue
        if any(h in href for h in NOISE_HREFS):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # 日期优先取 URL 里的 YYYYMMDD，其次取链接周边文本（这里用页面自身没有父级，取 URL 为准）
        date = extract_date(href, "")
        items.append({"source": source_name, "title": title, "url": href, "date": date})

    return items
