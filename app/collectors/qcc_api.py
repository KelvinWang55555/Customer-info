"""企查查 API 采集器（可选，标准库实现）。

拿到企查查开放平台账号后：
1. 在 GitHub 仓库 Settings -> Secrets and variables -> Actions 添加：
   QCC_API_KEY    —— 你的 Key
   QCC_SECRET_KEY —— 你的 SecrectKey
2. config/sources.json 中 qcc_api.enabled 保持 true
脚本会调用企查查接口查询上海新注册的自动化相关企业。

企查查接口文档: https://openapi.qcc.com/dataApi
不同接口的签名方式和返回字段略有差异，接入时按所购接口的文档微调。
"""
import hashlib
import json
import os
import time
import urllib.request

API_URL = "https://api.qcc.com/APIUserInfo/GetSearchList"  # 按实际购买的接口替换
TOKEN_URL = "https://api.qcc.com/OpenInterface/GetToken"   # 示例；以官方文档为准


def _fetch_json(url: str, params: dict, headers: dict | None = None, timeout: int = 20) -> dict:
    import urllib.parse
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def collect_qcc(cfg: dict) -> tuple[list[dict], list[dict]]:
    """没有 Key 时直接跳过；有 Key 时调用接口。"""
    key = os.environ.get("QCC_API_KEY", "")
    secret = os.environ.get("QCC_SECRET_KEY", "")
    if not (cfg.get("enabled", True) and key and secret):
        return [], [{"source": "qcc-api", "ok": False,
                     "note": "未配置 QCC_API_KEY/QCC_SECRET_KEY，已跳过"}]

    try:
        ts = str(int(time.time() * 1000))
        token = _fetch_json(TOKEN_URL, {"key": key, "seckey": secret}).get("Token", "")
        digest = hashlib.md5(f"{token}{ts}{secret}".encode()).hexdigest().upper()
        data = _fetch_json(
            API_URL,
            {
                "key": key,
                "searchKey": cfg.get("keywords", "自动化"),
                "area": cfg.get("area", "上海"),
                "searchType": cfg.get("search_type", "searchWide"),
            },
            headers={"Token": token, "Timespan": ts, "Sign": digest},
        )
    except Exception as e:  # noqa: BLE001
        return [], [{"source": "qcc-api", "ok": False,
                     "note": f"调用失败: {type(e).__name__}: {e}"}]

    items = []
    # 企查查各接口返回结构不同，这里按常见结构解析；接入后按实际字段调整
    for r in (data.get("Result") or []):
        name = r.get("Name") or r.get("name") or ""
        if not name:
            continue
        items.append({
            "source": "企查查API",
            "title": f"{name}（成立:{r.get('StartDate', '未知')}）",
            "url": f"https://www.qcc.com/firm/{r.get('KeyNo', '')}.html",
            "snippet": f"法定代表人:{r.get('LegalPerson', '')} 状态:{r.get('Status', '')}",
            "date": (r.get("StartDate") or "")[:10],
        })
    return items, [{"source": "qcc-api", "ok": True, "note": f"返回{len(items)}条"}]
