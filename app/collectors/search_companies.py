"""新注册自动化公司线索：免费渠道（网页搜索聚合，纯标准库）"""
import html as _html
import re
import time
from urllib.parse import quote

from ..util import extract_date, http_get, match_any, strip_tags
from .company_scorer import enrich_scope, score_company

BING_URL = "https://www.bing.com/search?q={q}&setlang=zh-CN&count=20"
SO_GOU_URL = "https://www.sogou.com/web?query={q}"
SO_360_URL = "https://www.so.com/s?q={q}"
DDG_URL = "https://html.duckduckgo.com/html/?q={q}"
# 过滤掉明显无关的结果（含搜索引擎降级时的通用填充页）
NEGATIVE_WORDS = ("招聘", "求职", "试题", "培训广告", "下载", "课件",
                  "旅游攻略", "旅游景点", "自由行", "百度百科", "酒店",
                  # 查询回显页 / 教程文档，不是企业实体
                  "怎样注册", "注册流程", "注册材料", "注册条件", "360地图",
                  "注册公司", "需要哪些材料", "办理流程", "流程介绍",
                  "操作指南", "进一步探索",
                  "最新相关消息", "相关消息", "短视频", "文档")

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

# 明显无业务价值的域名：文档分享站、短视频聚合、百科问答、搜索自身聚合页
JUNK_DOMAINS = ("tv.360kan.com", "360kan.com", "renrendoc.com", "docin.com",
                "wenku.baidu.com", "baike.baidu.com", "zhidao.baidu.com",
                "douyin.com", "kuaishou.com", "xiaohongshu.com", "zhihu.com/question",
                "book118.com", "doc88.com", "360doc.com", "mbalib.com",
                "toutiao.com/topic", "baike.sogou.com",
                # 搜索引擎自身的结果聚合页（"XX的最新相关消息"）
                "news.so.com/ns", "so.com/ns?", "jobui.com/rank",
                # 企业信息站的"新闻舆情/动态"列表页，不是单条新闻
                "aiqicha.baidu.com/company_news", "cechina.cn/Company/",
                # 百科/荣誉榜单页
                "baike.so.com", "cbi360.net",
                # 招商/招标聚合、公司注册代办类站点
                "zhaobiao.cn", "11467.com/info", "chenyangling.com", "71a.com",
                # 企业产品/留言页、投融资数据聚合页（不是新闻）
                "messagedetail.aspx", "zdb.pedaily.cn")

# 过时内容：标题里出现早年（含不带"年"的写法，如"2024智能制造大会"）
_STALE_YEARS = ("2018年", "2019年", "2020年", "2021年", "2022年", "2023年", "2024年")
_STALE_YEAR_RE = re.compile(r"20(1[5-9]|2[0-5])")
# URL 路径里的年份（如 /2024/09/02/、/20240311/）
_STALE_URL_YEAR = re.compile(r"/(201[5-9]|202[0-5])[/_-]?")
_STALE_URL_COMPACT = re.compile(r"(?:^|[/_.-])20(?:1[5-9]|2[0-5])(?:0[1-9]|1[0-2])")


def _is_stale(title: str, url: str = "") -> bool:
    """判断是否为过时内容（当前年份为 2026，标题/URL 出现 2015-2025 即视为旧闻）"""
    if any(y in title for y in _STALE_YEARS):
        return True
    if _STALE_YEAR_RE.search(title):
        return True
    if url and (_STALE_URL_YEAR.search(url) or _STALE_URL_COMPACT.search(url)):
        return True
    return False


def _is_junk_url(url: str) -> bool:
    return any(d in url for d in JUNK_DOMAINS)


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


