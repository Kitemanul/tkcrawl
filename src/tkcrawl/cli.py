"""Click CLI 入口，子命令定义"""

import asyncio
import logging

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from tkcrawl import __version__
from tkcrawl.auth import login_interactive
from tkcrawl.client import DouyinClient
from tkcrawl.filters import VideoFilter
from tkcrawl.store import (
    save_comments,
    save_search_results,
    save_user_posts,
    save_user_profile,
    save_video,
)
from tkcrawl.utils import extract_aweme_id, extract_sec_user_id, setup_logging

console = Console()

# ---- 搜索筛选条件的 CLI 选项值 → API 参数值映射 ----
# 使用人类可读的选项名（latest/likes）映射到抖音 API 的数字参数
SORT_MAP = {
    "comprehensive": "0",  # 综合排序（默认）
    "likes": "1",          # 最多点赞
    "latest": "2",         # 最新发布
}
PUBLISH_TIME_MAP = {
    "any": "0",            # 不限（默认）
    "day": "1",            # 一天内
    "week": "7",           # 一周内
    "halfyear": "182",     # 六个月内
}
DURATION_MAP = {
    "any": "",             # 不限（默认）
    "short": "0",          # 1 分钟以内
    "medium": "1",         # 1-5 分钟
    "long": "2",           # 5 分钟以上
}


@click.group()
@click.version_option(__version__, prog_name="tkcrawl")
@click.option("--verbose", "-v", is_flag=True, help="显示详细日志")
def cli(verbose: bool):
    """tkcrawl - 抖音数据采集 CLI 工具"""
    setup_logging(verbose)


@cli.command()
@click.option("--cookie-path", default=None, help="Cookie 保存路径")
def login(cookie_path: str | None):
    """扫码登录抖音，保存 Cookie"""
    success = asyncio.run(login_interactive(cookie_path))
    if success:
        console.print("[green]登录成功！[/green]")
    else:
        console.print("[red]登录失败，请重试[/red]")
        raise SystemExit(1)


@cli.command()
@click.argument("url")
@click.option("--output", "-o", default="output", help="输出目录")
@click.option("--headless/--no-headless", default=True, help="无头模式")
@click.option("--cookie-path", default=None, help="Cookie 路径")
def video(url: str, output: str, headless: bool, cookie_path: str | None):
    """采集视频信息

    URL 可以是视频链接或视频 ID
    """
    aweme_id = extract_aweme_id(url)
    console.print(f"正在采集视频: {aweme_id}")

    async def _run():
        async with DouyinClient(
            headless=headless, cookie_path=cookie_path
        ) as client:
            with Progress(
                SpinnerColumn(), TextColumn("{task.description}"), console=console
            ) as progress:
                progress.add_task("获取视频详情...", total=None)
                info = await client.get_video(aweme_id)
            path = save_video(output, info)
            console.print(f"[green]视频信息已保存: {path}[/green]")
            console.print(f"  标题: {info.desc[:60]}")
            console.print(
                f"  播放: {info.stats.play_count}  "
                f"点赞: {info.stats.digg_count}  "
                f"评论: {info.stats.comment_count}"
            )

    asyncio.run(_run())


@cli.command()
@click.argument("url")
@click.option("--max-count", default=50, help="最大采集作品数")
@click.option("--output", "-o", default="output", help="输出目录")
@click.option("--headless/--no-headless", default=True, help="无头模式")
@click.option("--cookie-path", default=None, help="Cookie 路径")
def user(
    url: str,
    max_count: int,
    output: str,
    headless: bool,
    cookie_path: str | None,
):
    """采集用户信息及作品列表

    URL 可以是用户主页链接或 sec_user_id
    """
    sec_uid = extract_sec_user_id(url)
    console.print(f"正在采集用户: {sec_uid[:30]}...")

    async def _run():
        async with DouyinClient(
            headless=headless, cookie_path=cookie_path
        ) as client:
            with Progress(
                SpinnerColumn(), TextColumn("{task.description}"), console=console
            ) as progress:
                task = progress.add_task("获取用户信息...", total=None)
                profile = await client.get_user_profile(sec_uid)
                console.print(f"  用户: {profile.nickname}")
                console.print(
                    f"  粉丝: {profile.follower_count}  "
                    f"关注: {profile.following_count}  "
                    f"作品: {profile.aweme_count}"
                )

                progress.update(task, description="获取作品列表...")
                posts = await client.get_user_posts(sec_uid, max_count=max_count)

            profile_path = save_user_profile(output, profile)
            posts_path = save_user_posts(output, sec_uid, posts)
            console.print(f"[green]用户信息已保存: {profile_path}[/green]")
            console.print(f"[green]作品列表已保存({len(posts)}个): {posts_path}[/green]")

    asyncio.run(_run())


