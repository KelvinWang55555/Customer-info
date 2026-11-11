"""生成每日 Markdown 日报"""
from .util import today

GRADE_LABEL = {"A": "🟢A·高相关", "B": "🟡B·可能相关", "C": "⚪C·弱相关"}


def build_report(projects: list[dict], companies: list[dict], health: list[dict],
                 tenders: list[dict] | None = None,
                 signals: list[dict] | None = None) -> str:
    """生成日报正文（Markdown，兼容各推送渠道的消息格式）"""
    lines = [f"# 上海自动化产业日报 {today()}", ""]

    # ---------- 第一部分：投资项目公示 ----------
    lines.append("## 一、自动化产业投资项目/政策公示")
    if projects:
        for it in projects[:30]:
            date = f"（{it['date']}）" if it.get("date") else ""
            lines.append(f"- **[{it['title']}]({it['url']})** {date}")
            lines.append(f"  来源：{it['source']}")
    else:
        lines.append("- 今日无新增命中")
    lines.append("")

    # ---------- 第二部分：新注册公司（多维评分） ----------
    lines.append("## 二、新注册自动化企业线索（按相关度排序）")
    if companies:
        for it in companies[:20]:
            grade = it.get("grade", "")
            badge = f" {GRADE_LABEL.get(grade, grade)}" if grade else ""
            score = f"（{it['score']}分）" if it.get("score") else ""
            lines.append(f"- **{it['title']}**{badge}{score}")
            if it.get("url"):
                lines.append(f"  [链接]({it['url']})")
            if it.get("snippet"):
                lines.append(f"  {it['snippet']}")
            if it.get("matched"):
                lines.append(f"  命中：{'、'.join(it['matched'][:6])}")
        lines.append("")
        lines.append("> 评级说明：A=核心自动化业务，B=系统集成/工程相关，C=邻近行业。"
                     "免费渠道覆盖可能不全；配置企查查 API 后可基于工商经营范围精确评分。")
    else:
        lines.append("- 今日无新增线索")
    lines.append("")

    # ---------- 第三部分：招标公告 ----------
    lines.append("## 三、自动化相关招标公告")
    if tenders:
        for it in tenders[:25]:
            date = f"（{it['date']}）" if it.get("date") else ""
            region = f"[{it['region']}] " if it.get("region") else ""
            lines.append(f"- {region}**[{it['title']}]({it['url']})** {date}")
            if it.get("source"):
                lines.append(f"  来源：{it['source']}")
    else:
        lines.append("- 今日无新增招标公告")
    lines.append("")

    # ---------- 第四部分：园区/展会/规划等业务信号 ----------
    signals = signals or []
    lines.append("## 四、产业园区/展会/政府规划动态")
    if signals:
        # 按 category 分组展示
        by_cat: dict[str, list[dict]] = {}
        for it in signals:
            by_cat.setdefault(it.get("category", "其他"), []).append(it)
        for cat, items in by_cat.items():
            lines.append(f"**◇ {cat}**")
            for it in items[:10]:
                date = f"（{it['date']}）" if it.get("date") else ""
                lines.append(f"- **[{it['title']}]({it['url']})** {date}")
                if it.get("snippet"):
                    lines.append(f"  {it['snippet'][:120]}")
            lines.append("")
    else:
        lines.append("- 今日无新增动态")
        lines.append("")

    # ---------- 第五部分：数据源健康状态 ----------
    lines.append("## 五、数据源状态")
    for h in health:
        status = "✅" if h["ok"] else "⚠️"
        lines.append(f"- {status} {h['source']}：{h['note']}")

    return "\n".join(lines)
