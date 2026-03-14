"""DouyinClient 核心类 — Playwright + httpx"""

import asyncio
import logging
from urllib.parse import urlencode

import httpx
from playwright.async_api import Page, async_playwright

from tkcrawl.auth import load_cookies, save_cookies
from tkcrawl.endpoints import (
    BASE_URL,
    ENDPOINTS,
    build_comment_list_params,
    build_comment_reply_params,
    build_search_params,
    build_user_posts_params,
    build_user_profile_params,
    build_video_detail_params,
)
from tkcrawl.models import Comment, SearchResult, UserInfo, VideoInfo
from tkcrawl.utils import get_random_user_agent

logger = logging.getLogger("tkcrawl")


class DouyinClient:
    """抖音数据采集核心客户端

    核心流程：
    1. 启动 Playwright chromium，加载已保存的 cookies
    2. 访问 douyin.com 建立浏览器上下文
    3. 通过 page.evaluate() 调用页面内 JS 生成签名参数
    4. 用签名参数 + httpx 发起 API 请求
    5. 解析响应数据，返回 Pydantic 模型
    """

    def __init__(
        self,
        headless: bool = True,
        delay: float = 2.0,
        cookie_path: str | None = None,
    ):
        self.headless = headless
        self.delay = delay
        self.cookie_path = cookie_path
        self._playwright = None
        self._browser = None
        self._context = None
        self._page: Page | None = None
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        """启动浏览器和 HTTP 客户端"""
        self._playwright = await async_playwright().start()
        ua = get_random_user_agent()

        self._browser = await self._playwright.chromium.launch(
            headless=self.headless
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=ua,
        )

        # 加载已保存的 cookies
        await load_cookies(self._context, self.cookie_path)

        self._page = await self._context.new_page()
        logger.info("正在初始化浏览器上下文...")
        await self._page.goto(BASE_URL, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(2000)

        # 提取 cookies 供 httpx 使用
        cookies = await self._context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}

        self._http = httpx.AsyncClient(
            headers={
                "User-Agent": ua,
                "Referer": BASE_URL,
                "Accept": "application/json, text/plain, */*",
            },
            cookies=cookie_dict,
            timeout=30.0,
            follow_redirects=True,
        )
        logger.info("客户端初始化完成")

    async def close(self) -> None:
        """关闭所有资源"""
        if self._http:
            await self._http.aclose()
        if self._context:
            # 保存最新 cookies
            await save_cookies(self._context, self.cookie_path)
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _sign_url(self, url: str) -> str:
        """通过 Playwright 页面 JS 环境生成签名 URL

        利用浏览器上下文中已加载的抖音 JS SDK 生成 X-Bogus 签名。
        """
        if not self._page:
            raise RuntimeError("客户端未初始化，请先调用 start()")

        try:
            signed = await self._page.evaluate(
                """(url) => {
                    // 尝试使用页面内的签名函数
                    if (window._sign) {
                        return window._sign(url);
                    }
                    if (window.byted_acrawler && window.byted_acrawler.sign) {
                        return window.byted_acrawler.sign({url: url});
                    }
                    return url;
                }""",
                url,
            )
            if isinstance(signed, dict):
                # 有些签名函数返回 {url: ..., "X-Bogus": ...}
                return signed.get("url", url)
            if isinstance(signed, str) and signed:
                return signed
        except Exception as e:
            logger.debug(f"JS 签名失败，使用原始 URL: {e}")

        return url

    async def _request(self, endpoint: str, params: dict) -> dict:
        """发起签名 API 请求"""
        url = f"{endpoint}?{urlencode(params)}"
        signed_url = await self._sign_url(url)

        resp = await self._http.get(signed_url)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status_code") != 0:
            status = data.get("status_code")
            msg = data.get("status_msg", "未知错误")
            logger.warning(f"API 返回异常: status_code={status}, msg={msg}")

        return data

    async def _delay(self) -> None:
        if self.delay > 0:
            await asyncio.sleep(self.delay)

    # ---- 视频详情 ----

    async def get_video(self, aweme_id: str) -> VideoInfo:
        """获取视频详情"""
        params = build_video_detail_params(aweme_id)
        data = await self._request(ENDPOINTS["video_detail"], params)
        aweme = data.get("aweme_detail", {})
        return VideoInfo.from_aweme(aweme)

    # ---- 用户信息 ----

    async def get_user_profile(self, sec_user_id: str) -> UserInfo:
        """获取用户信息"""
        params = build_user_profile_params(sec_user_id)
        data = await self._request(ENDPOINTS["user_profile"], params)
        return UserInfo.from_user_data(data)

    async def get_user_posts(
        self, sec_user_id: str, max_count: int = 50
    ) -> list[VideoInfo]:
        """获取用户作品列表"""
        posts = []
        cursor = 0
        while len(posts) < max_count:
            params = build_user_posts_params(
                sec_user_id, max_cursor=cursor, count=min(20, max_count - len(posts))
            )
            data = await self._request(ENDPOINTS["user_posts"], params)
            aweme_list = data.get("aweme_list", [])
            if not aweme_list:
                break
            for item in aweme_list:
                posts.append(VideoInfo.from_aweme(item))
            cursor = data.get("max_cursor", 0)
            has_more = data.get("has_more", False)
            if not has_more:
                break
            await self._delay()

        return posts[:max_count]

    # ---- 评论 ----

    async def get_comments(
        self,
        aweme_id: str,
        max_count: int = 100,
        with_replies: bool = False,
    ) -> list[Comment]:
        """获取视频评论"""
        comments = []
        cursor = 0
        while len(comments) < max_count:
            params = build_comment_list_params(
                aweme_id, cursor=cursor, count=min(20, max_count - len(comments))
            )
            data = await self._request(ENDPOINTS["comment_list"], params)
            comment_list = data.get("comments", [])
            if not comment_list:
                break
            for item in comment_list:
                comment = Comment.from_comment_data(item)
                if with_replies and comment.reply_count > 0:
                    comment.sub_comments = await self._get_reply_comments(
                        aweme_id, comment.cid
                    )
                comments.append(comment)
            cursor = data.get("cursor", 0)
            has_more = data.get("has_more", False)
            if not has_more:
                break
            await self._delay()

        return comments[:max_count]

    async def _get_reply_comments(
        self, aweme_id: str, comment_id: str, max_count: int = 50
    ) -> list[Comment]:
        """获取评论的回复"""
        replies = []
        cursor = 0
        while len(replies) < max_count:
            params = build_comment_reply_params(
                comment_id, aweme_id, cursor=cursor, count=20
            )
            data = await self._request(ENDPOINTS["comment_reply"], params)
            reply_list = data.get("comments", [])
            if not reply_list:
                break
            for item in reply_list:
                replies.append(Comment.from_comment_data(item))
            cursor = data.get("cursor", 0)
            has_more = data.get("has_more", False)
            if not has_more:
                break
            await self._delay()

        return replies[:max_count]

    # ---- 搜索 ----

    async def search(
        self,
        keyword: str,
        search_type: str = "video",
        max_count: int = 30,
    ) -> list[VideoInfo] | list[UserInfo]:
        """搜索视频或用户"""
        results = []
        offset = 0
        endpoint = (
            ENDPOINTS["search_user"]
            if search_type == "user"
            else ENDPOINTS["search_general"]
        )
        while len(results) < max_count:
            params = build_search_params(
                keyword, search_type, offset=offset,
                count=min(20, max_count - len(results)),
            )
            data = await self._request(endpoint, params)

            if search_type == "user":
                user_list = data.get("user_list", [])
                if not user_list:
                    break
                for item in user_list:
                    results.append(
                        UserInfo.from_user_data(item.get("user_info", item))
                    )
            else:
                aweme_list = data.get("data", [])
                if not aweme_list:
                    break
                for item in aweme_list:
                    aweme = item.get("aweme_info", item)
                    results.append(VideoInfo.from_aweme(aweme))

            has_more = data.get("has_more", False)
            if not has_more:
                break
            offset += 20
            await self._delay()

        return results[:max_count]
