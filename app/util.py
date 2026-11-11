"""通用工具：HTTP 请求（纯标准库）、日期提取、关键词匹配"""
import base64
import html as _html
import json
import re
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

DATE_IN_URL = re.compile(r"/(\d{4})(\d{2})(\d{2})/")
DATE_IN_TEXT = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")
TAG_RE = re.compile(r"<[^>]+>")


def _decode(raw: bytes, charset: str | None) -> str:
    """政府网站编码混杂，依次尝试：响应头编码 -> utf-8 -> gbk"""
    for enc in (charset, "utf-8", "gbk", "gb18030"):
        if not enc:
            continue
        try:
            text = raw.decode(enc)
            if text.count("\ufffd") < len(text) * 0.01:  # 乱码比例低就算成功
                return text
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def http_get(url: str, timeout: int = 20) -> str:
    """GET 一个页面并返回文本，失败抛异常由上层捕获"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset()
    return _decode(raw, charset)


def http_post_json(url: str, payload: dict, timeout: int = 15) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# 搜索引擎的跳转链接域名特征（真实地址被加密，只能跟一次重定向解出来）
_REDIRECT_HINTS = ("so.com/link", "sogou.com/link", "baidu.com/link", "bing.com/ck/a")
# 跳转页里真实地址的常见写法（360/搜狗用 JS 或 meta refresh 跳转）
_REDIRECT_BODY_PATTERNS = (
    re.compile(r'window\.location\.replace\(\s*["\']([^"\']+)["\']'),
    re.compile(r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']'),
    re.compile(r'URL\s*=\s*["\']([^"\']+)["\']', re.I),
)


def _decode_bing_link(url: str) -> str:
    """Bing 的 ck/a 跳转把目标地址放在 u=a1<base64url> 里，可直接解码"""
    try:
        from urllib.parse import parse_qs, unquote, urlparse

        u = parse_qs(urlparse(_html.unescape(url)).query).get("u", [""])[0]
        if u.startswith("a1"):
            raw = u[2:]
            raw += "=" * (-len(raw) % 4)
            return base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
        return unquote(u)
    except Exception:  # noqa: BLE001
        return ""


def resolve_redirect(url: str, timeout: int = 8) -> str:
    """把搜索引擎跳转链接解析成真实 URL；失败或非跳转链接原样返回。"""
    if not any(h in url for h in _REDIRECT_HINTS):
        return url
    if "bing.com/ck/a" in url:
        decoded = _decode_bing_link(url)
        if decoded.startswith("http"):
            return decoded
    target = url
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.url
            body = resp.read(4096).decode("utf-8", errors="replace")
        if final and not any(h in final for h in _REDIRECT_HINTS):
            target = final
        else:
            for pat in _REDIRECT_BODY_PATTERNS:
                m = pat.search(body)
                if m:
                    target = m.group(1).replace("\\/", "/")
                    break
    except Exception:  # noqa: BLE001
        return url
    return target if target.startswith("http") else url


def extract_date(url: str, text: str) -> str:
    """优先从 URL 路径取 YYYYMMDD，其次从周边文本取日期，返回 YYYY-MM-DD 或空"""
    m = DATE_IN_URL.search(url)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        try:
            datetime(int(y), int(mo), int(d))
            return f"{y}-{mo}-{d}"
        except ValueError:
            pass
    m = DATE_IN_TEXT.search(text or "")
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def match_any(title: str, keywords: list[str]) -> bool:
    t = (title or "").lower()
    return any(k.lower() in t for k in keywords)


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class LinkExtractor(HTMLParser):
    """提取页面里所有 <a href="...">标题</a>，兼容标题里嵌套 span 等标签"""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, text)
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._buf = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._buf).strip()))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)


def _clean_title(text: str) -> str:
    """政府网站常把 标题+摘要+日期 塞进同一个 <a>，取第一段作为标题"""
    for seg in re.split(r"\s{2,}|\n+", (text or "").strip()):
        seg = seg.strip()
        if seg:
            return seg
    return ""


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """返回 [(绝对URL, 标题), ...]，标题已清洗"""
    parser = LinkExtractor()
    parser.feed(html)
    out = []
    for href, text in parser.links:
        title = _clean_title(text)
        if title:
            out.append((urljoin(base_url, href), title))
    return out


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", html)).strip()
