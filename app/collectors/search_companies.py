"""新注册自动化公司线索：免费渠道（网页搜索聚合，纯标准库）"""
import html as _html
import re
import time
from urllib.parse import quote

from ..util import http_get, match_any, strip_tags
from .company_scorer import enrich_scope, score_company

BING_URL = "https://www.bing.com/search?q={q}&setlang=zh-CN&count=20"
SO_GOU_URL = "https://www.sogou.com/web?query={q}"
SO_360_URL = "https://www.so.com/s?q={q}"
# 过滤掉明显无关的结果（含搜索引擎降级时的通用填充页）
NEGATIVE_WORDS = ("招聘", "求职", "试题", "培训广告", "下载", "课件",
                  "旅游攻略", "旅游景点", "自由行", "百度百科", "酒店")

# Bing 结果块: <li class="b_algo"><h2><a href="...">标题</a></h2> ... <p>摘要</p>
RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<p[^>]*>(.*?)</p>',
    re.S)

# 搜狗/360 结果块: <h3> <a href="...">标题</a></h3>
_H3_A_RE = re.compile(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>')
_SOGOU_SNIPPET_RE = re.compile(r'<(?:p|div)[^>]*class="[^"]*(?:fz-mid|text-layout|space-txt|cite-width)[^"]*"[^>]*>([\s\S]*?)</(?:p|div)>')

# 搜索引擎被限流时返回的通用填充页特征
_JUNK_HINTS = ("百度百科", "旅游攻略", "Tripadvisor", "上海市人民政府",
               "旅游景点", "自由行", "旅行指南")


def _parse_sogou(html_text: str) -> list[dict]:
    """解析搜狗搜索结果页（按 vrwrap 结果块切分，兼容多版式）"""
    results = []
    for block in re.split(r'(?=<div class="vrwrap")', html_text):
        m = _H3_A_RE.search(block)
        if not m:
            continue
        url = _html.unescape(m.group(1)).strip()
        title = strip_tags(m.group(2)).strip()
        if not title or len(title) < 8 or url.startswith(("javascript:", "#")):
            continue
        if url.startswith("/link"):
            url = "https://www.sogou.com" + url
        snippet = ""
        sm = _SOGOU_SNIPPET_RE.search(block[m.end():])
        if sm:
            snippet = strip_tags(sm.group(1))[:200]
        results.append({"title": title, "url": url, "snippet": snippet, "date": ""})
    return results


def _parse_360(html_text: str) -> list[dict]:
    """解析 360 搜索结果页"""
    results = []
    for u, t in _H3_A_RE.findall(html_text):
        title = strip_tags(t).strip()
        if not title or len(title) < 8:
            continue
        if u.startswith(("https://image.so.com", "https://wenku.so.com",
                         "https://v.so.com", "https://news.so.com", "https://map.so.com")):
            continue
        results.append({"title": title, "url": u, "snippet": "", "date": ""})
    return results


def _looks_degraded(res: list[dict]) -> bool:
    """判断结果是否为搜索引擎降级时的通用填充页"""
    if len(res) < 3:
        return True
    junk = sum(1 for r in res if any(h in r["title"] for h in _JUNK_HINTS))
    return junk / len(res) > 0.4


# 引擎轮换起点：每次查询换一个引擎，分散反爬压力
# 注：_parse_bing 定义在本文件后部，这里用名称引用避免前向依赖
_engine_idx = 0


def _engines() -> tuple:
    return (("sogou", SO_GOU_URL, _parse_sogou),
            ("so360", SO_360_URL, _parse_360),
            ("bing", BING_URL, _parse_bing))


def web_search(q: str, count: int = 12, cooldown: int = 30) -> list[dict]:
    """统一搜索入口：sogou/360/bing 三引擎轮换，降级页自动冷却后换引擎重试。

    搜索引擎对高频查询会返回通用填充页（结果与查询词无关），通过特征检测
    识别降级页，冷却 cooldown 秒后切换引擎重试。
    """
    global _engine_idx
    engines = _engines()
    last_err: Exception | None = None
    for i in range(len(engines)):
        name, tpl, parser = engines[(_engine_idx + i) % len(engines)]
        try:
            time.sleep(4)  # 查询间隔，降低触发反爬的概率
            res = parser(http_get(tpl.format(q=quote(q))))
            if _looks_degraded(res):
                last_err = RuntimeError(f"{name} 返回降级结果")
                time.sleep(cooldown)  # 冷却后再换引擎
                continue
            _engine_idx = (_engine_idx + i + 1) % len(engines)
            return res[:count]
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"所有搜索引擎均失败或被降级服务（最后错误: {last_err})")


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
        try:
            got = web_search(q)
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


def apply_scoring(items: list[dict], cfg: dict) -> list[dict]:
    """对候选公司做多维评分（标题+摘要+经营范围快照），按相关度排序。

    cfg 可含 scorer.keywords（覆盖默认词库）、scorer.enrich（bool，二次检索）。
    低于 C 级的条目被丢弃。
    """
    scorer_cfg = cfg.get("scorer", {}) or {}
    if scorer_cfg.get("enrich") and items:
        enrich_scope(items, top_n=int(scorer_cfg.get("enrich_top", 5)))
    kw = scorer_cfg.get("keywords")  # None -> 用默认词库
    scored: list[dict] = []
    for it in items:
        result = score_company(it, kw)
        if result:
            it["score"] = result["score"]
            it["grade"] = result["grade"]
            it["matched"] = result["matched"]
            scored.append(it)
    scored.sort(key=lambda it: (-it["score"],))
    return scored


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
