"""登录、Cookie 管理、扫码登录"""

import json
import logging
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

logger = logging.getLogger("tkcrawl")

DEFAULT_COOKIE_PATH = "cookies/douyin_cookies.json"


async def get_cookie_path(cookie_path: str | None = None) -> Path:
    path = Path(cookie_path or DEFAULT_COOKIE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def save_cookies(context: BrowserContext, cookie_path: str | None = None) -> Path:
    """保存浏览器 cookies 到文件"""
    path = await get_cookie_path(cookie_path)
    cookies = await context.cookies()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    logger.info(f"Cookie 已保存到 {path}")
    return path


async def load_cookies(context: BrowserContext, cookie_path: str | None = None) -> bool:
    """从文件加载 cookies 到浏览器上下文"""
    path = await get_cookie_path(cookie_path)
    if not path.exists():
        logger.info("未找到已保存的 Cookie")
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        logger.info(f"已加载 Cookie: {path}")
        return True
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"加载 Cookie 失败: {e}")
        return False


async def check_login(page) -> bool:
    """检查当前页面是否已登录"""
    try:
        cookies = await page.context.cookies("https://www.douyin.com")
        cookie_names = {c["name"] for c in cookies}
        # sessionid 是登录态的关键标识
        return "sessionid" in cookie_names
    except Exception:
        return False


async def login_interactive(cookie_path: str | None = None) -> bool:
    """交互式扫码登录

    打开浏览器窗口，用户扫码登录后自动保存 Cookie。
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        logger.info("正在打开抖音登录页面...")
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded")

        # 等待用户登录 - 检测 sessionid cookie 出现
        logger.info("请在浏览器中完成登录（扫码或手动登录）...")
        logger.info("登录成功后将自动保存 Cookie，最多等待 120 秒")

        for _ in range(120):
            if await check_login(page):
                await save_cookies(context, cookie_path)
                logger.info("登录成功！Cookie 已保存")
                await browser.close()
                return True
            await page.wait_for_timeout(1000)

        logger.error("登录超时，请重试")
        await browser.close()
        return False
