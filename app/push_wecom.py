"""消息推送：支持三个渠道，按已配置的 Secret 自动选择

1. Server酱（推荐，个人微信即可）: Secret = SERVERCHAN_SENDKEY
   在 https://sct.ftqq.com/ 用微信扫码登录 → 复制 SendKey → 关注公众号"方糖"接收
2. PushPlus（个人微信）: Secret = PUSHPLUS_TOKEN
   在 https://www.pushplus.plus/ 用微信登录 → 复制 token → 关注公众号接收
3. 企业微信群机器人: Secret = WECOM_WEBHOOK
"""
import json
import os
import urllib.parse
import urllib.request

from .util import http_post_json

CHANNEL_NOTE = (
    "未配置任何推送渠道。三选一配置 GitHub Secret：\n"
    "- SERVERCHAN_SENDKEY（Server酱，个人微信，推荐）\n"
    "- PUSHPLUS_TOKEN（PushPlus，个人微信）\n"
    "- WECOM_WEBHOOK（企业微信群机器人）"
)


def _push_serverchan(sendkey: str, title: str, md_text: str) -> tuple[bool, str]:
    """Server酱：表单 POST，desp 支持 Markdown"""
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    data = urllib.parse.urlencode({
        "title": title[:31],
        "desp": md_text[:30000],
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace"))
    if result.get("code") == 0:
        return True, f"Server酱推送成功: {result.get('message', '')}"
    return False, f"Server酱推送失败: {result}"


def _push_pushplus(token: str, title: str, md_text: str) -> tuple[bool, str]:
    """PushPlus：JSON POST，template=markdown"""
    result = http_post_json("https://www.pushplus.plus/send", {
        "token": token,
        "title": title[:99],
        "content": md_text[:90000],
        "template": "markdown",
    })
    if result.get("code") == 200:
        return True, "PushPlus推送成功"
    return False, f"PushPlus推送失败: {result}"


def _push_wecom(webhook: str, title: str, md_text: str, max_chars: int) -> tuple[bool, str]:
    """企业微信群机器人：markdown 消息，超长自动拆条"""
    # 转换标题语法为企业微信支持的加粗
    out_lines = []
    for line in md_text.splitlines():
        if line.startswith("## "):
            out_lines.append(f"**{line[3:].strip()}**")
        elif line.startswith("# "):
            out_lines.append(f"**{line[2:].strip()}**")
        else:
            out_lines.append(line)
    text = "\n".join(out_lines)

    chunks, current = [], ""
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

    ok_count = 0
    for i, content in enumerate(chunks, 1):
        try:
            data = http_post_json(webhook, {
                "msgtype": "markdown",
                "markdown": {"content": content[:4000]},
            })
            if data.get("errcode") == 0:
                ok_count += 1
            else:
                return ok_count == len(chunks), f"第{i}条推送失败: {data}"
        except Exception as e:  # noqa: BLE001
            return ok_count == len(chunks), f"第{i}条推送异常: {type(e).__name__}: {e}"
    return True, f"企业微信已推送 {ok_count} 条消息"


def push_report(md_text: str, title: str = "", max_chars: int = 3800) -> tuple[bool, str]:
    """按已配置的渠道推送，返回 (是否成功, 说明)"""
    if not title:
        first = md_text.splitlines()[0] if md_text else ""
        title = first.lstrip("# ").strip() or "上海自动化产业日报"

    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "")
    pushplus = os.environ.get("PUSHPLUS_TOKEN", "")
    wecom = os.environ.get("WECOM_WEBHOOK", "")

    try:
        if sendkey:
            return _push_serverchan(sendkey, title, md_text)
        if pushplus:
            return _push_pushplus(pushplus, title, md_text)
        if wecom:
            if "qyapi.weixin.qq.com" not in wecom:
                return False, "WECOM_WEBHOOK 格式不对（应为 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx）"
            return _push_wecom(wecom, title, md_text, max_chars)
        return False, CHANNEL_NOTE
    except Exception as e:  # noqa: BLE001 网络/渠道异常不崩溃，返回失败说明
        return False, f"推送异常: {type(e).__name__}: {e}"
