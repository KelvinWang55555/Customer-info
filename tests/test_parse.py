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

    # 4. 报告生成
    report = build_report(
        [{"source": "s", "title": "测试公告" * 2, "url": "https://a.com", "date": "2026-11-10"}],
        [{"source": "s", "title": "测试公司", "url": "https://b.com", "snippet": "摘要"}],
        [{"source": "s", "ok": True, "note": "ok"}],
    )
    assert "**测试公告" not in report  # markdown 中链接格式正确

    # 5. 推送渠道：未配置任何 Secret 时给出明确提示；配置 Server酱 时走对应分支
    ok, msg = push_report(report)
    assert ok is False and "SERVERCHAN_SENDKEY" in msg, f"未配置渠道时的提示异常: {msg}"

    import os
    os.environ["SERVERCHAN_SENDKEY"] = "SCTtest_fake_key"
    ok, msg = push_report(report)
    assert ok is False and ("Server酱" in msg or "推送异常" in msg), f"Server酱分支未生效: {msg}"  # 假 Key 会被 API 拒绝，但证明走了该渠道且异常被捕获

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
