"""公司相关度多维评分引擎

不只看公司名字，而是综合「标题 + 摘要 + 经营范围文本」做加权关键词分析：
- 核心业务词（ weight 3 ）：直接做自动化设备/部件的
- 集成与工程词（ weight 2 ）：做自动化系统集成/工程的
- 邻近行业词（ weight 1 ）：机电/电气/仪器仪表等可能相关的
输出：score（分值）、grade（A/B/C 相关度）、matched（命中词），供日报按相关度排序展示。
"""
import re

# 加权关键词库（可在 config/sources.json -> company_scorer.keywords 里覆盖/扩展）
DEFAULT_KEYWORDS = {
    # weight 3: 核心自动化业务
    3: [
        "自动化设备", "工业机器人", "非标自动化", " PLC", "PLC", "伺服", "变频器",
        "传感器", "工控机", "数控", "SCADA", "DCS", "机器视觉", "视觉检测",
        "运动控制", "步进电机", "气动元件", "液压", "控制柜", "人机界面", "HMI",
    ],
    # weight 2: 系统集成 / 工程
    2: [
        "系统集成", "自动化工程", "自动化系统", "智能制造", "智能装备",
        "工业互联网", "数字化工厂", "智能仓储", "流水线", "输送线",
        "自动化生产线", "电气成套", "智能产线",
    ],
    # weight 1: 邻近行业
    1: [
        "机电", "电气", "仪器仪表", "机械制造", "智能科技", "工业软件",
        "检测设备", "物流设备", "自动化", "机器人", "机电一体化",
    ],
}

GRADE_RULES = [(8, "A"), (4, "B"), (1, "C")]  # score>=8 -> A 级 ...

NEGATIVE = ("招聘", "求职", "培训班", "资格考试")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or ""))  # 去空格便于匹配 " PLC" 之类的写法


def score_company(item: dict, keywords: dict | None = None) -> dict | None:
    """对一条公司线索打分。返回 {score, grade, matched}；低于 C 级返回 None。

    item 需含 title，可选 snippet / scope（经营范围文本）。
    """
    if keywords is None:
        kw = DEFAULT_KEYWORDS
    else:
        # 允许 config 覆盖：{"3": [...], "2": [...], "1": [...]}
        kw = {}
        for w, words in (keywords or {}).items():
            kw[int(w)] = words

    text = _normalize(
        (item.get("title") or "") + "|" + (item.get("snippet") or "") +
        "|" + (item.get("scope") or ""))
    if any(n in text for n in NEGATIVE):
        return None

    score = 0
    matched: list[str] = []
    seen = set()
    for weight, words in sorted(kw.items(), reverse=True):
        for w in words:
            key = _normalize(w)
            if key and key in text and key not in seen:
                score += weight
                matched.append(w.strip())
                seen.add(key)

    if score < 1:
        return None
    grade = next((g for th, g in GRADE_RULES if score >= th), "C")
    return {"score": score, "grade": grade, "matched": matched[:8]}


def enrich_scope(items: list[dict], top_n: int = 5) -> None:
    """对评分靠前的线索做二次检索，用『公司名+经营范围』快照补充 scope 文本再重新打分。

    免费渠道拿不到结构化工商数据，这里用搜索快照近似；配置企查查 API 后此函数可弃用。
    """
    from urllib.parse import quote

    from ..util import http_get

    bing = "https://www.bing.com/search?q={q}&setlang=zh-CN&count=5"
    tag_re = re.compile(r"<[^>]+>")
    for it in items[:top_n]:
        name = (it.get("company") or it.get("title") or "")[:24]
        if not name:
            continue
        try:
            html = http_get(bing.format(q=quote(f"{name} 经营范围")))
            # 取所有摘要文本拼起来当 scope 近似值
            text = tag_re.sub(" ", html)
            m = re.search(r"经营范围[::]?\s*([^<>{}]{20,180})", text)
            if m:
                it["scope"] = m.group(1)
        except Exception:  # noqa: BLE001 二次检索失败不影响主流程
            pass
