#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端版每日任务：运行 monitor.py -> 组装日报 -> 用 SMTP 发出。

与本地版的区别：
  - 发信不再依赖 Agent Mail 连接器（云端拿不到），改用标准 SMTP。
  - 需要环境变量：SMTP_USER / SMTP_PASS / MAIL_TO
    可选：SMTP_HOST（默认 smtp.qq.com）、SMTP_PORT（默认 465）

用法：
  python send_daily.py            # 正常抓取并发信
  python send_daily.py --dry-run  # 只打印将发送的邮件，不发信
  python send_daily.py --body-only # 只打印正文（调试用）
"""
import os
import re
import ssl
import sys
import smtplib
import datetime
import subprocess
from email.message import EmailMessage
from email.utils import formataddr, formatdate

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
MONITOR = os.path.join(BASE, "monitor.py")

TAIL = "本邮件由每日 09:00 自动任务发出；即使无新通知也会发送，用于证明监控正常运行。"

# 关键词 -> 对 080901（计算机科学与技术 专升本）考生的影响提示
HINTS = [
    (("论文", "毕业设计"), ("报名",), "论文报名窗口通常仅 3 天，错过要等下一批次（约 2 个月后）。"),
    (("论文", "毕业设计"), ("交稿", "提交", "答辩", "撰写"), "论文成绩需达良好（80 分）以上才可申请学位，交稿逾期不予安排答辩。"),
    (("实践",), ("报名",), "实践考核每批次最多报 5 门，建议按剩余科目优先排序。"),
    (("实践",), ("考核", "安排", "时间"), "实践考核一般安排在五山校区，需提前确认场次与携带材料。"),
    (("毕业",), ("申请", "办理"), "毕业申请仅在省系统开放期内受理（通常 5 天），逾期转入下一批次。"),
    (("学位",), ("申请", "办理"), "学位须在毕业后 6 个月内申请，且论文成绩达良好（80 分）以上。"),
]
DEFAULT_HINT = "请在截止前登录系统确认，逾期一般不予补报。"


def run_monitor():
    """跑 monitor.py，返回 (stdout, ok)"""
    try:
        p = subprocess.run([sys.executable, MONITOR], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300)
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
        return out.strip(), p.returncode == 0
    except Exception as e:
        return f"[monitor.py 执行失败] {type(e).__name__}: {e}", False


def hint_for(title):
    for kws1, kws2, text in HINTS:
        if any(k in title for k in kws1) and any(k in title for k in kws2):
            return text
    return DEFAULT_HINT


def parse(out):
    """从脚本输出里抽日期、抓取概况、源异常、新增条目、关键窗口"""
    info = {"date": "", "fetched": "", "errors": [], "items": [], "count": 0, "windows": []}
    m = re.search(r"^#\s*华工自考通知监控\s+(\d{4}-\d{2}-\d{2})", out, re.M)
    if m:
        info["date"] = m.group(1)
    m = re.search(r"^抓取\s*(\d+)\s*条\s*/\s*源\s*(\d+)\s*个可用", out, re.M)
    if m:
        info["fetched"] = f"抓取 {m.group(1)} 条 / 源 {m.group(2)} 个可用"
    info["errors"] = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("! 源异常")]

    m = re.search(r"^##\s*新增相关通知\s*(\d+)\s*条", out, re.M)
    if m:
        info["count"] = int(m.group(1))
        block = out[m.end():]
        block = block.split("## 下一个关键窗口")[0]
        for seg in re.split(r"^###\s+", block, flags=re.M)[1:]:
            lines = [x.strip() for x in seg.strip().splitlines() if x.strip()]
            head = lines[0]
            dm = re.match(r"\[([\d-]*)\]\s*(.+)", head)
            date = dm.group(1) if dm else ""
            title = dm.group(2) if dm else head
            url, times, mode = "", [], False
            for ln in lines[1:]:
                if ln.startswith("链接："):
                    url, mode = ln[3:].strip(), False
                elif ln.startswith("关键时间"):
                    mode = True
                elif mode and ln.startswith("- "):
                    times.append(ln[2:].strip())
                elif ln.startswith("关键时间/条件") or ln.startswith("来源："):
                    mode = False
            info["items"].append({"date": date, "title": title, "url": url, "times": times})

    m = re.search(r"^##\s*下一个关键窗口\s*$(.*)", out, re.M | re.S)
    if m:
        for ln in m.group(1).splitlines():
            if ln.strip().startswith("- 还有"):
                info["windows"].append(ln.strip())
    return info


def build(info, ok):
    today = info["date"] or str(datetime.date.today())
    new = info["count"]

    if not ok and not info["fetched"]:
        subject = f"【华工自考日报】{today} 运行异常 · 请人工检查"
        head = f"{today}：监控脚本运行异常，本次结果不可信（可能站点改版或网络故障）。"
    elif new > 0:
        subject = f"【华工自考日报】{today} 有 {new} 条新通知"
        head = f"{today}：发现 {new} 条与自考/实践考核/毕业论文/毕业/学位相关的新通知。"
    else:
        subject = f"【华工自考日报】{today} 已正常运行 · 无新增"
        head = f"{today}：监控正常运行，本次无新增。"

    body = [head, ""]
    body.append(f"抓取概况：{info['fetched']}" if info["fetched"] else "抓取概况：（脚本未返回抓取统计）")
    for e in info["errors"]:
        body.append(e)
        body.append("  提示：该源可能站点改版或临时不可用，本次内容可能有遗漏，可稍后手动查看官网。")

    if new > 0 and info["items"]:
        items = info["items"]
        shown = items[:4]
        body.append("")
        for it in shown:
            body.append(f"【{it['date']}】{it['title']}")
            if it["url"]:
                body.append(f"链接：{it['url']}")
            if it["times"]:
                body.append("关键时间：" + "；".join(it["times"][:3]))
            body.append("提示：" + hint_for(it["title"]))
        if len(items) > len(shown):
            body.append(f"（另有 {len(items) - len(shown)} 条，详见官网列表）")
    elif ok and new == 0:
        body.append("今天没有与自考/实践考核/毕业论文/毕业/学位相关的新通知。")

    if info["windows"]:
        body.append("")
        body.append("最近关键窗口：")
        body.extend(info["windows"][:2])

    body.append("")
    body.append(TAIL)

    text = "\n".join(body)
    return subject, text, today


def send(subject, text, to_addr):
    host = os.environ.get("SMTP_HOST", "smtp.qq.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")
    if not (user and pwd):
        raise RuntimeError("缺少 SMTP_USER / SMTP_PASS 环境变量（QQ邮箱需使用授权码，不是登录密码）")

    msg = EmailMessage()
    msg["From"] = formataddr(("华工自考监控", user))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text, charset="utf-8")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
        s.login(user, pwd)
        s.send_message(msg)


def main():
    args = sys.argv[1:]
    out, ok = run_monitor()
    print(out)
    print("\n" + "=" * 60)

    info = parse(out)
    subject, text, today = build(info, ok)
    to_addr = os.environ.get("MAIL_TO", os.environ.get("SMTP_USER", ""))

    print(f"主题：{subject}")
    print("-" * 60)
    print(text)
    print("-" * 60)
    print(f"正文 {len(text.splitlines())} 行")

    if "--dry-run" in args or "--body-only" in args:
        print("[dry-run] 未发送邮件")
        return 0
    try:
        send(subject, text, to_addr)
        print(f"[ok] 已发送至 {to_addr}")
        return 0
    except Exception as e:
        print(f"[失败] 邮件发送失败：{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
