"""Click CLI 入口，子命令定义"""

import asyncio
import logging

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from tkcrawl import __version__
from tkcrawl.auth import login_interactive
from tkcrawl.client import DouyinClient
from tkcrawl.store import (
    save_comments,
    save_search_results,
    save_user_posts,
    save_user_profile,
    save_video,
)
from tkcrawl.utils import extract_aweme_id, extract_sec_user_id, setup_logging

console = Console()


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
@click.option("--delay", default=2.0, help="请求间隔秒数")
@click.option("--cookie-path", default=None, help="Cookie 路径")
def video(url: str, output: str, headless: bool, delay: float, cookie_path: str | None):
    """采集视频信息

    URL 可以是视频链接或视频 ID
    """
    aweme_id = extract_aweme_id(url)
    console.print(f"正在采集视频: {aweme_id}")

    async def _run():
        async with DouyinClient(
            headless=headless, delay=delay, cookie_path=cookie_path
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
@click.option("--delay", default=2.0, help="请求间隔秒数")
@click.option("--cookie-path", default=None, help="Cookie 路径")
def user(
    url: str,
    max_count: int,
    output: str,
    headless: bool,
    delay: float,
    cookie_path: str | None,
):
    """采集用户信息及作品列表

    URL 可以是用户主页链接或 sec_user_id
    """
    sec_uid = extract_sec_user_id(url)
    console.print(f"正在采集用户: {sec_uid[:30]}...")

    async def _run():
        async with DouyinClient(
            headless=headless, delay=delay, cookie_path=cookie_path
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
@click.option("--delay", default=2.0, help="请求间隔秒数")
@click.option("--cookie-path", default=None, help="Cookie 路径")
def comments(
    url: str,
    max_count: int,
    with_replies: bool,
    output: str,
    headless: bool,
    delay: float,
    cookie_path: str | None,
):
    """采集视频评论

    URL 可以是视频链接或视频 ID
    """
    aweme_id = extract_aweme_id(url)
    console.print(f"正在采集评论: {aweme_id}")

    async def _run():
        async with DouyinClient(
            headless=headless, delay=delay, cookie_path=cookie_path
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
@click.option("--max-count", default=30, help="最大采集结果数")
@click.option("--output", "-o", default="output", help="输出目录")
@click.option("--headless/--no-headless", default=True, help="无头模式")
@click.option("--delay", default=2.0, help="请求间隔秒数")
@click.option("--cookie-path", default=None, help="Cookie 路径")
def search(
    keyword: str,
    search_type: str,
    max_count: int,
    output: str,
    headless: bool,
    delay: float,
    cookie_path: str | None,
):
    """搜索视频或用户

    KEYWORD 为搜索关键词
    """
    console.print(f"正在搜索: {keyword} (类型: {search_type})")

    async def _run():
        async with DouyinClient(
            headless=headless, delay=delay, cookie_path=cookie_path
        ) as client:
            with Progress(
                SpinnerColumn(), TextColumn("{task.description}"), console=console
            ) as progress:
                progress.add_task("搜索中...", total=None)
                results = await client.search(
                    keyword, search_type=search_type, max_count=max_count
                )
            path = save_search_results(output, keyword, results)
            console.print(f"[green]搜索结果已保存({len(results)}条): {path}[/green]")

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
