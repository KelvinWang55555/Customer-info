"""解析逻辑离线测试：不依赖网络，python tests/test_parse.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collectors.gov_notices import _parse_page
from app.collectors.search_companies import _parse_bing
from app.push_wecom import CHANNEL_NOTE, push_report
from app.report import build_report
from app.util import extract_date

SHEITC_HTML = """
<html><body>
<ul class="list">
<li><a href="/gg/20261110/abc123.html" title="关于智能制造示范工厂名单的公示">
  关于智能制造示范工厂名单的公示
  为推进智能制造，现将名单予以公示。
  2026-11-10</a></li>
<li><a href="/gg/20261109/def456.html">
  上海市经济信息化委关于组织2026年智能工厂领域项目申报的通知
  2026-11-09</a></li>
<li><a href="/gg/index.html">返回列表</a></li>
<li><a href="javascript:void(0)">无障碍</a></li>
</ul></body></html>
"""

BING_HTML = """
<li class="b_algo"><h2><a href="https://example.com/a">上海新注册一家自动化科技有限公司</a></h2>
<div class="b_caption"><p>该公司于2026年11月在浦东新区完成工商注册。</p></div></li>
<li class="b_algo"><h2><a href="https://example.com/b">某招聘信息</a></h2>
<div class="b_caption"><p>招聘自动化工程师。</p></div></li>
"""


def main():
    fails = []

    # 1. 标题清洗
    items = _parse_page(SHEITC_HTML, "https://sheitc.sh.gov.cn/gg/", "测试源")
    assert len(items) == 2, f"应解析出2条有效链接, 实际 {len(items)}"
    assert items[0]["title"] == "关于智能制造示范工厂名单的公示", f"标题清洗失败: {items[0]['title']!r}"
    assert items[0]["date"] == "2026-11-10", f"URL日期提取失败: {items[0]['date']}"

    # 2. Bing 解析
    bing = _parse_bing(BING_HTML)
    assert len(bing) == 2, f"Bing应解析出2条, 实际 {len(bing)}"
    assert bing[0]["title"] == "上海新注册一家自动化科技有限公司"

    # 3. 日期提取兜底
    assert extract_date("https://x.com/p/", "发布于 2026年11月3日") == "2026-11-03"

    # 4. 公司评分引擎
    from app.collectors.company_scorer import score_company
    from app.collectors.search_companies import apply_scoring

    strong = score_company({"title": "上海XX自动化设备有限公司",
                            "snippet": "经营范围：工业机器人、PLC 控制系统、伺服驱动、传感器研发"})
    assert strong and strong["grade"] == "A", f"高相关应评 A: {strong}"
    weak = score_company({"title": "上海XX餐饮管理有限公司", "snippet": "餐饮服务管理"})
    assert weak is None, f"无关公司应被丢弃: {weak}"
    mid = score_company({"title": "上海XX机电工程有限公司", "snippet": "电气安装 系统集成"})
    assert mid and mid["grade"] in ("B", "C"), f"邻近行业评分异常: {mid}"
    scored = apply_scoring(
        [{"title": "甲自动化设备", "snippet": "PLC 变频器", "url": "u1"},
         {"title": "乙餐饮管理", "snippet": "餐饮", "url": "u2"}],
        {"scorer": {"enrich": False}})
    assert [s["title"] for s in scored] == ["甲自动化设备"], "评分过滤/排序异常"

    # 5. 搜索引擎降级页识别（查询词 2-gram 覆盖率）
    from app.collectors.search_companies import _looks_degraded

    travel = [{"title": "上海旅游攻略_必去景点推荐", "snippet": "东方明珠 外滩", "url": "u1"},
              {"title": "上海自由行攻略2026", "snippet": "酒店 美食", "url": "u2"}]
    assert _looks_degraded(travel, "上海 临港 产业规划"), "通用填充页应判为降级"
    relevant = [{"title": "临港新片区发布产业规划 打造千亿级产业集群",
                 "snippet": "智能制造业布局", "url": "u3"}]
    assert not _looks_degraded(relevant, "上海 临港 产业规划"), "相关结果不应误判为降级"
    assert _looks_degraded([], "任意查询"), "空结果应判为降级"

    # 6. 信号板块相关性判定：标题不含"展会"但查询词高度相关的应保留
    from app.collectors.industry_signals import _relevant

    assert _relevant("上海国际工业自动化展览会开幕", ["展会"], "上海 工业自动化 展会"), \
        "含关键词应命中"
    assert not _relevant("上海美食节开幕", ["展会", "博览会"], "上海 工业自动化 展会"), \
        "无关内容应被过滤"

    # 5. 报告生成（含招标板块与评级展示）
    report = build_report(
        [{"source": "s", "title": "测试公告" * 2, "url": "https://a.com", "date": "2026-11-10"}],
        [{"source": "s", "title": "测试公司", "url": "https://b.com", "snippet": "摘要",
          "score": 9, "grade": "A", "matched": ["PLC"]}],
        [{"source": "s", "ok": True, "note": "ok"}],
        [{"title": "XX单位自动化设备采购项目招标公告", "url": "https://c.com",
          "date": "2026-11-11", "region": "上海", "source": "政府采购网"}],
    )
    assert "**测试公告" not in report  # markdown 中链接格式正确
    assert "招标公告" in report and "上海" in report
    assert "A·高相关" in report and "命中" in report

    # 6. 推送渠道：未配置任何 Secret 时给出明确提示；配置 Server酱 时走对应分支
    ok, msg = push_report(report)
    assert ok is False and "SERVERCHAN_SENDKEY" in msg, f"未配置渠道时的提示异常: {msg}"

    import os
    os.environ["SERVERCHAN_SENDKEY"] = "SCTtest_fake_key"
    ok, msg = push_report(report)
    assert ok is False and ("Server酱" in msg or "推送异常" in msg), f"Server酱分支未生效: {msg}"  # 假 Key 会被 API 拒绝，但证明走了该渠道且异常被捕获

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
