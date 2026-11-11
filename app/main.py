"""每日汇总主程序

用法:
  python -m app.main            # 正常运行（采集 -> 过滤 -> 推送 -> 写报告 -> 更新状态）
  python -m app.main --dry-run  # 只采集生成报告，不推送、不更新状态
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from .collectors.gov_notices import collect_gov_notices
from .collectors.industry_signals import collect_signals
from .collectors.qcc_api import collect_qcc
from .collectors.search_companies import apply_scoring, collect_new_companies
from .collectors.tenders import collect_tenders
from .push_wecom import push_report
from .report import build_report
from .util import resolve_redirect, today

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "sources.json"
STATE_FILE = ROOT / "state" / "seen.json"
STATE_KEEP = 5000  # 状态文件最多保留的条目数


def load_state() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text("utf-8")))
        except Exception:
            return set()
    return set()


def save_state(seen: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = sorted(seen)[-STATE_KEEP:]
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def key_of(item: dict) -> str:
    """去重键：URL 优先，否则标题 hash"""
    return item.get("url") or hashlib.md5(item["title"].encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="不推送不更新状态")
    args = parser.parse_args()

    cfg = json.loads(CONFIG_FILE.read_text("utf-8"))

    # ---------- 1. 采集 ----------
    # 搜索引擎有累积限流效应：越靠后的板块越容易拿不到结果。
    # 因此按日期轮换三个搜索类板块的采集顺序，让每个板块公平分享"引擎新鲜期"。
    projects, health = collect_gov_notices(
        cfg.get("gov_notices", []), cfg.get("automation_keywords", []))
    search_cfg = cfg.get("search_companies", {})

    def _companies() -> tuple[list[dict], list[dict]]:
        items, hh = collect_new_companies(search_cfg)
        extra, h2 = collect_qcc(cfg.get("qcc_api", {}))
        items.extend(extra)
        return apply_scoring(items, search_cfg), hh + h2

    def _tenders() -> tuple[list[dict], list[dict]]:
        return collect_tenders(cfg.get("tenders", {}))

    def _signals() -> tuple[list[dict], list[dict]]:
        return collect_signals(cfg.get("signals", {}))

    stages = [("companies", _companies), ("tenders", _tenders), ("signals", _signals)]
    rotate = 0
    if cfg.get("report", {}).get("rotate_stage_order", True):
        from datetime import date

        rotate = date.today().toordinal() % len(stages)
    stages = stages[rotate:] + stages[:rotate]
    print(f"[order] 采集顺序: {' → '.join(name for name, _ in stages)}")

    companies: list[dict] = []
    tenders: list[dict] = []
    signals: list[dict] = []
    for name, fn in stages:
        items, hh = fn()
        health.extend(hh)
        if name == "companies":
            companies = items
        elif name == "tenders":
            tenders = items
        else:
            signals = items

    # ---------- 2. 去重 ----------
    # 同一 URL 可能出现在多个栏目，先全局去重
    def dedupe(items: list[dict]) -> list[dict]:
        seen_urls, out = set(), []
        for it in items:
            k = key_of(it)
            if k in seen_urls:
                continue
            seen_urls.add(k)
            out.append(it)
        return out

    projects, companies = dedupe(projects), dedupe(companies)
    tenders, signals = dedupe(tenders), dedupe(signals)
    seen = load_state()
    new_projects = [it for it in projects if key_of(it) not in seen]
    new_companies = [it for it in companies if key_of(it) not in seen]
    new_tenders = [it for it in tenders if key_of(it) not in seen]
    new_signals = [it for it in signals if key_of(it) not in seen]

    if not args.dry_run:
        for it in projects + companies + tenders + signals:
            seen.add(key_of(it))
        save_state(seen)

    # 搜索引擎给的是加密跳转链接，解析成真实地址（只处理入选条目并限量，避免拖慢）
    budget = int(cfg.get("report", {}).get("resolve_redirects", 60))
    for it in new_tenders + new_signals + new_companies + new_projects:
        if budget <= 0:
            break
        url = it.get("url") or ""
        if url:
            resolved = resolve_redirect(url)
            if resolved != url:
                it["url"] = resolved
                budget -= 1

    # 解析后才知道真实域名，这里二次过滤文档站/视频站等无价值来源
    from .collectors.search_companies import _is_junk_url

    def drop_junk(items: list[dict]) -> list[dict]:
        return [it for it in items if not _is_junk_url(it.get("url") or "")]

    new_projects = drop_junk(new_projects)
    new_companies = drop_junk(new_companies)
    new_tenders = drop_junk(new_tenders)
    new_signals = drop_junk(new_signals)

    # ---------- 3. 生成报告 ----------
    report = build_report(new_projects, new_companies, health, new_tenders, new_signals)
    report_dir = ROOT / cfg.get("report", {}).get("report_dir", "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today()}.md"
    report_path.write_text(report, "utf-8")
    print(f"[report] 已生成 {report_path}")
    news_n = sum(1 for it in new_signals if it.get("category") == "行业动向新闻")
    print(f"[stats] 新增公示 {len(new_projects)} 条，新增公司线索 {len(new_companies)} 条，"
          f"招标公告 {len(new_tenders)} 条，园区/展会/规划 {len(new_signals) - news_n} 条，"
          f"行业动向新闻 {news_n} 条")

    # ---------- 4. 推送 ----------
    has_new = bool(new_projects or new_companies or new_tenders or new_signals)
    if args.dry_run:
        print("[push] dry-run 模式，跳过推送")
        return 0

    push_empty = cfg.get("report", {}).get("push_empty", True)
    if not has_new and not push_empty:
        print("[push] 今日无新增，按配置跳过推送")
        return 0

    message = report if has_new else (
        f"# 上海自动化产业日报 {today()}\n\n今日无新增命中，数据源状态见仓库报告。")
    ok, msg = push_report(
        message, max_chars=cfg.get("report", {}).get("max_push_chars", 3800))
    print(f"[push] {'成功' if ok else '失败'}: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
