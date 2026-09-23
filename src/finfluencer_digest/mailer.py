"""用 Gmail 发日报：同时附纯文本和 HTML 两个版本。"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .render import to_html


def send_email(subject: str, md: str, *, sender: str, password: str, to: list[str]) -> None:
    """password 是 Gmail 应用专用密码（中间的空格会自动去掉）。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
    msg.attach(MIMEText(md, "plain", "utf-8"))
    msg.attach(MIMEText(to_html(md), "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(sender, password.replace(" ", ""))
        s.sendmail(sender, to, msg.as_string())
