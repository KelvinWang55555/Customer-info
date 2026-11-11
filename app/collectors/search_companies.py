"""新注册自动化公司线索：免费渠道（Bing 网页搜索聚合，纯标准库）"""
import re
from urllib.parse import quote

from ..util import http_get, match_any, strip_tags

BING_URL = "https://www.bing.com/search?q={q}&setlang=zh-CN&count=20"
# 过滤掉明显无关的结果
NEGATIVE_WORDS = ("招聘", "求职", "试题", "培训广告", "下载", "课件")

# Bing 结果块: <li class="b_algo"><h2><a href="...">标题</a></h2> ... <p>摘要</p>
RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<p[^>]*>(.*?)</p>',
    re.S)


def collect_new_companies(cfg: dict) -> tuple[list[dict], list[dict]]:
    """用 Bing 搜索聚合"上海新注册自动化公司"线索。返回 (条目, 健康状态)。

    免费渠道的局限：只能拿到网页里提到的注册信息，覆盖不全。
    配置企查查 API Key 后（qcc_api.py）可获得准确数据。
    """
    if not cfg.get("enabled", True):
        return [], [{"source": "web-search", "ok": False, "note": "已禁用"}]

    items: list[dict] = []
    health: list[dict] = []
    seen_urls = set()

    for q in cfg.get("queries", []):
        url = BING_URL.format(q=quote(q))
        try:
            html = http_get(url)
            got = _parse_bing(html)
            filt = cfg.get("filter_keywords", [])
            n_hit = 0
            for it in got:
                if it["url"] in seen_urls:
                    continue
                if not match_any(it["title"], filt) and not match_any(it["snippet"], filt):
                    continue
                if any(n in it["title"] + it["snippet"] for n in NEGATIVE_WORDS):
                    continue
                seen_urls.add(it["url"])
                it["source"] = f"web-search「{q}」"
                items.append(it)
                n_hit += 1
            health.append({"source": f"web-search「{q}」", "ok": True,
                           "note": f"搜索结果{len(got)}条，命中{n_hit}条"})
        except Exception as e:  # noqa: BLE001
            health.append({"source": f"web-search「{q}」", "ok": False,
                           "note": f"搜索失败: {type(e).__name__}: {e}"})

    return items, health


def _parse_bing(html: str) -> list[dict]:
    results = []
    for m in RESULT_RE.finditer(html):
        url, title_html, snip_html = m.group(1), m.group(2), m.group(3)
        title = strip_tags(title_html)
        snippet = strip_tags(snip_html)[:200]
        if not title or url.startswith(("javascript:", "#")):
            continue
        results.append({"title": title, "url": url, "snippet": snippet, "date": ""})
    return results