def _parse_ddg(html_text: str) -> list[dict]:
    """解析 DuckDuckGo HTML 版结果页（海外机房 IP 更稳定）"""
    results = []
    blocks = re.split(r'(?=<div class="result results_links)', html_text)
    for block in blocks:
        m = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not m:
            continue
        url = _html.unescape(m.group(1)).strip()
        # DDG 有时用跳转链接 //duckduckgo.com/l/?uddg=<encoded>
        if "uddg=" in url:
            from urllib.parse import unquote, urlparse, parse_qs

            qs = parse_qs(urlparse(url if url.startswith("http") else "https:" + url).query)
            url = unquote(qs.get("uddg", [url])[0])
        title = strip_tags(m.group(2)).strip()
        if not title or len(title) < 8 or url.startswith(("javascript:", "#")):
            continue
        snippet = ""
        sm = re.search(r'class="result__snippet"[^>]*>([\s\S]*?)</a>', block)
        if sm:
            snippet = strip_tags(sm.group(1))[:200]
        results.append({"title": title, "url": url, "snippet": snippet, "date": ""})
    return results


GENERIC_TOKENS = {"上海", "2026", "2025", "公司", "最新", "报名", "公告", "信息"}
SCORE_THRESHOLD = 0.18  # 结果与查询词的 2-gram 覆盖率低于此值视为降级页


def _query_grams(q: str) -> set[str]:
    """把查询词切成有区分度的 2-gram（剔除城市/年份等通用词，避免误判）"""
    toks = [t for t in re.split(r"[\s，,、/]+", q)
            if len(t) >= 2 and t not in GENERIC_TOKENS]
    grams: set[str] = set()
    for t in toks:
        grams.update(t[i:i + 2] for i in range(len(t) - 1))
    return grams


def _looks_degraded(res: list[dict], q: str = "") -> bool:
    """判断结果是否为搜索引擎降级时的通用填充页。

    降级页的典型特征：结果条数少、或内容与查询词完全无关（如查"临港产业规划"
    却返回"上海旅游"）。因此除了垃圾词比例，还要看查询词 2-gram 覆盖率。
    """
    if not res:
        return True
    junk = sum(1 for r in res if any(h in r["title"] for h in _JUNK_HINTS))
    if junk / len(res) > 0.5:
        return True
    grams = _query_grams(q)
    if not grams:
        return len(res) < 3
    best = 0.0
    for r in res:
        text = r["title"] + " " + r.get("snippet", "")
        best = max(best, sum(1 for g in grams if g in text) / len(grams))
    return best < SCORE_THRESHOLD


# 引擎轮换起点：每次查询换一个引擎，分散反爬压力
# 注：_parse_bing 定义在本文件后部，这里用名称引用避免前向依赖
_engine_idx = 0
_engine_down: dict[str, float] = {}  # 引擎名 -> 冷却截止时间（monotonic 秒）
ENGINE_DOWN_SEC = 45   # 单引擎失败后的冷却时长（要短于整次运行，否则后半段全被跳过）
QUERY_GAP_SEC = 5.0    # 查询间隔，降低触发反爬概率（太密会被搜索引擎限流）
_fail_streak = 0       # 连续失败次数：连续多次全引擎失败后停止等待重试（熔断）
FAIL_STREAK_LIMIT = 2
SEARCH_BUDGET_SEC = 480  # 全部搜索类采集的总时间预算（防止坏网络下拖超 Actions 超时）
_budget_start: float | None = None


def _budget_left() -> float:
    global _budget_start
    if _budget_start is None:
        _budget_start = time.monotonic()
    return SEARCH_BUDGET_SEC - (time.monotonic() - _budget_start)


def _engines() -> tuple:
    return (("sogou", SO_GOU_URL, _parse_sogou),
            ("so360", SO_360_URL, _parse_360),
            ("bing", BING_URL, _parse_bing),
            ("ddg", DDG_URL, _parse_ddg))


