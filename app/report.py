"""生成每日 Markdown 日报 —— 两板块：新闻 + 新注册企业

面向微信端阅读：开头概览数字、每条单行列表、企业按相关度分组。
"""
from .util import today

NEWS_GROUPS = (("政策公示", "📋 政策/公示"), ("上海动态", "🏙 上海动态"), ("行业动态", "📊 行业动态"))


def _short(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def _date_short(d: str) -> str:
    """2026-11-11 -> 11-11"""
    return d[5:] if d and len(d) >= 10 else (d or "")


def build_report(news: list[dict], companies: list[dict], health: list[dict],
                 news_limit: int = 32, company_limit: int = 25) -> str:
    lines = [f"# 上海自动化日报 {today()}", ""]
    lines.append(f"📊 **今日**：新闻 {len(news)} 条 · 新注册企业 {len(companies)} 家")
    lines.append("")

    # ---------- 一、新闻 ----------
    lines.append(f"## 📰 自动化相关新闻（{len(news)}）")
    if news:
        shown = 0
        for kind, label in NEWS_GROUPS:
            group = [it for it in news if it.get("kind", "行业动态") == kind]
            if not group:
                continue
            lines.append(f"**{label}（{len(group)}）**")
            for it in group:
                if shown >= news_limit:
                    break
                d = _date_short(it.get("date", ""))
                line = f"- [{_short(it['title'], 40)}]({it['url']})"
                if d:
                    line += f" · {d}"
                lines.append(line)
                shown += 1
            lines.append("")
        if shown >= news_limit and len(news) > news_limit:
            lines.append(f"> 共 {len(news)} 条，已展示前 {news_limit} 条")
            lines.append("")
    else:
        lines.append("- 今日暂无新增（政府与媒体栏目页通常稳定，搜索引擎限流时搜索补充会缺失）")
        lines.append("")

    # ---------- 二、新注册企业（列表呈现：逐家公司一行名 + 一行工商信息） ----------
    lines.append(f"## 🏢 上海新注册企业（{len(companies)}）")
    if companies:
        for it in companies[:company_limit]:
            name = _short(it["title"], 30)
            head = f"- [{name}]({it['url']})" if it.get("url") else f"- **{name}**"
            lines.append(head)
            meta = []
            d = _date_short(it.get("date", ""))
            if d:
                meta.append(f"注册 {d}")
            if it.get("reg_cap"):
                meta.append(f"资本 {_short(it['reg_cap'], 14)}")
            if it.get("legal"):
                meta.append(f"法人 {_short(it['legal'], 8)}")
            grade = it.get("grade")
            if grade:
                badge = {"A": "🟢", "B": "🟡", "C": "⚪"}.get(grade, "")
                meta.append(f"{badge}{grade}·{it.get('score', 0)}分")
            if it.get("matched"):
                meta.append("命中:" + "、".join(it["matched"][:3]))
            elif it.get("scope"):
                meta.append(f"经营:{_short(it['scope'], 12)}")
            if meta:
                lines.append("  " + " · ".join(meta))
        lines.append("")
        lines.append("> 工商信息来自爱企查/企查查（直连），🟢A 核心自动化 · 🟡B 系统集成 · ⚪C 邻近行业；"
                     "搜索兜底来源可能缺注册资本等字段")
    else:
        lines.append("- 今日暂无新增（工商直连被风控且搜索兜底无结果）")
    lines.append("")

    # ---------- 数据源 ----------
    ok_n = sum(1 for h in health if h["ok"])
    lines.append(f"**数据源**：{ok_n}/{len(health)} 正常")
    bad = [h for h in health if not h["ok"]]
    for h in bad:
        lines.append(f"- ⚠️ {h['source']}：{_short(h['note'], 40)}")

    return "\n".join(lines)
