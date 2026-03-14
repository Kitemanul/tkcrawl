"""通用工具函数"""

import asyncio
import logging
import random
import re

logger = logging.getLogger("tkcrawl")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def extract_aweme_id(url_or_id: str) -> str:
    """从 URL 或纯 ID 中提取视频 aweme_id"""
    # 纯数字 ID
    if re.match(r"^\d+$", url_or_id.strip()):
        return url_or_id.strip()

    # 从 URL 中提取: /video/1234567890
    match = re.search(r"/video/(\d+)", url_or_id)
    if match:
        return match.group(1)

    # 从 URL 中提取: /note/1234567890
    match = re.search(r"/note/(\d+)", url_or_id)
    if match:
        return match.group(1)

    return url_or_id.strip()


def extract_sec_user_id(url_or_id: str) -> str:
    """从 URL 或纯 ID 中提取用户 sec_user_id"""
    # 已经是 sec_uid 格式
    if url_or_id.startswith("MS4wLj"):
        return url_or_id.strip()

    # 从 URL 中提取: /user/MS4wLjABAAAA...
    match = re.search(r"/user/([A-Za-z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)

    return url_or_id.strip()


async def retry_async(
    coro_func,
    max_retries: int = 3,
    base_delay: float = 2.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
):
    """异步重试装饰器，支持指数退避"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return await coro_func()
        except retry_on as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"请求失败 (尝试 {attempt + 1}/{max_retries}): {e}，"
                    f"{delay:.1f}s 后重试"
                )
                await asyncio.sleep(delay)
    raise last_exc


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
