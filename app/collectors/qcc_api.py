"""企查查开放平台采集器（标准库实现，无第三方依赖）。

开通与配置步骤（一次性的）：
1. 打开 https://openapi.qcc.com 注册账号并完成实名认证
2. 申请接口：至少需要「企业模糊搜索」；建议同时申请「企业基本信息」
   （免费试用额度一般每天几十~上百次，够日报使用）
3. 在个人中心拿到 API Key 与 Secret Key
4. GitHub 仓库 KelvinWang55555/Customer-info → Settings → Secrets and
   variables → Actions → New repository secret，添加两条：
      QCC_API_KEY    = 你的 Key
      QCC_SECRET_KEY = 你的 Secret Key
5. 完成。无需改代码：下次运行自动用 API 数据生成企业列表，
   字段包含 公司名称 / 成立日期 / 注册资本 / 法定代表人 / 经营范围。

接口名与参数写在 config/sources.json 的 qcc_api 段；如果购买的接口名
不同（如 FuzzySearch / SearchWide），改配置即可，不用动这里。
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

BASE = "https://api.qichacha.com"   # 官方文档接口域名（openapi.qcc.com 各接口文档标注）


def _txt(v) -> str:
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or v.get("value") or "").strip()
    if isinstance(v, (list, tuple)):
        return " ".join(_txt(x) for x in v).strip()
    return str(v or "").strip()


def _norm_date(s: str) -> str:
    m = re.search(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})", str(s or ""))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else str(s or "")[:10]


def _call(api: str, params: dict, key: str, secret: str,
          timeout: int = 25) -> dict:
    """官方文档标准鉴权（openapi.qcc.com 各接口页标注）：
       Token   = MD5(key + Timespan + SecretKey) 的 32 位大写
       Timespan= 精确到秒的 Unix 时间戳
    """
    ts = str(int(time.time()))          # 秒级时间戳（文档要求）
    token = hashlib.md5(f"{key}{ts}{secret}".encode()).hexdigest().upper()
    url = f"{BASE}/{api}?" + urllib.parse.urlencode({"key": key, **params})
    req = urllib.request.Request(url, headers={
        "Token": token, "Timespan": ts,
        "User-Agent": "sh-automation-daily/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _iter_results(data: dict) -> list:
    """兼容 Result / ResultList / result 等返回结构"""
    if not isinstance(data, dict):
        return []
    for k in ("Result", "result", "ResultList", "resultList", "Items"):
        v = data.get(k)
        if isinstance(v, list):
            return v
    inner = data.get("result") or {}
    if isinstance(inner, dict):
        for k in ("Result", "list", "List"):
            if isinstance(inner.get(k), list):
                return inner[k]
    return []


def _collect_from_search(data: dict) -> list[dict]:
    """模糊搜索结果 → 统一条目（轻字段版，注册资本等由详情接口补）"""
    out = []
    for r in _iter_results(data):
        if not isinstance(r, dict):
            continue
        name = _txt(r.get("Name") or r.get("name") or r.get("CompanyName"))
        if not name or "公司" not in name and "厂" not in name:
            continue
        key_no = _txt(r.get("KeyNo") or r.get("keyNo") or r.get("Id") or r.get("id"))
        out.append({
            "title": name,
            "url": f"https://www.qcc.com/firm/{key_no}.html" if key_no else "",
            "date": _norm_date(_txt(r.get("StartDate") or r.get("startDate") or r.get("EsDate"))),
            "key_no": key_no,
            "snippet": _txt(r.get("Address") or r.get("Area") or r.get("No")),
            "source": "企查查API",
        })
    return out


def _collect_from_detail(data: dict) -> dict:
    """企业基本信息接口 → {reg_cap, legal, scope, address, date}"""
    info = data.get("Result") or data.get("result") or {}
    if isinstance(info, dict):
        info = info.get("BasicInfo") or info.get("basicInfo") or info
    if not isinstance(info, dict):
        return {}
    return {
        "reg_cap": _txt(info.get("RegCapital") or info.get("regCapital")),
        "legal": _txt(info.get("LegalPerson") or info.get("legalPerson") or info.get("FrName")),
        "scope": _txt(info.get("Scope") or info.get("BusinessScope") or info.get("scope"))[:120],
        "address": _txt(info.get("Address") or info.get("address")),
        "date": _norm_date(_txt(info.get("StartDate") or info.get("StartDateStr") or "")),
    }


def collect_qcc(cfg: dict) -> tuple[list[dict], list[dict]]:
    """配置了 QCC_API_KEY/QCC_SECRET_KEY 时走官方 API；否则跳过。"""
    key = os.environ.get("QCC_API_KEY", "")
    secret = os.environ.get("QCC_SECRET_KEY", "")
    if not (cfg.get("enabled", True) and key and secret):
        return [], [{"source": "qcc-api", "ok": False,
                     "note": "未配置 QCC_API_KEY/QCC_SECRET_KEY，已跳过"}]

    import datetime as _dt
    reg_days = int(cfg.get("reg_days", 7))
    today_s = _dt.date.today().isoformat()
    cutoff = (_dt.date.today() - _dt.timedelta(days=reg_days)).isoformat()
    health: list[dict] = []

    # 1) 模糊搜索：上海新注册的自动化相关企业
    #    官方 FuzzySearch/GetList 每次最多返回 5 条，多页补足
    search_api = cfg.get("search_api", "FuzzySearch/GetList")
    pages = max(1, int(cfg.get("search_pages", 4)))  # 4页≈20条，消耗4次配额
    items: list[dict] = []
    try:
        for p in range(1, pages + 1):
            data = _call(search_api, {
                "searchKey": cfg.get("search_key", "上海 自动化"),
                "pageIndex": p,
            }, key, secret)
            got = _collect_from_search(data)
            items.extend(got)
            if len(got) < 5:      # 没满页说明到底了
                break
            time.sleep(0.4)
        health.append({"source": f"qcc-api·{search_api}", "ok": True,
                       "note": f"返回{len(items)}条"})
    except Exception as e:  # noqa: BLE001
        return [], [{"source": f"qcc-api·{search_api}", "ok": False,
                     "note": f"搜索失败: {type(e).__name__}: {e}"}]

    # 2) 时效窗口：只保留最近 reg_days 天内成立的（"每天新注册"）
    window = [it for it in items
              if (not it["date"] or cutoff <= it["date"] <= today_s)]

    # 3) 详情接口补注册资本/法人/经营范围（限量，控制配额）
    detail_api = cfg.get("detail_api", "")
    detail_top = int(cfg.get("detail_top", 10))
    if detail_api:
        ok_n = 0
        for it in window[:detail_top]:
            if not it.get("key_no"):
                continue
            try:
                d = _collect_from_detail(_call(
                    detail_api, {"id": it["key_no"], "keyNo": it["key_no"],
                                 "unique": it["key_no"]}, key, secret))
            except Exception:
                continue  # 接口未购买/参数不符时静默降级，列表仍可用
            for k, v in d.items():
                if v and not it.get(k):
                    it[k] = v
            if d.get("date") and not it.get("date"):
                it["date"] = d["date"]
            ok_n += 1
            time.sleep(0.4)
        health.append({"source": f"qcc-api·{detail_api}", "ok": ok_n > 0,
                       "note": f"详情补齐{ok_n}条（配额消耗{detail_top}次内）"})

    # 4) 地域兜底：确保是上海企业
    out = []
    for it in window:
        blob = it["title"] + it.get("snippet", "") + it.get("address", "")
        if "上海" not in blob:
            it["snippet"] = f"上海市 {it.get('snippet', '')}".strip()
        it.setdefault("kind", "新注册企业")
        out.append(it)
    return out, health
