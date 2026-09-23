"""命令行入口。

用法：
  finfluencer-digest --check                 检查 Gemini 密钥、模型、邮箱、频道是否都能用
  finfluencer-digest --list                  只列出待处理的新视频，不调用 Gemini
  finfluencer-digest --limit 1 --dry-run     只处理 1 个视频，日报存本地、不发邮件
  finfluencer-digest                         正式运行（云端每天自动跑的就是这一条）
  finfluencer-digest --find-channel @某博主   查某个 YouTube 博主的频道 ID，方便加进 config.yaml
  finfluencer-digest gate                    定时任务用：打印 run=true 或 run=false，判断现在该不该运行
以上都可以加 --config PATH（默认 ./config.yaml）；state/、data/、digests/ 和持仓文件都在配置文件所在的目录。

  finfluencer-digest --demo [--out DIR]      演示：用包内虚构的频道、帖子和录好的模型回复跑完整流程，
                                             不需要配置文件、密钥和网络；日报写到 DIR（默认 ./demo-output/）并打印
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import demo, x_posts
from .aggregate import call_rows
from .check import check
from .config import LOCAL_TZ, load_config, load_env
from .gate import should_run
from .llm import Gemini, short_error
from .mailer import send_email
from .render import build_digest
from .state import Paths, load_seen, save_calls, save_seen
from .summarize import summarize_video, summarize_x, synthesize, x_channel_name
from .youtube import find_channel_id, find_new_videos, videos_from_rss

SUBCOMMANDS = ("run", "gate")
CONFIG_HELP = "配置文件（默认 ./config.yaml）；state/、data/、digests/ 都放在它所在的目录"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finfluencer-digest", description="财经博主日报")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="生成并发送日报（默认子命令，可以省略）", description="财经博主日报",
                       epilog="另有子命令 gate：finfluencer-digest gate [--config PATH]，"
                              "定时任务用它判断现在该不该运行。")
    p.add_argument("--config", type=Path, default=Path("config.yaml"), metavar="PATH", help=CONFIG_HELP)
    p.add_argument("--check", action="store_true", help="检查密钥、模型、邮箱、频道")
    p.add_argument("--list", action="store_true", help="只列出新视频")
    p.add_argument("--limit", type=int, help="最多处理几个视频（测试用）")
    p.add_argument("--dry-run", action="store_true", help="不发邮件、不记录已处理")
    p.add_argument("--find-channel", metavar="@handle", help="查频道 ID")
    p.add_argument("--channel", metavar="名字", help="只处理名字里包含这几个字的博主（测试新博主用）")
    p.add_argument("--lookback-hours", type=int, help="临时改：看最近多少小时的视频")
    p.add_argument("--per-channel", type=int, help="临时改：每位博主最多处理几个视频")
    p.add_argument("--max-minutes", type=int, help="临时改：每个视频最多看几分钟")
    p.add_argument("--portfolio-only", action="store_true", help="只生成“我的持仓”部分并打印（测试用，不发邮件）")
    p.add_argument("--demo", action="store_true",
                   help="演示：用包内虚构的频道和录好的模型回复跑完整流程，不需要配置文件、密钥和网络，不发邮件、不写记录")
    p.add_argument("--out", type=Path, default=Path("demo-output"), metavar="DIR",
                   help="演示模式的日报写到哪个目录（默认 ./demo-output/）")

    g = sub.add_parser("gate", help="定时任务用：判断现在该不该运行",
                       description="打印 run=true 或 run=false，不运行时第二行是原因。"
                                   "英国时间已过 8 点、且今天的日报还没发，才是 run=true。")
    g.add_argument("--config", type=Path, default=Path("config.yaml"), metavar="PATH", help=CONFIG_HELP)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in SUBCOMMANDS:
        argv.insert(0, "run")  # 默认子命令：finfluencer-digest --check 等价于 finfluencer-digest run --check
    args = build_parser().parse_args(argv)
    if args.command == "gate":
        return gate_command(args.config)
    return run_command(args)


def gate_command(config: Path) -> int:
    """总是返回 0；该不该运行看打印的第一行。"""
    ok, why = should_run(dt.datetime.now(LOCAL_TZ), Paths.from_config(config).digest_dir)
    print(f"run={'true' if ok else 'false'}")
    if why:
        print(why)
    return 0


def _portfolio_section(cfg: dict, llm: Gemini, today_rows: list[dict], paths: Paths) -> tuple[str, set[str]]:
    from .portfolio import build_section  # 持仓是可选功能（要装 [portfolio] 附加依赖），用到时才导入

    return build_section(cfg, llm.ask, today_rows, holdings_root=paths.holdings_root)


@dataclass
class Runtime:
    """主流程用到的外部环境：正式运行在 run_command 里组装，演示模式由 demo_runtime 组装。
    两者共用同一个主流程 run_digest。"""

    cfg: dict
    llm: Gemini
    now: dt.datetime  # 带时区的当前时间；时间窗口、处理日期和日报日期（英国时间）都由它算
    digest_dir: Path  # 日报写到这里
    seen: dict = field(default_factory=dict)  # 处理过的视频和帖子
    fetch_rss: Callable[[str], list[dict]] = videos_from_rss  # 频道 ID → 视频列表
    fetch_x: Callable[[str], dict] = x_posts.fetch_statuses  # X 账号 → 帖子接口的原始回复
    yt_key: str | None = None  # RSS 失效时改用 YouTube 官方接口的密钥
    watch: frozenset[str] = frozenset()  # 你关注的股票
    paths: Paths | None = None  # 持仓文件和 state/、data/ 的位置；演示模式没有
    demo: bool = False  # 演示模式：不发邮件、不写记录，把日报打印出来


def apply_overrides(cfg: dict, args: argparse.Namespace) -> None:
    """命令行的临时改动（--lookback-hours、--per-channel、--max-minutes）和 --channel 筛选，直接改 cfg。"""
    for key, value in (("lookback_hours", args.lookback_hours), ("max_videos_per_channel", args.per_channel),
                       ("max_minutes", args.max_minutes)):
        if value:
            cfg[key] = value
    if args.channel:
        key = args.channel.lower()
        cfg["channels"] = [ch for ch in cfg["channels"] if key in ch["name"].lower()]
        cfg["x_accounts"] = [a for a in cfg.get("x_accounts") or []
                             if key in a["name"].lower() or key in a["handle"].lower()]


def run_command(args: argparse.Namespace) -> int:
    if args.demo:
        print("演示模式：频道、帖子和模型回复都是包内录好的虚构内容，不联网、不需要密钥。")
        return run_digest(demo_runtime(args), args)
    paths = Paths.from_config(args.config)
    load_env(paths.root)
    cfg = load_config(args.config)

    if args.find_channel:
        cid = find_channel_id(args.find_channel)
        print(cid or "没找到，请检查 @ 后面的名字是否和频道网址一致")
        return 0 if cid else 1
    if args.check:
        ok = check(cfg, api_key=os.getenv("GEMINI_API_KEY"), sender=os.getenv("GMAIL_ADDRESS"),
                   password=os.getenv("GMAIL_APP_PASSWORD"), email_to=os.getenv("EMAIL_TO"))
        return 0 if ok else 1

    apply_overrides(cfg, args)
    llm = Gemini(os.environ["GEMINI_API_KEY"])

    if args.portfolio_only:
        print(_portfolio_section(cfg, llm, [], paths)[0])
        print("\n" + llm.usage_text())
        return 0

    watch = frozenset(x.strip().upper() for x in os.getenv("WATCHLIST", "").split(",") if x.strip())
    rt = Runtime(cfg=cfg, llm=llm, now=dt.datetime.now(dt.UTC), digest_dir=paths.digest_dir, seen=load_seen(paths),
                 yt_key=os.getenv("YOUTUBE_API_KEY"), watch=watch, paths=paths)
    return run_digest(rt, args)


def demo_runtime(args: argparse.Namespace) -> Runtime:
    """演示模式：包内的虚构素材和固定的当前时间。不读环境变量和 .env，不读写 state/、data/，只写 --out。"""
    d = demo.load_demo()
    apply_overrides(d.cfg, args)
    return Runtime(cfg=d.cfg, llm=d.llm, now=d.now, digest_dir=args.out, fetch_rss=d.fetch_rss, fetch_x=d.fetch_x,
                   demo=True)


def run_digest(rt: Runtime, args: argparse.Namespace) -> int:
    """主流程（正式运行和演示模式共用）：找新视频和帖子 → Gemini 整理 → 汇总 → 写日报 → 发邮件并记录已处理。"""
    cfg, llm, seen = rt.cfg, rt.llm, rt.seen
    now_london = rt.now.astimezone(LOCAL_TZ)
    videos, failures = find_new_videos(cfg, seen, now=rt.now, fetch_rss=rt.fetch_rss, yt_key=rt.yt_key)
    if args.limit:
        videos = videos[: args.limit]
    print(f"找到 {len(videos)} 个新视频")
    for v in videos:
        print(f"  - [{v['channel']}] {v['title']}")
    since = rt.now - dt.timedelta(hours=cfg["lookback_hours"])
    x_batches = []
    for acc in cfg.get("x_accounts") or []:
        try:
            posts = x_posts.recent_posts(acc["handle"], since, seen, fetch=rt.fetch_x)
        except Exception as e:
            failures.append(f"X 账号 @{acc['handle']} 读取失败（免费接口可能已失效）：{short_error(e)}")
            continue
        print(f"  - [X @{acc['handle']}] {len(posts)} 条新帖")
        if posts:
            x_batches.append((acc, posts))
    if args.list:
        return 0

    today = now_london.date().isoformat()
    results, skipped = [], []

    def work(v):
        started = time.time()
        return summarize_video(llm, cfg, v) + (time.time() - started,)

    workers = cfg.get("max_workers", 3)
    print(f"Gemini 开始看视频（同时 {workers} 个）…", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, v): v for v in videos}
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                s, model, meta, secs = fut.result()
            except Exception as e:
                failures.append(f"{v['channel']}《{v['title']}》：{short_error(e)}")
                print(f"  ✗ {v['channel']}《{v['title'][:30]}》：{short_error(e)}", flush=True)
                continue
            print(f"  ✓ {v['channel']}《{v['title'][:30]}》（{model}，{secs:.0f} 秒）", flush=True)
            llm.add_usage(model, meta)
            seen[v["id"]] = today
            (results if s.is_market_related else skipped).append((v, s))

    for acc, posts in x_batches:
        try:
            v, s = summarize_x(llm, cfg, acc, posts, today)
        except Exception as e:
            failures.append(f"{x_channel_name(acc)}：{short_error(e)}")
            continue
        print(f"  ✓ {v['channel']}（{len(posts)} 条帖子）", flush=True)
        for post in posts:
            seen[post["key"]] = today
        (results if s.is_market_related else skipped).append((v, s))

    names = [ch["name"] for ch in cfg["channels"]] + [x_channel_name(a) for a in cfg.get("x_accounts") or []]
    rank = {name: i for i, name in enumerate(names)}
    results.sort(key=lambda r: (rank[r[0]["channel"]], r[0]["published"]))
    synth = synthesize(llm, cfg, results) if results else ""
    rows = call_rows(today, results)
    portfolio_md, held = "", set()
    if cfg.get("portfolio", {}).get("enabled"):
        print("生成持仓部分…", flush=True)
        portfolio_md, held = _portfolio_section(cfg, llm, rows, rt.paths)
    md = build_digest(now_london.strftime("%Y-%m-%d"), results, synth, failures, skipped, rt.watch, llm.usage_text(),
                      portfolio_md, frozenset(held))

    rt.digest_dir.mkdir(parents=True, exist_ok=True)
    path = rt.digest_dir / f"{now_london:%Y-%m-%d}.md"
    if path.exists():
        path = rt.digest_dir / f"{now_london:%Y-%m-%d-%H%M}.md"
    path.write_text(md, encoding="utf-8")
    print(f"日报已保存：{path if rt.demo else path.relative_to(rt.paths.root)}")

    if rt.demo:
        print(f"演示模式：没有发邮件，也没有记录已处理的视频。日报全文：\n\n{md}")
    elif args.dry_run:
        print("试运行：没有发邮件，也没有记录已处理的视频")
    elif videos or failures or cfg.get("send_when_empty", True):
        sender = os.environ["GMAIL_ADDRESS"]
        to = [x.strip() for x in (os.getenv("EMAIL_TO") or sender).split(",") if x.strip()]
        subject = f"{'投资日报' if portfolio_md else '财经博主日报'} {now_london:%m-%d}｜{len(results)} 条博主更新"
        send_email(subject, md, sender=sender, password=os.environ["GMAIL_APP_PASSWORD"], to=to)
        print("✓ 邮件已发送")
        save_seen(rt.paths, seen, now_london.date())
        save_calls(rt.paths, rows)
    # 有视频却一个都没处理成功时返回失败，GitHub 会发邮件提醒
    return 1 if videos and not results and not skipped else 0


if __name__ == "__main__":
    sys.exit(main())
