"""企查查系工商数据直连（免费通道，纯标准库）。

按优先级尝试三个通道，产出统一的"新注册企业"条目：
  A. 爱企查（aiqicha.baidu.com，百度旗下工商数据平台）搜索 XHR
  B. 企查查公开搜索页（www.qcc.com/web/search）HTML 解析
  C. 企查查官方 API（见 qcc_api.py，需付费 Key，单独模块）

条目字段统一为：
  title=公司名  url=详情页  date=成立日期  reg_cap=注册资本
  legal=法定代表人  scope=经营范围/行业  snippet=地址等补充信息
  source=通道名

若爱企查触发风控（status=-14 验证码）或企查查要求登录，则该通道
记入 health 失败并尝试下一通道；全部失败时上层会回退到搜索聚合。
"""
import re
import time
import urllib.parse
import urllib.request
import http.cookiejar

from ..util import today


# ---------------- 通用工具 ----------------

def _txt(v) -> str:
    """爱企查字段可能是 str 或 {'text': ...} / {'name': ...} 结构，统一转文本"""
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or "").strip()
    if isinstance(v, (list, tuple)):
        return " ".join(_txt(x) for x in v).strip()
    return str(v or "").strip()


def _norm_date(s: str) -> str:
    """把 '2026年11月5日' / '2026-11-05' / '2026.11.05' 归一为 YYYY-MM-DD"""
    m = re.search(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})", str(s or ""))
    if not m:
        return ""
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _session():
    """带 Cookie 的会话（先访问首页拿 BAIDUID）"""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        ("Referer", "https://aiqicha.baidu.com/"),
        ("Accept-Language", "zh-CN,zh;q=0.9"),
    ]
    try:
        op.open("https://aiqicha.baidu.com/", timeout=15).read(4096)
    except Exception:
        pass
    return op


# ---------------- 通道A：爱企查 ----------------

_AQ_ENDPOINTS = (
    "https://aiqicha.baidu.com/s/advanceFilterAdv?q={q}&p={p}&s={s}&t=0",
    "https://aiqicha.baidu.com/s/advanceFilterAdv?p={p}&s={s}&t=0&q={q}",
)


def _aq_request(op, url: str) -> str:
    req = urllib.request.Request(url, headers={
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    })
    return op.open(req, timeout=15).read().decode("utf-8", errors="replace")


def _extract_list(data) -> list:
    """从各种可能的返回结构里抠出结果列表"""
    def dig(d):
        for k in ("result", "Result", "list", "resultList", "resultData"):
            if isinstance(d.get(k), list):
                return d[k]
        return []
    if isinstance(data, dict):
        out = dig(data)
        if out:
            return out
        inner = data.get("data") or data.get("Data") or {}
        if isinstance(inner, dict):
            return dig(inner)
    return []


def aiqicha_search(op, queries: list[str], pages: int = 2,
                   gap: float = 2.5) -> tuple[list[dict], str]:
    """通道A。返回 (条目, 通道结论)；条目为空时结论说明原因"""
    items: list[dict] = []
    captcha_hit = False
    got_json = 0
    for q in queries:
        for p in range(1, pages + 1):
            text = ""
            for ep in _AQ_ENDPOINTS:
                url = ep.format(q=urllib.parse.quote(q), p=p, s=(p - 1) * 10)
                try:
                    text = _aq_request(op, url)
                except Exception:
                    text = ""
                if text:
                    break
            if not text:
                continue
            got_json += 1
            try:
                data = __import__("json").loads(text)
            except Exception:
                continue
            status = data.get("status") if isinstance(data, dict) else None
            if status == -14 or "captcha" in text[:500]:
                captcha_hit = True
                continue
            for r in _extract_list(data):
                if not isinstance(r, dict):
                    continue
                name = _txt(r.get("name") or r.get("title"))
                if not name or "公司" not in name and "厂" not in name and "企业" not in name:
                    continue
                pid = _txt(r.get("pid"))
                d = _norm_date(_txt(r.get("startDate") or r.get("esDate") or r.get("setupDate")))
                prov = _txt(r.get("provinceName") or r.get("province") or "")
                city = _txt(r.get("cityName") or r.get("city") or "")
                addr = _txt(r.get("address"))
                scope = _txt(r.get("industrys") or r.get("industry") or r.get("businessScope"))
                if "上海" not in (prov + city + addr + name):
                    continue
                items.append({
                    "title": name,
                    "url": f"https://aiqicha.baidu.com/company_detail_{pid}" if pid else "",
                    "date": d,
                    "reg_cap": _txt(r.get("regCap") or r.get("regCapital")),
                    "legal": _txt(r.get("legalPerson") or r.get("frName")),
                    "scope": scope,
                    "snippet": " ".join(x for x in (addr, scope) if x),
                    "source": "爱企查",
                })
            time.sleep(gap)
    note = f"返回{len(items)}条"
    if captcha_hit:
        note += "（部分请求触发验证码）"
    if got_json == 0:
        note = "接口无响应（可能被风控拦截）"
    return items, note


# ---------------- 通道B：企查查公开搜索页 ----------------

