#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
华南理工大学自考通知监控（计算机科学与技术 专升本 080901）
用法:
    python monitor.py            # 只输出新增通知
    python monitor.py --all      # 输出当前抓到的全部条目（调试用）
    python monitor.py --reset    # 清空去重记录（下次运行全部视为新）
数据源:
    1. 华工继续教育学院-自学考试通知  https://sce.scut.edu.cn/16915/list.htm
    2. 华工继续教育学院-通知公告      https://sce.scut.edu.cn/39994/list.htm
    3. 广东省教育考试院-自学考试      https://eea.gd.gov.cn/zxks/index.html
"""
import sys, os, re, json, hashlib, datetime, urllib.request, urllib.error
from html.parser import HTMLParser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class _Text(HTMLParser):
    """容忍畸形 HTML 的取文本器（比正则剥标签稳）"""
    BLOCK = {"p", "div", "br", "td", "tr", "li", "h1", "h2", "h3", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.buf.append(" ")

    def handle_data(self, d):
        if not self.skip:
            self.buf.append(d)

    def text(self):
        return re.sub(r"\s+", " ", "".join(self.buf)).strip()


def html_to_text(s):
    p = _Text()
    try:
        p.feed(s)
        p.close()
    except Exception:
        pass
    return p.text()

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

SOURCES = [
    ("华工·自学考试通知", "https://sce.scut.edu.cn/16915/list.htm"),
    ("华工·自学考试通知(2)", "https://sce.scut.edu.cn/16915/list2.htm"),
    ("华工·通知公告", "https://sce.scut.edu.cn/39994/list.htm"),
    ("华工·通知公告(2)", "https://sce.scut.edu.cn/39994/list2.htm"),
    ("省考试院·自学考试", "https://eea.gd.gov.cn/zxks/index.html"),
]

KEYWORDS = ["自学考试", "自考", "实践", "毕业论文", "毕业设计", "学位",
            "毕业办理", "毕业申请", "计算机科学与技术", "考核", "报名", "申报"]

# 已知窗口（confirmed=官方已公布；否则按历年规律预计）
WINDOWS = [
    ("2026-12-10", "12月毕业申请（省系统开放 12/10 9:30–12/15 16:00）", True),
    ("2027-01-18", "上半年毕业论文报名（预计 1 月中旬，仅 3 天）", False),
    ("2027-03-01", "上半年实践考核报名（预计 3 月初，仅 3–5 天）", False),
    ("2027-03-29", "上半年论文交稿（预计 3 月底–4 月初）", False),
    ("2027-04-26", "学位申请（2026年12月毕业批次，预计 4 月下旬）", False),
    ("2027-05-02", "上半年实践考核（预计 5/2–5/5，五山校区）", False),
    ("2027-06-11", "6月毕业申请（预计 6/11–6/16）", False),
    ("2027-08-02", "下半年毕业论文报名（预计 8 月初）", False),
    ("2027-08-25", "下半年实践考核报名（预计 8 月底–9 月初）", False),
    ("2027-10-02", "下半年实践考核（预计 10/2–10/5）", False),
    ("2027-10-25", "学位申请（2027年6月毕业批次，预计 10 月下旬）", False),
]


def next_windows(today, n=2):
    out = []
    for d, label, conf in WINDOWS:
        try:
            dt = datetime.date.fromisoformat(d)
        except Exception:
            continue
        if dt >= today:
            out.append(((dt - today).days, dt, label, conf))
    out.sort()
    return out[:n]


def fetch(url, timeout=25, retry=2):
    last = None
    for i in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            for enc in ("utf-8", "gbk"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "ignore")
        except Exception as e:
            last = e
            if i < retry:
                import time
                time.sleep(1.5)
    raise last


def clean(s):
    if "<" in s and ">" in s:
        s = html_to_text(s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_sce(html, host="https://sce.scut.edu.cn"):
    """华工 Webplus 模板：<p class="title"><a href='..' title='..'>..</a></p> ... <p class="time">2026-10-16</p>"""
    out, seen = [], set()
    for m in re.finditer(r'<p class="title">\s*<a href=\'([^\']+)\'([^>]*)>(.*?)</a>', html, re.S):
        href, attrs, inner = m.group(1), m.group(2), m.group(3)
        tm = re.search(r"title='([^']+)'", attrs)
        title = clean(tm.group(1) if tm else inner)
        tail = html[m.end():m.end() + 600]
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", tail)
        date = dm.group(1) if dm else ""
        url = href if href.startswith("http") else host + href
        key = norm(title)
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "url": url, "date": date})
    return out


def parse_eea(html):
    out = []
    for m in re.finditer(r'<a href="(https://eea\.gd\.gov\.cn/[^"]*post_\d+\.html)"[^>]*>(.*?)</a>'
                         r'\s*<span class="time">([\d-]+)</span>', html, re.S):
        out.append({"title": clean(m.group(2)), "url": m.group(1), "date": m.group(3)})
    return out


def norm(t):
    t = re.sub(r"(\.{2,}|…+)$", "", t)
    return re.sub(r"[\s\u3000·:：,，。.\-—_()（）\[\]【】]", "", t)


def relevant(title):
    return any(k in title for k in KEYWORDS)


def resolve(url):
    """华工 CMS 有“栏目参数错误”的重复链接，逐个换栏目号试探，返回可读的正文页"""
    cands = [url]
    m = re.search(r"/c(\d+)a(\d+)/page\.htm", url)
    if m:
        for col in ("16912", "16915", "39994", "29500", "25782"):
            cands.append(url.replace(f"c{m.group(1)}a", f"c{col}a", 1))
    for u in cands:
        try:
            html = fetch(u)
        except Exception:
            continue
        if "wp_articlecontent" in html and len(html) > 5000:
            return u, html
    return url, None


def extract_times(url):
    """抓正文，摘出含时间/条件的短句，给推送加信息量"""
    try:
        real, html = resolve(url)
    except Exception:
        return url, []
    if not html:
        return url, []
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    i = html.find("wp_articlecontent")
    if i > 0:
        start = html.rfind("<", 0, i)
        html = html[start:]
    text = clean(html)
    hits, seen = [], set()
    pat = re.compile(r"[^。；;]{0,26}(?:时间|截止|前完成|前提交|逾期)[^。；;]{0,60}")
    date_like = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}\s*年|\d{1,2}:\d{2}")
    for s in pat.findall(text):
        s = re.sub(r"\s+", "", s).strip("，,、")
        if len(s) < 6 or not date_like.search(s):
            continue
        if "咨询时间" in s or "序号" in s or "联系电话" in s:
            continue
        k = s[:24]
        if k in seen:
            continue
        seen.add(k)
        hits.append(s)
        if len(hits) >= 6:
            break
    return real, hits


def print_calendar(today):
    nxt = next_windows(today)
    if not nxt:
        return
    print("\n## 下一个关键窗口")
    for days, dt, label, conf in nxt:
        print(f"- 还有 {days} 天 · {dt} {label}")


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen": {}}


def save_state(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def main():
    args = sys.argv[1:]
    st = {"seen": {}} if "--reset" in args else load_state()
    first_run = not st["seen"]
    today = datetime.date.today()

    collected, errors = [], []
    for name, url in SOURCES:
        try:
            html = fetch(url)
            items = parse_eea(html) if "eea.gd.gov.cn" in url else parse_sce(html)
            for it in items:
                it["source"] = name
            collected.extend(items)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__} {e}")

    # 同一通知可能同时出现在多个栏目，按标题去重，保留最早出现的源
    uniq, seen_titles = [], set()
    for it in collected:
        k = norm(it["title"])
        if not k or k in seen_titles:
            continue
        seen_titles.add(k)
        uniq.append(it)

    new_items = []
    for it in uniq:
        fid = hashlib.md5((norm(it["title"])).encode("utf-8")).hexdigest()[:12]
        if fid not in st["seen"]:
            st["seen"][fid] = {"title": it["title"], "date": it["date"],
                               "url": it["url"], "first_seen": str(today)}
            if not first_run:
                new_items.append(it)
    save_state(st)

    print(f"# 华工自考通知监控  {today}")
    print(f"抓取 {len(uniq)} 条 / 源 {len(SOURCES)-len(errors)} 个可用")
    for e in errors:
        print(f"! 源异常 -> {e}")

    if "--all" in args:
        print("\n## 当前全部条目")
        for it in uniq:
            print(f"- [{it['date']}] {it['source']} | {it['title']}\n  {it['url']}")
        return

    if first_run:
        print("\n## 首次运行，已建立基线（不推送历史）。最近 8 条：")
        for it in sorted(uniq, key=lambda x: x["date"], reverse=True)[:8]:
            print(f"- [{it['date']}] {it['title']}")
        print_calendar(today)
        return

    hits = [it for it in new_items if relevant(it["title"])]
    muted = [it for it in new_items if not relevant(it["title"])]

    if not hits:
        print("\n本次无与自考/实践/论文/毕业/学位相关的新通知。")
        if muted:
            print(f"（另有 {len(muted)} 条无关通知已记录，不推送）")
        print_calendar(today)
        return

    print(f"\n## 新增相关通知 {len(hits)} 条")
    for it in sorted(hits, key=lambda x: x["date"], reverse=True):
        real, times = extract_times(it["url"])
        print(f"\n### [{it['date']}] {it['title']}")
        print(f"来源：{it['source']}")
        print(f"链接：{real}")
        if times:
            print("关键时间/条件：")
            for t in times:
                print(f"  - {t}")
    print_calendar(today)


if __name__ == "__main__":
    main()
