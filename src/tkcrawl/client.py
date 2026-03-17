"""DouyinClient 核心类 — 基于 Playwright 网络拦截"""

import asyncio
import json
import logging
import random
from urllib.parse import quote, urlencode

from playwright.async_api import Page, Response, async_playwright

from tkcrawl.auth import load_cookies, save_cookies
from tkcrawl.endpoints import BASE_URL
from tkcrawl.models import Comment, UserInfo, VideoInfo
from tkcrawl.utils import get_random_user_agent

logger = logging.getLogger("tkcrawl")


class DouyinClient:
    """抖音数据采集核心客户端

    核心思路：通过 Playwright 控制真实浏览器访问抖音页面，
    拦截浏览器自身发出的 API 响应来获取数据。
    浏览器天然处理所有签名（X-Bogus/a_bogus/msToken），无需逆向。

    流程：
    1. 启动 Playwright chromium，加载已保存的 cookies
    2. 导航到目标页面（视频页、用户页、搜索页）
    3. 通过 page.on("response") 拦截匹配的 API 响应
    4. 解析响应 JSON，返回 Pydantic 模型
    """

    def __init__(
        self,
        headless: bool = True,
        cookie_path: str | None = None,
    ):
        self.headless = headless
        self.cookie_path = cookie_path
        self._playwright = None
        self._browser = None
        self._context = None
        self._page: Page | None = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        """启动浏览器"""
        self._playwright = await async_playwright().start()
        ua = get_random_user_agent()

        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={
                "width": random.randint(1200, 1400),
                "height": random.randint(680, 800),
            },
            user_agent=ua,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 隐藏 webdriver 特征
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en'],
            });
            window.chrome = { runtime: {} };
        """)

        # 加载已保存的 cookies
        await load_cookies(self._context, self.cookie_path)

        self._page = await self._context.new_page()
        logger.info("正在初始化浏览器...")
        await self._page.goto(BASE_URL, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(random.randint(2000, 4000))
        logger.info("浏览器初始化完成")

    async def close(self) -> None:
        """关闭所有资源"""
        if self._context:
            await save_cookies(self._context, self.cookie_path)
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _wait_for_api(
        self, url_pattern: str, action, timeout: float = 15000
    ) -> dict | None:
        """等待匹配的 API 响应

        Args:
            url_pattern: URL 中需要包含的关键字符串
            action: 触发请求的异步操作（如页面导航）
            timeout: 超时时间（毫秒）

        Returns:
            API 响应的 JSON 数据，或 None
        """
        result = {"data": None}
        event = asyncio.Event()

        async def on_response(response: Response):
            if url_pattern in response.url and response.status == 200:
                try:
                    body = await response.json()
                    result["data"] = body
                    event.set()
                except Exception:
                    pass

        self._page.on("response", on_response)
        try:
            await action()
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout / 1000)
            except asyncio.TimeoutError:
                logger.warning(f"等待 API 响应超时: {url_pattern}")
        finally:
            self._page.remove_listener("response", on_response)

        return result["data"]

    async def _collect_api_responses(
        self, url_pattern: str, action, count: int = 1, timeout: float = 15000
    ) -> list[dict]:
        """收集多个匹配的 API 响应"""
        results = []
        event = asyncio.Event()

        async def on_response(response: Response):
            if url_pattern in response.url and response.status == 200:
                try:
                    body = await response.json()
                    results.append(body)
                    if len(results) >= count:
                        event.set()
                except Exception:
                    pass

        self._page.on("response", on_response)
        try:
            await action()
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout / 1000)
            except asyncio.TimeoutError:
                pass
        finally:
            self._page.remove_listener("response", on_response)

        return results

    async def _delay(self) -> None:
        """随机等待 1-5 秒，模拟真人操作节奏"""
        wait = random.uniform(1.0, 5.0)
        logger.debug(f"随机等待 {wait:.1f}s")
        await asyncio.sleep(wait)

    async def _human_move(self) -> None:
        """模拟真人鼠标随机移动"""
        try:
            vw = self._page.viewport_size or {"width": 1280, "height": 720}
            x = random.randint(100, vw["width"] - 100)
            y = random.randint(100, vw["height"] - 100)
            await self._page.mouse.move(x, y, steps=random.randint(5, 15))
            # 偶尔做一次微小的滚动
            if random.random() < 0.3:
                await self._page.mouse.wheel(0, random.randint(-50, 50))
        except Exception:
            pass

    async def _check_captcha(self) -> bool:
        """检测是否出现验证码，出现则暂停等待用户处理

        Returns: True 表示检测到验证码并已等待处理
        """
        try:
            captcha_selectors = [
                '[class*="captcha"]',
                '[class*="Captcha"]',
                '[class*="verify"]',
                '[class*="Verify"]',
                '[class*="secsdk"]',
                '#captcha_container',
                '[class*="captcha-verify"]',
            ]
            for sel in captcha_selectors:
                el = self._page.locator(sel).first
                if await el.is_visible(timeout=300):
                    logger.warning(
                        "检测到验证码！如果是无头模式请用 --no-headless 重试。"
                        "等待验证码消失..."
                    )
                    # 等待验证码消失（用户手动处理或自动消失），最多等 120 秒
                    for _ in range(120):
                        still_visible = False
                        for s in captcha_selectors:
                            try:
                                if await self._page.locator(s).first.is_visible(
                                    timeout=300
                                ):
                                    still_visible = True
                                    break
                            except Exception:
                                pass
                        if not still_visible:
                            logger.info("验证码已通过，继续采集")
                            return True
                        await asyncio.sleep(1)
                    logger.error("验证码等待超时")
                    return True
        except Exception:
            pass
        return False

    # ---- 视频详情 ----

    async def get_video(self, aweme_id: str) -> VideoInfo:
        """获取视频详情 — 访问视频页面，拦截 aweme/detail API"""
        url = f"{BASE_URL}/video/{aweme_id}"
        logger.info(f"正在访问视频页: {url}")

        async def navigate():
            await self._page.goto(url, wait_until="domcontentloaded")

        data = await self._wait_for_api("aweme/detail", navigate)
        if not data:
            # 回退：尝试从页面 SSR 数据中提取
            data = await self._extract_ssr_data()
            if data:
                aweme = (
                    data.get("aweme_detail")
                    or data.get("awemeDetail")
                    or {}
                )
                return VideoInfo.from_aweme(aweme)
            raise RuntimeError(f"无法获取视频数据: {aweme_id}")

        aweme = data.get("aweme_detail", {})
        return VideoInfo.from_aweme(aweme)

    # ---- 用户信息 ----

    async def get_user_profile(self, sec_user_id: str) -> UserInfo:
        """获取用户信息 — 访问用户主页，拦截 profile API"""
        url = f"{BASE_URL}/user/{sec_user_id}"
        logger.info(f"正在访问用户页: {url}")

        async def navigate():
            await self._page.goto(url, wait_until="domcontentloaded")

        data = await self._wait_for_api("user/profile", navigate)
        if not data:
            data = await self._extract_ssr_data()
            if data:
                user = data.get("user") or data.get("userInfo", {}).get("user", {})
                if user:
                    return UserInfo.from_user_data({"user": user})
            raise RuntimeError(f"无法获取用户数据: {sec_user_id}")

        return UserInfo.from_user_data(data)

    async def get_user_posts(
        self, sec_user_id: str, max_count: int = 50
    ) -> list[VideoInfo]:
        """获取用户作品列表 — 在用户页滚动加载，拦截 aweme/post API"""
        posts = []

        # 确保在用户页面
        current_url = self._page.url
        if sec_user_id not in current_url:
            url = f"{BASE_URL}/user/{sec_user_id}"
            logger.info(f"导航到用户页: {url}")

            async def navigate():
                await self._page.goto(url, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(2000)

            # 首次加载会同时返回 profile 和 post 数据
            data = await self._wait_for_api("aweme/post", navigate)
            if data:
                for item in data.get("aweme_list", []):
                    posts.append(VideoInfo.from_aweme(item))
        else:
            # 已在用户页，尝试从 SSR 提取初始数据
            ssr = await self._extract_ssr_data()
            if ssr:
                for item in ssr.get("post", {}).get("data", []):
                    posts.append(VideoInfo.from_aweme(item))

        # 滚动加载更多
        while len(posts) < max_count:
            logger.info(f"已采集 {len(posts)} 个作品，继续滚动...")

            async def scroll():
                await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self._page.wait_for_timeout(1500)

            data = await self._wait_for_api("aweme/post", scroll, timeout=8000)
            if not data:
                break
            aweme_list = data.get("aweme_list", [])
            if not aweme_list:
                break
            for item in aweme_list:
                posts.append(VideoInfo.from_aweme(item))
            if not data.get("has_more", False):
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
        """获取视频评论 — 在视频页滚动加载评论区"""
        comments = []

        # 确保在视频页面
        current_url = self._page.url
        if aweme_id not in current_url:
            url = f"{BASE_URL}/video/{aweme_id}"
            logger.info(f"导航到视频页: {url}")

            async def navigate():
                await self._page.goto(url, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(2000)

            data = await self._wait_for_api("comment/list", navigate, timeout=10000)
            if data:
                for item in data.get("comments", []) or []:
                    comments.append(Comment.from_comment_data(item))

        # 滚动加载更多评论
        while len(comments) < max_count:
            logger.info(f"已采集 {len(comments)} 条评论，继续滚动...")

            async def scroll():
                # 滚动评论区域
                await self._page.evaluate("""
                    const commentArea = document.querySelector('[class*=comment]')
                        || document.querySelector('[class*=Comment]')
                        || document.body;
                    commentArea.scrollTop = commentArea.scrollHeight;
                    window.scrollTo(0, document.body.scrollHeight);
                """)
                await self._page.wait_for_timeout(1500)

            data = await self._wait_for_api("comment/list", scroll, timeout=8000)
            if not data:
                break
            comment_list = data.get("comments", []) or []
            if not comment_list:
                break
            for item in comment_list:
                comments.append(Comment.from_comment_data(item))
            if not data.get("has_more", False):
                break
            await self._delay()

        # 采集子评论
        if with_replies:
            for comment in comments[:max_count]:
                if comment.reply_count > 0:
                    comment.sub_comments = await self._get_reply_comments(
                        aweme_id, comment.cid
                    )

        return comments[:max_count]

    async def _get_reply_comments(
        self, aweme_id: str, comment_id: str, max_count: int = 50
    ) -> list[Comment]:
        """获取评论回复 — 点击展开回复，拦截 reply API"""
        replies = []

        # 尝试点击"展开回复"按钮
        try:
            reply_btn = self._page.locator(
                f'[data-comment-id="{comment_id}"] [class*=reply],'
                f' [class*=ReplyAction]'
            ).first
            if await reply_btn.is_visible(timeout=2000):
                async def click_reply():
                    await reply_btn.click()
                    await self._page.wait_for_timeout(1500)

                data = await self._wait_for_api(
                    "comment/list/reply", click_reply, timeout=8000
                )
                if data:
                    for item in data.get("comments", []) or []:
                        replies.append(Comment.from_comment_data(item))
        except Exception as e:
            logger.debug(f"获取回复评论失败: {e}")

        return replies[:max_count]

    # ---- 搜索 ----

    async def search(
        self,
        keyword: str,
        search_type: str = "video",
        max_count: int = 30,
    ) -> list[VideoInfo] | list[UserInfo]:
        """搜索视频或用户 — 通过搜索框输入关键词，拦截搜索 API

        搜索比推荐流更容易触发验证码，建议使用 --no-headless 模式。
        """
        results = []

        if search_type == "user":
            api_patterns = ("search/user",)
        else:
            api_patterns = ("general/search/single", "search/item", "search/video")

        logger.info(f"正在搜索: {keyword} (类型: {search_type})")

        # 通过搜索框输入，比直接跳 URL 更不容易触发验证码
        async def do_search():
            # 确保在首页（搜索框所在页面）
            if "/search/" not in self._page.url:
                await self._page.goto(BASE_URL, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(2000)
                await self._dismiss_popups()

            # 查找搜索框
            search_input = self._page.locator(
                'input[data-e2e="searchbar-input"],'
                ' input[placeholder*="搜索"],'
                ' input[class*="search"]'
            ).first
            try:
                await search_input.click(timeout=3000)
                await search_input.fill("")
                await self._page.wait_for_timeout(300)
                # 逐字输入，模拟真人
                for ch in keyword:
                    await search_input.type(ch, delay=random.randint(50, 150))
                await self._page.wait_for_timeout(500)
                await self._page.keyboard.press("Enter")
                await self._page.wait_for_timeout(2000)
            except Exception:
                # 搜索框找不到，回退到 URL 直接跳转
                encoded = quote(keyword)
                type_param = "user" if search_type == "user" else "video"
                url = f"{BASE_URL}/search/{encoded}?type={type_param}"
                await self._page.goto(url, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(2000)

            # 如果搜索视频但当前 tab 不对，点击对应 tab
            if search_type == "user":
                try:
                    tab = self._page.locator('text="用户"').first
                    if await tab.is_visible(timeout=2000):
                        await tab.click()
                        await self._page.wait_for_timeout(1500)
                except Exception:
                    pass

        # 使用多模式匹配的 _wait_for_api
        data = await self._wait_for_api_multi(
            api_patterns, do_search, timeout=20000
        )

        # 检测验证码并等待用户处理
        if not data:
            captcha_found = await self._check_captcha()
            if captcha_found:
                logger.info("验证码已处理，重新等待搜索结果...")
                # 验证码通过后页面会自动加载结果
                data = await self._wait_for_api_multi(
                    api_patterns,
                    lambda: self._page.wait_for_timeout(1000),
                    timeout=15000,
                )

        if data:
            results.extend(self._parse_search_data(data, search_type))

        if not results:
            logger.warning(
                "未获取到搜索结果，可能被验证码拦截。"
                "请使用 --no-headless 模式并手动完成验证码。"
            )
            return results

        # 滚动加载更多结果
        while len(results) < max_count:
            logger.info(f"已采集 {len(results)} 条结果，继续滚动...")
            await self._delay()

            async def scroll():
                await self._human_move()
                await self._page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                await self._page.wait_for_timeout(1500)

            data = await self._wait_for_api_multi(
                api_patterns, scroll, timeout=10000
            )
            if not data:
                # 可能又触发验证码
                await self._check_captcha()
                break
            new_items = self._parse_search_data(data, search_type)
            if not new_items:
                break
            results.extend(new_items)
            if not data.get("has_more", False):
                break

        return results[:max_count]

    async def _wait_for_api_multi(
        self, url_patterns: tuple[str, ...], action, timeout: float = 15000
    ) -> dict | None:
        """等待匹配多个模式之一的 API 响应"""
        result = {"data": None}
        event = asyncio.Event()

        async def on_response(response: Response):
            if any(p in response.url for p in url_patterns) and response.status == 200:
                try:
                    body = await response.json()
                    result["data"] = body
                    event.set()
                except Exception:
                    pass

        self._page.on("response", on_response)
        try:
            await action()
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout / 1000)
            except asyncio.TimeoutError:
                logger.warning(f"等待搜索 API 响应超时")
        finally:
            self._page.remove_listener("response", on_response)

        return result["data"]

    @staticmethod
    def _parse_search_data(
        data: dict, search_type: str
    ) -> list[VideoInfo] | list[UserInfo]:
        items = []
        if search_type == "user":
            for item in data.get("user_list", []):
                items.append(
                    UserInfo.from_user_data(item.get("user_info", item))
                )
        else:
            for item in data.get("data", []):
                aweme = item.get("aweme_info", item)
                vid = VideoInfo.from_aweme(aweme)
                if vid.is_valid:
                    items.append(vid)
        return items

    # ---- 推荐流 ----

    async def crawl_feed(
        self,
        max_count: int = 20,
        on_video=None,
    ) -> list[VideoInfo]:
        """刷推荐流，自动滑动采集视频（自动随机 1-5s 间隔）

        Args:
            max_count: 最大采集视频数
            on_video: 回调函数，每采集到一个视频时调用 on_video(video, index)
        """
        videos = []
        seen_ids = set()

        logger.info("正在打开抖音推荐页...")
        await self._page.goto(BASE_URL, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(3000)

        # 关闭可能的登录弹窗/通知弹窗
        await self._dismiss_popups()

        # 尝试从首屏 SSR 数据获取第一个视频
        ssr = await self._extract_ssr_data()
        if ssr:
            # SSR 数据结构可能是嵌套的，尝试多种路径
            for key, val in ssr.items():
                if isinstance(val, dict):
                    aweme = val.get("awemeDetail") or val.get("aweme_detail")
                    if aweme and aweme.get("aweme_id"):
                        vid = VideoInfo.from_aweme(aweme)
                        if vid.is_valid and vid.aweme_id not in seen_ids:
                            seen_ids.add(vid.aweme_id)
                            videos.append(vid)
                            if on_video:
                                await on_video(vid, len(videos))

        # 持续监听 recommend API 响应
        collected = asyncio.Queue()

        async def on_response(response):
            url = response.url
            # 匹配推荐流/视频相关 API
            feed_patterns = (
                "recommend", "aweme/post", "tab/feed", "aweme/detail",
                "feed/", "slide/", "related/recommend",
            )
            if any(p in url for p in feed_patterns) and response.status == 200:
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" not in ct and "javascript" not in ct:
                        return
                    body = await response.json()
                    logger.debug(f"拦截到 API: {url[:120]}")
                    # 推荐流返回 aweme_list
                    aweme_list = body.get("aweme_list", [])
                    if aweme_list:
                        for item in aweme_list:
                            await collected.put(item)
                    # 单个视频详情
                    aweme_detail = body.get("aweme_detail")
                    if aweme_detail:
                        await collected.put(aweme_detail)
                except Exception:
                    pass

        self._page.on("response", on_response)

        try:
            # 点击页面激活焦点（确保键盘事件生效）
            try:
                await self._page.click("body", timeout=2000)
            except Exception:
                pass
            await asyncio.sleep(1)

            # 滑动采集循环
            swipe_count = 0
            stale_count = 0
            while len(videos) < max_count:
                # 每次滑动前关闭弹窗 + 检测验证码
                if swipe_count % 5 == 0:
                    await self._dismiss_popups()
                await self._check_captcha()

                # 模拟真人鼠标移动（每 2-3 次滑动做一次）
                if random.random() < 0.4:
                    await self._human_move()

                # 模拟下滑到下一个视频（按下箭头键）
                await self._page.keyboard.press("ArrowDown")
                wait = random.uniform(1.0, 5.0)
                logger.debug(f"滑动 #{swipe_count + 1}，等待 {wait:.1f}s")
                await asyncio.sleep(wait)
                swipe_count += 1

                # 从队列中取出已拦截的数据
                new_found = False
                while not collected.empty():
                    item = collected.get_nowait()
                    aid = str(item.get("aweme_id", ""))
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        vid = VideoInfo.from_aweme(item)
                        if not vid.is_valid:
                            logger.debug(f"跳过无效条目: {aid}")
                            continue
                        videos.append(vid)
                        new_found = True
                        if on_video:
                            await on_video(vid, len(videos))
                        if len(videos) >= max_count:
                            break

                if not new_found:
                    stale_count += 1
                    if stale_count >= 15:
                        logger.warning("连续多次未获取到新视频，停止")
                        break
                else:
                    stale_count = 0

        finally:
            self._page.remove_listener("response", on_response)

        return videos

    # ---- 作者信息补全 ----

    async def enrich_author(self, video: VideoInfo) -> VideoInfo:
        """访问作者主页，补全粉丝数等信息"""
        sec_uid = video.author.sec_uid
        if not sec_uid:
            return video

        # 随机短暂等待，避免连续快速打开页面
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # 用新 tab 访问用户主页，拦截 profile API
        page = await self._context.new_page()
        try:
            url = f"{BASE_URL}/user/{sec_uid}"
            result = {"data": None}
            event = asyncio.Event()

            async def on_resp(response):
                if "user/profile" in response.url and response.status == 200:
                    try:
                        body = await response.json()
                        result["data"] = body
                        event.set()
                    except Exception:
                        pass

            page.on("response", on_resp)
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await asyncio.wait_for(event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
            page.remove_listener("response", on_resp)

            data = result["data"]
            if data:
                user = data.get("user", {})
                video.author.follower_count = user.get("follower_count", 0)
                video.author.following_count = user.get("following_count", 0)
                video.author.total_favorited = int(
                    user.get("total_favorited", 0) or 0
                )
                video.author.aweme_count = user.get("aweme_count", 0)
                logger.debug(
                    f"补全作者 {video.author.nickname}: "
                    f"粉丝 {video.author.follower_count}"
                )
            else:
                # 回退：从 SSR 数据提取
                try:
                    ssr = await page.evaluate("""
                        () => {
                            const el = document.getElementById('RENDER_DATA');
                            if (el) {
                                try { return JSON.parse(decodeURIComponent(el.textContent)); }
                                catch {}
                            }
                            return null;
                        }
                    """)
                    if ssr:
                        for val in ssr.values():
                            if isinstance(val, dict):
                                user = (
                                    val.get("user")
                                    or val.get("userInfo", {}).get("user")
                                )
                                if user and user.get("follower_count"):
                                    video.author.follower_count = user.get(
                                        "follower_count", 0
                                    )
                                    video.author.following_count = user.get(
                                        "following_count", 0
                                    )
                                    video.author.total_favorited = int(
                                        user.get("total_favorited", 0) or 0
                                    )
                                    video.author.aweme_count = user.get(
                                        "aweme_count", 0
                                    )
                                    break
                except Exception:
                    pass
        finally:
            await page.close()

        return video

    # ---- 辅助方法 ----

    async def _dismiss_popups(self) -> None:
        """关闭登录弹窗、通知弹窗等遮挡物"""
        try:
            # 常见的关闭按钮选择器
            close_selectors = [
                '[class*="close"]',
                '[class*="Close"]',
                '[aria-label="关闭"]',
                '[class*="dy-account-close"]',
                '.douyin-login [class*="close"]',
                '[class*="modal"] [class*="close"]',
                '[class*="dialog"] [class*="close"]',
                '[class*="mask"] [class*="close"]',
            ]
            for sel in close_selectors:
                try:
                    btn = self._page.locator(sel).first
                    if await btn.is_visible(timeout=500):
                        await btn.click()
                        logger.debug(f"关闭弹窗: {sel}")
                        await self._page.wait_for_timeout(500)
                except Exception:
                    pass
        except Exception:
            pass

    async def _extract_ssr_data(self) -> dict | None:
        """从页面 SSR 注入的 script 标签中提取数据"""
        try:
            data = await self._page.evaluate("""
                () => {
                    // 抖音将 SSR 数据注入到 RENDER_DATA script 标签
                    const el = document.getElementById('RENDER_DATA');
                    if (el) {
                        try {
                            return JSON.parse(decodeURIComponent(el.textContent));
                        } catch {}
                    }
                    // 或者通过 __NEXT_DATA__
                    if (window.__NEXT_DATA__) {
                        return window.__NEXT_DATA__.props?.pageProps;
                    }
                    return null;
                }
            """)
            return data
        except Exception as e:
            logger.debug(f"提取 SSR 数据失败: {e}")
            return None