def qcc_web_search(queries: list[str]) -> tuple[list[dict], str]:
    """企查查网页版搜索（未登录时可看少量结果；跳登录则放弃）"""
    items: list[dict] = []
    login_wall = False
    try:
        op = _session()
    except Exception:
        op = None
    for q in queries:
        url = ("https://www.qcc.com/web/search?key=" + urllib.parse.quote(q))
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Referer": "https://www.qcc.com/",
            })
            html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
        except Exception:
            continue
        if re.search(r"(请登录|验证码|访问过于频繁)", html[:6000]):
            login_wall = True
            continue
        for m in re.finditer(
                r'firm/([0-9a-f]{16,40})\.html[^>]*>(?:<[^>]+>)*([^<>{}]{4,60})', html):
            name = m.group(2).strip()
            if "公司" not in name:
                continue
            items.append({
                "title": name,
                "url": f"https://www.qcc.com/firm/{m.group(1)}.html",
                "date": "",
                "snippet": q,
                "source": "企查查网页",
            })
        time.sleep(2.5)
    note = f"返回{len(items)}条"
    if login_wall:
        note += "（触发登录/验证码墙）"
    return items, note


# ---------------- 对外入口 ----------------

# 工商详情页特征（搜索兜底结果只保留这类页面，剔除纯新闻页）
REGISTRY_URL_HINTS = ("qcc.com/firm/", "aiqicha.baidu.com", "tianyancha.com/firm",
                      "qixin.com/company", "shuidi.cn/company", "11467.com/",
                      "qichacha.com/firm")


def is_registry_page(url: str) -> bool:
    return any(h in (url or "") for h in REGISTRY_URL_HINTS)


# 详情页里抓工商字段的正则（页面结构不同，多写几种兜底）
_RE_CAP = re.compile(r"注册资本[^0-9]{0,40}([\d,.]+\s*万?万?\s*(?:人民币|元)?)")
_RE_EST = re.compile(r"(?:成立日期|成立于|注册日期)[^0-9]{0,20}(20\d{2}[年\-/.]\d{1,2}[月\-/.]?\d{0,2})")
_RE_LEGAL = re.compile(r"法定代表人[^一-龥]{0,24}([一-龥·]{2,5})")


def enrich_registry(items: list[dict], probe_budget: int = 15) -> None:
    """对工商详情页尽力抓取 注册资本/成立日期/法定代表人（页面是 JS 壳或要求登录时静默跳过）。"""
    from ..util import http_get

    for it in items:
        if probe_budget <= 0:
            break
        url = it.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        probe_budget -= 1
        try:
            html = http_get(url, timeout=12)
        except Exception:
            continue
        if it.get("date") == "" or not it.get("date"):
            m = _RE_EST.search(html)
            if m:
                it["date"] = _norm_date(m.group(1))
        if not it.get("reg_cap"):
            m = _RE_CAP.search(html)
            if m:
                it["reg_cap"] = m.group(1).strip()
        if not it.get("legal"):
            m = _RE_LEGAL.search(html)
            if m:
                it["legal"] = m.group(1).strip()


def collect_qcc_free(cfg: dict) -> tuple[list[dict], list[dict]]:
    """免费工商数据通道。reg_days：只保留最近 N 天内注册的企业（"每天新注册"）。"""
    health: list[dict] = []
    queries = cfg.get("queries") or ["上海 自动化", "上海 智能装备", "上海 机器人",
                                     "上海 机电 设备", "上海 仪器仪表"]
    reg_days = int(cfg.get("reg_days", 7))
    today_s = today()
    y, m, dd = today_s.split("-")
    import datetime as _dt
    cutoff = (_dt.date(int(y), int(m), int(dd)) - _dt.timedelta(days=reg_days)).isoformat()

    # 通道A：爱企查
    try:
        op = _session()
        aq_items, aq_note = aiqicha_search(op, queries, pages=int(cfg.get("pages", 2)))
    except Exception as e:  # noqa: BLE001
        aq_items, aq_note = [], f"通道异常: {type(e).__name__}"
    health.append({"source": "爱企查", "ok": bool(aq_items), "note": aq_note})

    items = list(aq_items)

    # 通道B：企查查网页（爱企查无结果时启用）
    if not items:
        try:
            qcc_items, qcc_note = qcc_web_search(queries[:3])
        except Exception as e:  # noqa: BLE001
            qcc_items, qcc_note = [], f"通道异常: {type(e).__name__}"
        health.append({"source": "企查查网页", "ok": bool(qcc_items), "note": qcc_note})
        items.extend(qcc_items)

    # 统一后处理：时效窗口 + 补充信息
    out: list[dict] = []
    for it in items:
        d = it.get("date") or ""
        if reg_days and d:
            if d < cutoff or d > today_s:   # 窗口外的旧注册 / 未来日期
                continue
        if "上海" not in (it["title"] + it.get("snippet", "") + it.get("url", "")):
            it["snippet"] = f"上海市 {it.get('snippet', '')}".strip()
        it.setdefault("kind", "新注册企业")
        out.append(it)
    return out, health