@cli.command()
@click.argument("url")
@click.option("--max-count", default=100, help="最大采集评论数")
@click.option("--with-replies", is_flag=True, help="同时采集评论回复")
@click.option("--output", "-o", default="output", help="输出目录")
@click.option("--headless/--no-headless", default=True, help="无头模式")
@click.option("--cookie-path", default=None, help="Cookie 路径")
def comments(
    url: str,
    max_count: int,
    with_replies: bool,
    output: str,
    headless: bool,
    cookie_path: str | None,
):
    """采集视频评论

    URL 可以是视频链接或视频 ID
    """
    aweme_id = extract_aweme_id(url)
    console.print(f"正在采集评论: {aweme_id}")

    async def _run():
        async with DouyinClient(
            headless=headless, cookie_path=cookie_path
        ) as client:
            with Progress(
                SpinnerColumn(), TextColumn("{task.description}"), console=console
            ) as progress:
                progress.add_task("获取评论...", total=None)
                comment_list = await client.get_comments(
                    aweme_id, max_count=max_count, with_replies=with_replies
                )
            path = save_comments(output, aweme_id, comment_list)
            console.print(f"[green]评论已保存({len(comment_list)}条): {path}[/green]")

    asyncio.run(_run())


@cli.command()
@click.argument("keyword")
@click.option(
    "--type",
    "search_type",
    type=click.Choice(["video", "user"]),
    default="video",
    help="搜索类型",
)
@click.option("--max-count", default=30, help="最大采集结果数（按过滤后计数）")
@click.option("--output", "-o", default="output", help="输出目录")
@click.option("--headless/--no-headless", default=True, help="无头模式")
@click.option("--cookie-path", default=None, help="Cookie 路径")
# ---- 第一层：请求端筛选（通过 UI 按钮控制搜索 API 返回范围）----
@click.option(
    "--sort",
    type=click.Choice(["comprehensive", "likes", "latest"]),
    default="comprehensive",
    show_default=True,
    help="排序方式：comprehensive 综合 / likes 最多点赞 / latest 最新发布",
)
@click.option(
    "--publish-time",
    type=click.Choice(["any", "day", "week", "halfyear"]),
    default="any",
    show_default=True,
    help="发布时间：any 不限 / day 一天内 / week 一周内 / halfyear 六个月内",
)
@click.option(
    "--duration",
    type=click.Choice(["any", "short", "medium", "long"]),
    default="any",
    show_default=True,
    help="视频时长：any 不限 / short <1分钟 / medium 1-5分钟 / long >5分钟",
)
# ---- 第二层：存储前客户端过滤 ----
@click.option("--min-likes", default=0, help="最小点赞数（0 = 不限）")
@click.option("--min-comments", default=0, help="最小评论数（0 = 不限）")
@click.option("--min-shares", default=0, help="最小分享数（0 = 不限）")
@click.option("--min-collects", default=0, help="最小收藏数（0 = 不限）")
@click.option("--min-followers", default=0, help="作者最小粉丝数（0 = 不限）")
@click.option("--max-followers", default=0, help="作者最大粉丝数（0 = 不限）")
@click.option(
    "--keyword",
    "keywords",
    multiple=True,
    help="标题/话题标签须含此词（可多次使用，OR 关系）",
)
@click.option(
    "--exclude-keyword",
    "exclude_keywords",
    multiple=True,
    help="标题/话题标签含此词则排除（可多次使用，OR 关系）",
)
@click.option("--verified-only", is_flag=True, help="仅保留已认证用户的视频")
# ---- 反爬速率控制 ----
@click.option(
    "--rate-limit",
    default=1.0,
    type=float,
    show_default=True,
    help="请求速率倍率（1.0=默认 3-8s，2.0=翻倍，长时间采集建议 1.5-2.0）",
)
def search(
    keyword: str,
    search_type: str,
    max_count: int,
    output: str,
    headless: bool,
    cookie_path: str | None,
    sort: str,
    publish_time: str,
    duration: str,
    min_likes: int,
    min_comments: int,
    min_shares: int,
    min_collects: int,
    min_followers: int,
    max_followers: int,
    keywords: tuple[str, ...],
    exclude_keywords: tuple[str, ...],
    verified_only: bool,
    rate_limit: float,
):
    """搜索视频或用户

    KEYWORD 为搜索关键词

    搜索容易触发验证码，建议使用 --no-headless 模式。

    \b
    示例：
      # 搜索 Python，按最新发布排序，只保留点赞 > 1000 的视频
      tkcrawl search Python --sort latest --min-likes 1000

      # 搜索教程类视频，只保留 1-5 分钟时长、粉丝 > 10 万的作者
      tkcrawl search 教程 --duration medium --min-followers 100000

      # 搜索最近一周的内容，排除广告关键词
      tkcrawl search 美食 --publish-time week --exclude-keyword 广告 --exclude-keyword 推广
    """
    # 将 CLI 可读选项值转换为 API 参数值
    sort_type = SORT_MAP[sort]
    publish_time_val = PUBLISH_TIME_MAP[publish_time]
    filter_duration = DURATION_MAP[duration]

    # 构建客户端过滤器
    vf = VideoFilter(
        min_likes=min_likes,
        min_comments=min_comments,
        min_shares=min_shares,
        min_collects=min_collects,
        min_followers=min_followers,
        max_followers=max_followers,
        keywords=list(keywords),
        exclude_keywords=list(exclude_keywords),
        verified_only=verified_only,
    )

    console.print(f"正在搜索: [bold]{keyword}[/bold]（类型: {search_type}）")

    # 打印已启用的筛选条件摘要
    if sort != "comprehensive":
        console.print(f"  排序: {sort}")
    if publish_time != "any":
        console.print(f"  发布时间: {publish_time}")
    if duration != "any":
        console.print(f"  视频时长: {duration}")
    if not vf.is_empty():
        console.print(f"  客户端过滤: {vf.describe()}")
    if rate_limit != 1.0:
        console.print(f"  速率倍率: {rate_limit}x")

    if headless:
        console.print(
            "[yellow]提示：搜索容易触发验证码，"
            "如采集失败请加 --no-headless[/yellow]"
        )

    async def _run():
        async with DouyinClient(
            headless=headless, cookie_path=cookie_path, rate_limit=rate_limit
        ) as client:
            with Progress(
                SpinnerColumn(), TextColumn("{task.description}"), console=console
            ) as progress:
                progress.add_task("搜索中...", total=None)
                results = await client.search(
                    keyword,
                    search_type=search_type,
                    max_count=max_count,
                    sort_type=sort_type,
                    publish_time=publish_time_val,
                    filter_duration=filter_duration,
                    video_filter=vf if not vf.is_empty() else None,
                )
            path = save_search_results(output, keyword, results)
            console.print(f"[green]搜索结果已保存({len(results)}条): {path}[/green]")

    asyncio.run(_run())


