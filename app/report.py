"""生成每日 Markdown 日报"""
from .util import today


def build_report(projects: list[dict], companies: list[dict], health: list[dict]) -> str:
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

    # ---------- 第二部分：新注册公司 ----------
    lines.append("## 二、新注册自动化企业线索")
    if companies:
        for it in companies[:20]:
            title = it["title"]
            lines.append(f"- **{title}**")
            if it.get("url"):
                lines.append(f"  [链接]({it['url']})")
            if it.get("snippet"):
                lines.append(f"  {it['snippet']}")
        lines.append("")
        lines.append("> 注：以上来自免费公开渠道，覆盖可能不全；配置企查查 API 后可获得准确数据。")
    else:
        lines.append("- 今日无新增线索")
    lines.append("")

    # ---------- 第三部分：数据源健康状态 ----------
    lines.append("## 三、数据源状态")
    for h in health:
        status = "✅" if h["ok"] else "⚠️"
        lines.append(f"- {status} {h['source']}：{h['note']}")

    return "\n".join(lines)


def markdown_to_wecom(md_text: str, max_chars: int) -> list[str]:
    """把 Markdown 转成企业微信群机器人消息（可能拆多条，每条 <= max_chars）。

    企业微信 markdown 不支持 h1/h2 标题，# 和 ## 转成 **加粗**。
    """
    out_lines = []
    for line in md_text.splitlines():
        if line.startswith("## "):
            out_lines.append(f"**{line[3:].strip()}**")
        elif line.startswith("# "):
            out_lines.append(f"**{line[2:].strip()}**")
        else:
            out_lines.append(line)
    text = "\n".join(out_lines)

    chunks = []
    current = ""
    for block in text.split("\n\n"):
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate.encode("utf-8")) > max_chars:
            if current:
                chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