def web_search(q: str, count: int = 12, retry_wait: bool = True) -> list[dict]:
    """统一搜索入口：多引擎轮换，单引擎降级只冷却自己，不影响其他引擎。

    如果全部引擎都在冷却中，会等到最早恢复的那个（最多 ENGINE_DOWN_SEC 秒）
    再重试一轮，避免"一次失败导致整次运行后半段全废"。
    """
    global _engine_idx, _fail_streak
    if _budget_left() <= 0:
        raise RuntimeError(f"搜索总预算 {SEARCH_BUDGET_SEC}s 已用完，跳过本次查询")
    engines = _engines()
    last_err: Exception | None = None
    for attempt in range(2):
        now = time.monotonic()
        for i in range(len(engines)):
            idx = (_engine_idx + i) % len(engines)
            name, tpl, parser = engines[idx]
            if _engine_down.get(name, 0) > now:
                continue
            try:
                time.sleep(QUERY_GAP_SEC if i == 0 else 1.2)
                res = parser(http_get(tpl.format(q=quote(q)), timeout=12))
                res = [r for r in res if not _is_junk_url(r["url"])
                       and not _is_stale(r["title"], r["url"])]
                if _looks_degraded(res, q):
                    _engine_down[name] = time.monotonic() + ENGINE_DOWN_SEC
                    last_err = RuntimeError(f"{name} 返回降级结果")
                    continue
                _engine_idx = (idx + 1) % len(engines)  # 成功的引擎下次优先换下一个
                _fail_streak = 0
                return res[:count]
            except Exception as e:  # noqa: BLE001
                _engine_down[name] = time.monotonic() + ENGINE_DOWN_SEC
                last_err = e
        # 全部引擎不可用：等最早恢复的那个再试一次（连续失败过多则熔断，不再空等）
        if attempt == 0 and retry_wait and _fail_streak < FAIL_STREAK_LIMIT:
            recovery = min((_engine_down.get(n, 0) for n, _, _ in engines), default=0)
            wait = recovery - time.monotonic()
            if 0 < wait <= ENGINE_DOWN_SEC:
                time.sleep(wait + 1)
                continue
        break
    _fail_streak += 1
    raise RuntimeError(f"所有搜索引擎均不可用或被降级（最后错误: {last_err}）")


def collect_new_companies(cfg: dict) -> tuple[list[dict], list[dict]]:
    """用搜索聚合"上海新注册自动化公司"线索。返回 (条目, 健康状态)。

    免费渠道的局限：只能拿到网页里提到的注册信息，覆盖不全。
    配置企查查 API Key 后（qcc_api.py）可获得准确数据。
    """
    if not cfg.get("enabled", True):
        return [], [{"source": "web-search", "ok": False, "note": "已禁用"}]

    items: list[dict] = []
    health: list[dict] = []
    seen_urls = set()
    entity_words = cfg.get("entity_words", ["公司", "企业", "厂"])
    exclude_words = cfg.get("exclude_keywords", [])
    max_age_days = int(cfg.get("max_age_days", 120))
    cutoff = ""
    if max_age_days > 0:
        from datetime import date, timedelta

        cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()

    for q in cfg.get("queries", []):
        try:
            got = web_search(q)
            filt = cfg.get("filter_keywords", [])
            n_hit = 0
            for it in got:
                url = it.get("url") or ""
                if not url or url in seen_urls or _is_junk_url(url):
                    continue
                title = it["title"]
                # 企业线索必须是"公司实体"页面，排除行业新闻/教程
                if not any(w in title for w in entity_words):
                    continue
                if not match_any(title, filt) and not match_any(it.get("snippet", ""), filt):
                    continue
                if any(n in title + it.get("snippet", "") for n in NEGATIVE_WORDS):
                    continue
                if any(x in title for x in exclude_words):  # 荣誉榜单/年报等非新增线索
                    continue
                if _is_stale(title, url):  # 旧闻/旧公告
                    continue
                d = extract_date(url, it.get("snippet", ""))
                if d and cutoff and d < cutoff:  # 超出时效窗口
                    continue
                seen_urls.add(url)
                it["date"] = d
                it["source"] = f"web-search「{q}」"
                items.append(it)
                n_hit += 1
            health.append({"source": f"搜索「{q[:14]}」", "ok": True,
                           "note": f"结果{len(got)}条，命中{n_hit}条"})
        except Exception as e:  # noqa: BLE001
            health.append({"source": f"搜索「{q[:14]}」", "ok": False,
                           "note": f"搜索失败: {type(e).__name__}"})

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
