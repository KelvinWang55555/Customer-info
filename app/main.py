"""每日汇总主程序（精简版：新闻 + 新注册企业）

用法:
  python -m app.main            # 正常运行（采集 -> 过滤 -> 推送 -> 写报告 -> 更新状态）
  python -m app.main --dry-run  # 只采集生成报告，不推送、不更新状态
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from .collectors.news import collect_news
from .collectors.qcc_api import collect_qcc
from .collectors.search_companies import _is_junk_url, apply_scoring, collect_new_companies
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


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        k = key_of(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="不推送不更新状态")
    args = parser.parse_args()

    cfg = json.loads(CONFIG_FILE.read_text("utf-8"))
    report_cfg = cfg.get("report", {})

    # ---------- 1. 采集：新闻（政策公示 + 行业新闻） ----------
    print("[stage] 采集新闻 ...")
    news, health = collect_news(
        cfg.get("news", {}), cfg.get("gov_notices", []), cfg.get("automation_keywords", []))

    # ---------- 2. 采集：新注册企业 ----------
    print("[stage] 采集新注册企业 ...")
    search_cfg = cfg.get("search_companies", {})
    raw_companies, comp_health = collect_new_companies(search_cfg)
    qcc_items, qcc_health = collect_qcc(cfg.get("qcc_api", {}))
    raw_companies.extend(qcc_items)
    health.extend(comp_health + qcc_health)
    companies = apply_scoring(raw_companies, search_cfg)

    # ---------- 3. 去重 + 仅保留新增 ----------
    news, companies = dedupe(news), dedupe(companies)
    seen = load_state()
    new_news = [it for it in news if key_of(it) not in seen]
    new_companies = [it for it in companies if key_of(it) not in seen]
    print(f"[dedupe] 新闻 {len(news)}→新增{len(new_news)}，企业 {len(companies)}→新增{len(new_companies)}")

    if not args.dry_run:
        for it in news + companies:
            seen.add(key_of(it))
        save_state(seen)

    # ---------- 4. 解析跳转链接（搜索引擎给的是加密跳转） ----------
    budget = int(report_cfg.get("resolve_redirects", 60))
    for it in new_news + new_companies:
        if budget <= 0:
            break
        url = it.get("url") or ""
        if not url:
            continue
        resolved = resolve_redirect(url)
        if resolved != url:
            it["url"] = resolved
            budget -= 1

    # ---------- 5. 拿到真实 URL 后的第二批过滤：时效 + 垃圾域名 + 地域 ----------
    probe = {"left": int(report_cfg.get("probe_publish_date", 28))}

    def refine(items: list[dict], max_age_days: int,
               region: str = "") -> list[dict]:
        from datetime import date, timedelta

        from .collectors.search_companies import _is_stale
        from .util import extract_date, fetch_publish_date

        cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
        out: list[dict] = []
        for it in items:
            url = it.get("url") or ""
            # 必须是可访问的真实链接（过滤 javascript:; 等提示页）
            if not url.startswith(("http://", "https://")) or _is_junk_url(url):
                continue
            title = it.get("title", "")
            if _is_stale(title, url):          # 真实 URL 里带早年 → 旧闻
                continue
            if not it.get("date"):             # 真实 URL 可能带来日期
                it["date"] = extract_date(url, it.get("snippet", ""))
            if not it.get("date") and probe["left"] > 0:
                # 还没有日期 → 抓文章页取发布日期（限量，避免拖慢）
                it["date"] = fetch_publish_date(url)
                probe["left"] -= 1
            if it.get("date") and it["date"] < cutoff:
                continue
            if region and region not in (title + it.get("snippet", "") + url):
                continue                       # 企业线索必须与上海相关
            out.append(it)
        return out

    news_age = int(cfg.get("news", {}).get("max_age_days", 30))
    comp_age = int(search_cfg.get("max_age_days", 180))
    n0, c0 = len(new_news), len(new_companies)
    new_news = refine(new_news, news_age)
    new_companies = refine(new_companies, comp_age, region="上海")
    print(f"[filter] 时效/地域过滤：新闻 {n0}→{len(new_news)}，企业 {c0}→{len(new_companies)}")
    new_news.sort(key=lambda it: it.get("date") or "", reverse=True)

    # ---------- 5. 生成报告 ----------
    report = build_report(new_news, new_companies, health)
    report_dir = ROOT / report_cfg.get("report_dir", "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today()}.md"
    report_path.write_text(report, "utf-8")
    print(f"[report] 已生成 {report_path}")
    print(f"[stats] 新闻 {len(new_news)} 条，新注册企业 {len(new_companies)} 家")

    # ---------- 6. 推送 ----------
    if args.dry_run:
        print("[push] dry-run 模式，跳过推送")
        return 0

    has_new = bool(new_news or new_companies)
    if not has_new and not report_cfg.get("push_empty", True):
        print("[push] 今日无新增，按配置跳过推送")
        return 0

    message = report if has_new else (
        f"# 上海自动化日报 {today()}\n\n今日无新增（新闻与企业线索均为 0）。")
    ok, msg = push_report(message, max_chars=report_cfg.get("max_push_chars", 6000))
    print(f"[push] {'成功' if ok else '失败'}: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
