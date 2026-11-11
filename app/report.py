"""生成每日 Markdown 日报 —— 紧凑列表式

设计原则（面向微信端阅读体验）：
1. 开头给"今日概览"数字条，一眼看清各板块新增量
2. 每条信息压缩为单行列表项：标题即链接，来源/日期/摘要合并到同一行
3. 行业动向新闻（signals 中的"行业动向"分类）单列一个板块
"""
from .util import today

# 紧凑评级标签
GRADE_LABEL = {"A": "🟢A", "B": "🟡B", "C": "⚪C"}
# 行业动向新闻在 signals 中对应的分类名（与 config/sources.json 保持一致）
NEWS_CATEGORY = "行业动向新闻"


def _short(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def build_report(projects: list[dict], companies: list[dict], health: list[dict],
                 tenders: list[dict] | None = None,
                 signals: list[dict] | None = None) -> str:
    """生成日报正文（Markdown，兼容各推送渠道的消息格式）"""
    signals = signals or []
    # 拆分：行业动向新闻单列，其余（园区/展会/规划）归入产业动态
    news_items = [it for it in signals if it.get("category") == NEWS_CATEGORY]
    other_signals = [it for it in signals if it.get("category") != NEWS_CATEGORY]

    lines = [f"# 上海自动化产业日报 {today()}", ""]
    # ---------- 今日概览 ----------
    lines.append(f"📊 **今日概览**：公示 {len(projects)} | 新企业 {len(companies)} | "
                 f"招标 {len(tenders or [])} | 园区/展会/规划 {len(other_signals)} | "
                 f"行业动向 {len(news_items)}")
    lines.append("")

    # ---------- 一、投资项目公示 ----------
    lines.append(f"## 一、投资项目/政策公示（{len(projects)}）")
    if projects:
        for it in projects[:25]:
            date = _short(it.get("date", ""), 10)
            src = _short(it.get("source", ""), 14)
            tail = " · ".join(x for x in (src, date) if x)
            lines.append(f"- [{_short(it['title'], 42)}]({it['url']})" + (f" · {tail}" if tail else ""))
    else:
        lines.append("- 今日无新增命中")
    lines.append("")

    # ---------- 二、新注册企业线索 ----------
    lines.append(f"## 二、新注册自动化企业（{len(companies)}）")
    if companies:
        for it in companies[:20]:
            grade = it.get("grade", "")
            badge = GRADE_LABEL.get(grade, "")
            score = f"{it['score']}分" if it.get("score") else ""
            matched = f"命中:{'、'.join(it['matched'][:4])}" if it.get("matched") else ""
            meta = " · ".join(x for x in (badge, score, matched) if x)
            line = f"- **{_short(it['title'], 30)}**"
            if it.get("url"):
                line += f" [🔍]({it['url']})"
            if meta:
                line += f" {meta}"
            lines.append(line)
            if it.get("snippet"):
                lines.append(f"  {_short(it['snippet'], 60)}")
        lines.append("")
        lines.append("> 🟢A=核心自动化 🟡B=系统集成/工程 ⚪C=邻近行业；"
                     "配置企查查 API 后可基于经营范围精确评分")
    else:
        lines.append("- 今日无新增线索")
    lines.append("")

    # ---------- 三、招标公告 ----------
    lines.append(f"## 三、招标公告（{len(tenders or [])}）")
    if tenders:
        for it in tenders[:25]:
            date = _short(it.get("date", ""), 10)
            region = f"[{_short(it.get('region', ''), 6)}]" if it.get("region") else ""
            tail = date
            lines.append(f"- {region} [{_short(it['title'], 40)}]({it['url']})"
                         + (f" · {tail}" if tail else ""))
    else:
        lines.append("- 今日无新增招标公告")
    lines.append("")

    # ---------- 四、园区/展会/规划 ----------
    lines.append(f"## 四、产业园区/展会/规划（{len(other_signals)}）")
    if other_signals:
        by_cat: dict[str, list[dict]] = {}
        for it in other_signals:
            by_cat.setdefault(it.get("category", "其他"), []).append(it)
        for cat, items in by_cat.items():
            lines.append(f"**◇ {cat}（{len(items)}）**")
            for it in items[:8]:
                date = _short(it.get("date", ""), 10)
                line = f"- [{_short(it['title'], 38)}]({it['url']})"
                if date:
                    line += f" · {date}"
                lines.append(line)
    else:
        lines.append("- 今日无新增动态")
    lines.append("")

    # ---------- 五、行业动向新闻 ----------
    lines.append(f"## 五、行业动向新闻（{len(news_items)}）")
    if news_items:
        for it in news_items[:15]:
            date = _short(it.get("date", ""), 10)
            line = f"- [{_short(it['title'], 40)}]({it['url']})"
            if date:
                line += f" · {date}"
            lines.append(line)
            if it.get("snippet"):
                lines.append(f"  {_short(it['snippet'], 70)}")
    else:
        lines.append("- 今日无新增行业新闻")
    lines.append("")

    # ---------- 数据源状态（压缩为紧凑行） ----------
    ok_n = sum(1 for h in health if h["ok"])
    lines.append(f"**数据源**：{ok_n}/{len(health)} 正常")
    for h in health:
        status = "✅" if h["ok"] else "⚠️"
        lines.append(f"- {status} {h['source']}：{_short(h['note'], 40)}")

    return "\n".join(lines)
