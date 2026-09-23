"""--check：逐项检查 Gemini 密钥和模型、Gmail 登录、各频道和 X 账号能不能读到。"""
from __future__ import annotations

import datetime as dt
import smtplib

from . import x_posts
from .llm import Gemini, short_error
from .youtube import videos_from_rss


def check(cfg: dict, *, api_key: str | None, sender: str | None, password: str | None, email_to: str | None) -> bool:
    ok = True
    if not api_key:
        print("✗ 没有找到 GEMINI_API_KEY")
        ok = False
    else:
        llm = Gemini(api_key)
        working = set()
        for model in dict.fromkeys(cfg["video_models"] + cfg["synth_models"]):
            try:
                llm._call(model, contents="只回复两个字：你好")  # 每个模型只试一次、不重试
                working.add(model)
                print(f"✓ Gemini 模型 {model} 可用")
            except Exception as e:
                hint = "（Pro 模型需要开通付费才能用，不影响运行，会自动换下一个）" if "pro" in model else ""
                print(f"✗ Gemini 模型 {model} 不可用：{short_error(e)}{hint}")
        for role in ("video_models", "synth_models"):
            if not working & set(cfg[role]):
                ok = False
                print(f"✗ config.yaml 里 {role} 的模型全都不可用")

    if not (sender and password):
        print("✗ 没有找到 GMAIL_ADDRESS 或 GMAIL_APP_PASSWORD")
        ok = False
    else:
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
                s.login(sender, password.replace(" ", ""))
            print(f"✓ Gmail 登录成功（{sender}），日报会发到：{email_to or sender}")
        except Exception as e:
            ok = False
            print(f"✗ Gmail 登录失败：{short_error(e)}")

    for ch in cfg["channels"]:
        try:
            print(f"✓ 频道「{ch['name']}」：最近 {len(videos_from_rss(ch['id']))} 个视频")
        except Exception as e:
            print(f"✗ 频道「{ch['name']}」获取失败：{short_error(e)}")
    for acc in cfg.get("x_accounts") or []:
        try:
            n = len(x_posts.recent_posts(acc["handle"], dt.datetime.now(dt.UTC) - dt.timedelta(days=3), {}))
            print(f"✓ X 账号 @{acc['handle']}：最近 3 天 {n} 条原创帖")
        except Exception as e:
            print(f"✗ X 账号 @{acc['handle']} 读取失败：{short_error(e)}")
    print("\n全部通过 ✓" if ok else "\n有问题，请按上面的 ✗ 提示修改 .env")
    return ok