@cli.command()
@click.option("--max-count", default=20, help="最大采集视频数（按过滤后计数）")
@click.option("--output", "-o", default="output", help="输出目录")
@click.option("--headless/--no-headless", default=True, help="无头模式")
@click.option("--cookie-path", default=None, help="Cookie 路径")
# ---- 第二层：存储前客户端过滤 ----
@click.option("--min-likes", default=0, help="最小点赞数（0 = 不限）")
@click.option("--min-comments", default=0, help="最小评论数（0 = 不限）")
@click.option("--min-shares", default=0, help="最小分享数（0 = 不限）")
@click.option("--min-collects", default=0, help="最小收藏数（0 = 不限）")
@click.option("--min-followers", default=0, help="作者最小粉丝数（0 = 不限）")
@click.option("--max-followers", default=0, help="作者最大粉丝数（0 = 不限）")
@click.option(
    "--keyword",
    "keywords",
    multiple=True,
    help="标题/话题标签须含此词（可多次使用，OR 关系）",
)
@click.option(
    "--exclude-keyword",
    "exclude_keywords",
    multiple=True,
    help="标题/话题标签含此词则排除（可多次使用，OR 关系）",
)
@click.option("--verified-only", is_flag=True, help="仅保留已认证用户的视频")
# ---- 反爬速率控制 ----
@click.option(
    "--rate-limit",
    default=1.0,
    type=float,
    show_default=True,
    help="请求速率倍率（1.0=默认 3-8s，2.0=翻倍，长时间采集建议 1.5-2.0）",
)
def feed(
    max_count: int,
    output: str,
    headless: bool,
    cookie_path: str | None,
    min_likes: int,
    min_comments: int,
    min_shares: int,
    min_collects: int,
    min_followers: int,
    max_followers: int,
    keywords: tuple[str, ...],
    exclude_keywords: tuple[str, ...],
    verified_only: bool,
    rate_limit: float,
):
    """刷推荐流，自动滑动采集视频

    max_count 按过滤后的有效条数计算，设置过滤条件后程序会持续刷到满足数量为止。

    \b
    示例：
      # 刷 50 个视频，只保留点赞 > 5000、粉丝 > 5 万的
      tkcrawl feed --max-count 50 --min-likes 5000 --min-followers 50000

      # 放慢速率采集（适合长时间运行）
      tkcrawl feed --max-count 200 --rate-limit 2.0
    """
    # 构建客户端过滤器
    vf = VideoFilter(
        min_likes=min_likes,
        min_comments=min_comments,
        min_shares=min_shares,
        min_collects=min_collects,
        min_followers=min_followers,
        max_followers=max_followers,
        keywords=list(keywords),
        exclude_keywords=list(exclude_keywords),
        verified_only=verified_only,
    )

    console.print(
        f"开始刷推荐流，目标 [bold]{max_count}[/bold] 个视频（速率 {rate_limit}x）"
    )
    if not vf.is_empty():
        console.print(f"  过滤条件: {vf.describe()}")
        console.print(
            "  [dim]注意：过滤条件启用时，程序会持续刷直到保存足够数量，实际滑动次数可能更多[/dim]"
        )

    async def _run():
        async with DouyinClient(
            headless=headless, cookie_path=cookie_path, rate_limit=rate_limit
        ) as client:
            seen_authors: set[str] = set()

            async def on_video(vid, idx):
                # 补全作者信息（同一作者只请求一次）
                if vid.author.sec_uid and vid.author.sec_uid not in seen_authors:
                    seen_authors.add(vid.author.sec_uid)
                    console.print(
                        f"  [dim]正在获取作者 {vid.author.nickname} 的详细信息...[/dim]"
                    )
                    await client.enrich_author(vid)

                desc = vid.desc[:40] if vid.desc else "(无标题)"
                fans = vid.author.follower_count
                fans_str = f"  粉丝: {fans:,}" if fans else ""
                verified_mark = " ✓" if vid.author.verified else ""
                console.print(
                    f"  [cyan]#{idx}[/cyan] {desc}  "
                    f"[dim]❤ {vid.stats.digg_count:,}{fans_str}{verified_mark}[/dim]"
                )
                save_video(output, vid)

            videos = await client.crawl_feed(
                max_count=max_count,
                on_video=on_video,
                video_filter=vf if not vf.is_empty() else None,
            )
            console.print(
                f"\n[green]采集完成！共保存 {len(videos)} 个视频，"
                f"保存在 {output}/videos/[/green]"
            )

    asyncio.run(_run())


@cli.command()
@click.option("--host", default="127.0.0.1", help="监听地址")
@click.option("--port", default=8000, type=int, help="监听端口")
def web(host: str, port: int):
    """启动知空 Web 界面"""
    import uvicorn

    console.print(f"[bold]知空[/bold] Web 界面启动中...")
    console.print(f"[blue]-> http://{host}:{port}[/blue]")
    uvicorn.run("tkcrawl.server:app", host=host, port=port)


if __name__ == "__main__":
    cli()
