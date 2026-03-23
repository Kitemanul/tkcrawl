"""DouyinClient 核心类 — 基于 Playwright 网络拦截"""

import asyncio
import json
import logging
import random
import time

from playwright.async_api import (
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from tkcrawl.auth import load_cookies, save_cookies
from tkcrawl.endpoints import (
    BASE_URL,
    SEARCH_DURATION_LABELS,
    SEARCH_SORT_LABELS,
    SEARCH_TIME_LABELS,
    build_search_page_url,
)
from tkcrawl.filters import VideoFilter
from tkcrawl.models import Comment, UserInfo, VideoInfo
from tkcrawl.utils import get_random_user_agent

logger = logging.getLogger("tkcrawl")

# 单次搜索最大翻页数，超过后自动停止（防止过度请求触发限流）
MAX_SEARCH_PAGES = 10

# Cookie 自动保存间隔（每 N 次请求保存一次，防止会话崩溃丢失 cookie）
COOKIE_SAVE_INTERVAL = 30


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

    反爬参数：
    - rate_limit: 请求速率倍率（1.0 = 默认 3-8s 间隔，2.0 = 翻倍，以此类推）
                  建议长时间采集时设为 1.5-2.0，遭遇限流后设为 2.0-3.0
    """

    def __init__(
        self,
        headless: bool = False,
        cookie_path: str | None = None,
        rate_limit: float = 1.0,
    ):
        self.headless = headless
        self.cookie_path = cookie_path
        # rate_limit 最小 0.5 倍（不允许过快），最大不限（用户可以很慢）
        self.rate_limit = max(0.5, float(rate_limit))

        # ---- 会话统计（用于监控请求频率，辅助反爬决策）----
        self._session_start: float = 0.0
        self._request_count: int = 0      # 本次会话 API 请求次数
        self._captcha_count: int = 0      # 本次会话触发验证码次数
        self._consecutive_empty: int = 0  # 连续空结果次数（指数退避计数器）

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
        self._session_start = time.time()
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
        logger.info("浏览器初始化完成（rate_limit=%.1fx）", self.rate_limit)

    async def close(self) -> None:
        """关闭所有资源，保存 cookies"""
        if self._context:
            await save_cookies(self._context, self.cookie_path)
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        # 打印本次会话的汇总统计
        self._log_session_stats(final=True)

    # ---- 请求速率控制 ----

    async def _delay(self) -> None:
        """随机等待 3-8 秒（乘以 rate_limit 倍率），模拟真人操作节奏。

        相比原始的 1-5s，增大了下限，降低触发限流的风险。
        """
        wait = random.uniform(3.0, 8.0) * self.rate_limit
        logger.debug("请求间隔等待 %.1fs", wait)
        await asyncio.sleep(wait)

    async def _progressive_delay(self, page_num: int) -> None:
        """分页请求间隔递增：越往后越慢，模拟人类翻页疲劳感。

        页码越大，间隔系数越高：
        - 第 1-3 页：1.0x（正常速度）
        - 第 4-6 页：1.5x（适当放缓）
        - 第 7 页+：2.0x（明显放缓，降低被识别为机器人的风险）
        """
        if page_num <= 3:
            multiplier = 1.0
        elif page_num <= 6:
            multiplier = 1.5
        else:
            multiplier = 2.0

        wait = random.uniform(3.0, 8.0) * self.rate_limit * multiplier
        logger.debug("第 %d 页，递增等待 %.1fs（×%.1f）", page_num, wait, multiplier)
        await asyncio.sleep(wait)

    async def _backoff_delay(self) -> None:
        """遭遇连续空结果时使用指数退避策略。

        连续空结果通常意味着触发了限流或内容已抓取完毕。
        通过逐步增加等待时间让服务端"冷却"。

        退避策略：
        - 第 1 次空：额外等待 5s
        - 第 2 次空：额外等待 10s
        - 第 3 次空：额外等待 20s
        - 第 4+ 次空：上限 40s，此后调用方应考虑终止
        """
        self._consecutive_empty += 1
        extra = min(5 * (2 ** (self._consecutive_empty - 1)), 40)
        logger.warning(
            "连续第 %d 次空结果，指数退避等待 %ds（可能触发限流或内容已采集完毕）",
            self._consecutive_empty,
            extra,
        )
        await asyncio.sleep(extra)

    def _reset_consecutive_empty(self) -> None:
        """收到有效数据时重置连续空结果计数器"""
        if self._consecutive_empty > 0:
            logger.debug("收到有效数据，重置退避计数器")
            self._consecutive_empty = 0

    async def _maybe_save_cookies(self) -> None:
        """定期保存 cookies，防止长时间运行时因崩溃丢失登录状态。

        每 COOKIE_SAVE_INTERVAL 次请求保存一次。
        """
        if self._request_count > 0 and self._request_count % COOKIE_SAVE_INTERVAL == 0:
            logger.debug("定期保存 cookies（已请求 %d 次）", self._request_count)
            if self._context:
                await save_cookies(self._context, self.cookie_path)

    def _log_session_stats(self, final: bool = False) -> None:
        """打印当前会话的请求频率统计，便于用户监控是否触发限流。

        Args:
            final: True 时为最终汇总，使用 info 级别；否则使用 debug
        """
        if self._session_start == 0:
            return
        elapsed = time.time() - self._session_start
        if elapsed <= 0:
            return

        rpm = self._request_count / (elapsed / 60)  # 每分钟请求数
        prefix = "📊 会话汇总" if final else "📊 会话统计"
        log_fn = logger.info if final else logger.debug
        log_fn(
            "%s | 请求 %d 次 | %.1f 次/分钟 | 运行 %.0f 秒 | 验证码 %d 次",
            prefix,
            self._request_count,
            rpm,
            elapsed,
            self._captcha_count,
        )
        if final and self._captcha_count >= 3:
            logger.warning(
                "本次会话触发验证码 %d 次，建议下次使用更大的 --rate-limit 值",
                self._captcha_count,
            )

    # ---- 网络响应拦截 ----

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
                logger.warning("等待 API 响应超时: %s", url_pattern)
        finally:
            self._page.remove_listener("response", on_response)

        if result["data"] is not None:
            self._request_count += 1
            await self._maybe_save_cookies()

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

        if results:
            self._request_count += len(results)
            await self._maybe_save_cookies()

        return results

    # ---- 真人行为模拟 ----

    async def _human_move(self) -> None:
        """模拟真人鼠标行为：随机移动、偶发滚动、偶发停顿。

        通过多样化的鼠标轨迹降低被机器人识别的概率。
        """
        try:
            vw = self._page.viewport_size or {"width": 1280, "height": 720}

            # 随机鼠标移动 1-3 次，每次步数随机
            for _ in range(random.randint(1, 3)):
                x = random.randint(50, vw["width"] - 50)
                y = random.randint(50, vw["height"] - 50)
                await self._page.mouse.move(x, y, steps=random.randint(5, 20))
                # 30% 概率在移动中间停顿一下，模拟阅读
                if random.random() < 0.3:
                    await asyncio.sleep(random.uniform(0.1, 0.5))

            # 30% 概率做微小的上下滚动（模拟翻看内容）
            if random.random() < 0.3:
                delta = random.randint(-150, 150)
                await self._page.mouse.wheel(0, delta)
                await asyncio.sleep(random.uniform(0.2, 0.8))

            # 10% 概率模拟"阅读停顿"（1-3 秒，模拟真正在看视频）
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(1.0, 3.0))

        except Exception:
            pass

    # ---- 验证码检测 ----

    async def _check_captcha(self) -> bool:
        """检测是否出现验证码，出现则暂停等待用户处理。

        检测到验证码时累计计数，触发频率过高时会输出警告提醒用户降速。

        Returns:
            True 表示检测到验证码并已等待处理
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
                    self._captcha_count += 1
                    logger.warning(
                        "⚠️  检测到验证码（本次会话第 %d 次）！"
                        "如果当前使用无头模式，请改用默认有头模式重试；"
                        "等待验证码消失...",
                        self._captcha_count,
                    )
                    # 验证码频率过高时提醒用户降速
                    if self._captcha_count >= 3:
                        logger.warning(
                            "验证码已触发 %d 次，建议停止后使用更大的 --rate-limit 值重试",
                            self._captcha_count,
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
                    logger.error("验证码等待超时（120s），请检查网络或账号状态")
                    return True
        except Exception:
            pass
        return False

    # ---- 视频详情 ----

    async def _click_search_filters(
        self,
        sort_type: str,
        publish_time: str,
        filter_duration: str,
    ) -> bool:
        """点击搜索结果页筛选按钮，触发带筛选条件的重新搜索。"""
        clicked_any = False

        async def _try_click_label(label: str) -> bool:
            selectors = [
                f'[class*="filter"] :text-is("{label}")',
                f'[class*="sort"] :text-is("{label}")',
                f'[class*="tab"] :text-is("{label}")',
                f':text-is("{label}")',
            ]
            for sel in selectors:
                try:
                    el = self._page.locator(sel).first
                    if await el.is_visible(timeout=1500):
                        await el.click()
                        await self._page.wait_for_timeout(600)
                        logger.debug("点击筛选按钮: %s", label)
                        return True
                except Exception:
                    continue
            logger.debug("未找到筛选按钮: %s", label)
            return False

        await self._page.wait_for_timeout(800)

        sort_label = SEARCH_SORT_LABELS.get(sort_type, "")
        if sort_label and await _try_click_label(sort_label):
            clicked_any = True
            await asyncio.sleep(0.5)

        time_label = SEARCH_TIME_LABELS.get(publish_time, "")
        if time_label and await _try_click_label(time_label):
            clicked_any = True
            await asyncio.sleep(0.5)

        duration_label = SEARCH_DURATION_LABELS.get(filter_duration, "")
        if duration_label and await _try_click_label(duration_label):
            clicked_any = True

        return clicked_any

    async def _search_via_input(self, keyword: str, search_type: str) -> None:
        """通过搜索框输入关键词，作为 URL 导航失败时的回退路径。"""
        if "/search/" not in self._page.url:
            await self._page.goto(BASE_URL, wait_until="domcontentloaded")
            await self._page.wait_for_timeout(2000)
            await self._dismiss_popups()

        search_input = self._page.locator(
            'input[data-e2e="searchbar-input"],'
            ' input[placeholder*="搜索"],'
            ' input[class*="search"]'
        ).first

        await search_input.click(timeout=3000)
        await search_input.fill("")
        await self._page.wait_for_timeout(300)
        for ch in keyword:
            await search_input.type(ch, delay=random.randint(50, 150))
        await self._page.wait_for_timeout(500)
        await self._page.keyboard.press("Enter")
        await self._page.wait_for_timeout(2000)

        if search_type == "user":
            try:
                tab = self._page.locator('text="用户"').first
                if await tab.is_visible(timeout=2000):
                    await tab.click()
                    await self._page.wait_for_timeout(1500)
            except Exception:
                pass

    async def get_video(self, aweme_id: str) -> VideoInfo:
        """获取视频详情 — 访问视频页面，拦截 aweme/detail API"""
        url = f"{BASE_URL}/video/{aweme_id}"
        logger.info("正在访问视频页: %s", url)

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
        logger.info("正在访问用户页: %s", url)

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
            logger.info("导航到用户页: %s", url)

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
        page_num = 1
        while len(posts) < max_count:
            logger.info("已采集 %d 个作品，继续滚动...", len(posts))

            async def scroll():
                await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self._page.wait_for_timeout(1500)

            data = await self._wait_for_api("aweme/post", scroll, timeout=8000)
            if not data:
                await self._backoff_delay()
                if self._consecutive_empty >= 3:
                    logger.warning("连续 %d 次空结果，停止采集", self._consecutive_empty)
                    break
                continue

            self._reset_consecutive_empty()
            aweme_list = data.get("aweme_list", [])
            if not aweme_list:
                break
            for item in aweme_list:
                posts.append(VideoInfo.from_aweme(item))
            if not data.get("has_more", False):
                break

            page_num += 1
            await self._progressive_delay(page_num)

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
            logger.info("导航到视频页: %s", url)

            async def navigate():
                await self._page.goto(url, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(2000)

            data = await self._wait_for_api("comment/list", navigate, timeout=10000)
            if data:
                for item in data.get("comments", []) or []:
                    comments.append(Comment.from_comment_data(item))

        # 滚动加载更多评论
        page_num = 1
        while len(comments) < max_count:
            logger.info("已采集 %d 条评论，继续滚动...", len(comments))

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
                await self._backoff_delay()
                if self._consecutive_empty >= 3:
                    break
                continue

            self._reset_consecutive_empty()
            comment_list = data.get("comments", []) or []
            if not comment_list:
                break
            for item in comment_list:
                comments.append(Comment.from_comment_data(item))
            if not data.get("has_more", False):
                break

            page_num += 1
            await self._progressive_delay(page_num)

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
            logger.debug("获取回复评论失败: %s", e)

        return replies[:max_count]

    # ---- 搜索 ----

    async def search(
        self,
        keyword: str,
        search_type: str = "video",
        max_count: int = 30,
        sort_type: str = "0",
        publish_time: str = "0",
        filter_duration: str = "",
        video_filter: VideoFilter | None = None,
    ) -> list[VideoInfo] | list[UserInfo]:
        """搜索视频或用户 — 优先直接导航搜索页，必要时回退到 UI 搜索

        第一层：请求端筛选
            - sort_type / publish_time / filter_duration 通过搜索结果页 URL 参数传入，
              浏览器自动携带正确签名发起真实搜索请求，无需逆向 API。
            - 视频时长对应搜索页的 `filter_selected` 参数。
            - 若搜索页导航失败，则回退到搜索框输入 + UI 筛选。

        第二层：存储前过滤
            - video_filter 在解析数据后、返回前进行客户端过滤。
            - max_count 按过滤后的有效条数计算，确保返回数量符合预期。

        反爬说明：
            - 搜索比推荐流更容易触发验证码，默认使用有头模式更稳。
            - 超过 MAX_SEARCH_PAGES 页后自动停止，防止过度请求。
            - 每页间隔使用递增延迟（_progressive_delay）。

        Args:
            keyword: 搜索关键词
            search_type: "video" 视频 / "user" 用户
            max_count: 最多保留几条（按过滤后计数）
            sort_type: "0" 综合 / "1" 最多点赞 / "2" 最新发布
            publish_time: "0" 不限 / "1" 一天内 / "7" 一周内 / "182" 六个月内
            filter_duration: "" 不限 / "0" 1分钟内 / "1" 1-5分钟 / "2" 5分钟以上
            video_filter: 客户端过滤条件（None 表示不过滤）
        """
        results: list[VideoInfo] | list[UserInfo] = []

        if search_type == "user":
            api_patterns = ("search/user",)
        else:
            api_patterns = ("general/search/single", "search/item", "search/video")

        # 是否有第一层（请求端）筛选条件
        has_api_filter = sort_type != "0" or publish_time != "0" or filter_duration != ""

        if has_api_filter:
            sort_label = SEARCH_SORT_LABELS.get(sort_type, "")
            time_label = SEARCH_TIME_LABELS.get(publish_time, "")
            dur_label = SEARCH_DURATION_LABELS.get(filter_duration, "")
            active = [l for l in [sort_label, time_label, dur_label] if l]
            logger.info("请求端筛选条件: %s", "、".join(active) if active else "无")

        if video_filter and not video_filter.is_empty():
            logger.info("客户端过滤条件: %s", video_filter.describe())

        logger.info("正在搜索: %s（类型: %s）", keyword, search_type)

        # ---- 第一阶段：导航到搜索页，获取初始结果 ----

        async def do_search():
            """导航到标准搜索页，筛选参数通过 URL query string 传入。"""
            url = build_search_page_url(
                keyword,
                search_type=search_type,
                sort_type=sort_type,
                publish_time=publish_time,
                filter_duration=filter_duration,
            )
            logger.debug("导航到搜索页: %s", url)
            try:
                await self._page.goto(url, wait_until="commit", timeout=45000)
            except PlaywrightTimeoutError:
                logger.warning("搜索页导航超时，继续等待搜索 API: %s", url)
            await self._page.wait_for_timeout(2000)
            await self._dismiss_popups()

            # 如果搜索用户，点击"用户" tab
            if search_type == "user":
                try:
                    tab = self._page.locator('text="用户"').first
                    if await tab.is_visible(timeout=2000):
                        await tab.click()
                        await self._page.wait_for_timeout(1500)
                except Exception:
                    pass

        # 拦截初始搜索 API 响应
        data = await self._wait_for_api_multi(
            api_patterns, do_search, timeout=20000
        )

        # 验证码检测与处理
        if not data:
            captcha_found = await self._check_captcha()
            if captcha_found:
                logger.info("验证码已处理，重新等待搜索结果...")
                data = await self._wait_for_api_multi(
                    api_patterns,
                    lambda: self._page.wait_for_timeout(1000),
                    timeout=15000,
                )

        if not data:
            logger.info("搜索页直达未获取到结果，回退到搜索框输入")

            async def do_search_via_input():
                await self._search_via_input(keyword, search_type)

            data = await self._wait_for_api_multi(
                api_patterns, do_search_via_input, timeout=20000
            )

            if not data:
                captcha_found = await self._check_captcha()
                if captcha_found:
                    logger.info("UI 搜索后验证码已处理，重新等待搜索结果...")
                    data = await self._wait_for_api_multi(
                        api_patterns,
                        lambda: self._page.wait_for_timeout(1000),
                        timeout=15000,
                    )

            if data and has_api_filter and search_type == "video":
                logger.info("UI 搜索已获取结果，尝试点击筛选按钮应用请求端过滤")

                async def do_filter():
                    await self._click_search_filters(
                        sort_type, publish_time, filter_duration
                    )

                filtered_data = await self._wait_for_api_multi(
                    api_patterns, do_filter, timeout=12000
                )
                if filtered_data:
                    data = filtered_data

        if not data:
            logger.warning(
                "未获取到搜索结果，可能被验证码拦截。"
                "请使用默认有头模式并手动完成验证码。"
            )
            return results

        # ---- 解析初始结果 ----
        def _passes_filter(item) -> bool:
            """判断条目是否通过客户端过滤"""
            if video_filter and not video_filter.is_empty() and hasattr(item, "stats"):
                return video_filter.match(item)
            return True

        for item in self._parse_search_data(data, search_type):
            if _passes_filter(item):
                results.append(item)
                if len(results) >= max_count:
                    return results

        # ---- 滚动翻页获取更多结果 ----
        page_num = 1
        while len(results) < max_count:
            # 安全页数上限
            if page_num >= MAX_SEARCH_PAGES:
                logger.info(
                    "已达最大翻页数 %d，停止搜索（已保存 %d 条）",
                    MAX_SEARCH_PAGES,
                    len(results),
                )
                break

            page_num += 1
            await self._progressive_delay(page_num)

            # 每 3 页打印一次统计（让用户知道程序还在跑）
            if page_num % 3 == 0:
                self._log_session_stats()

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
                await self._backoff_delay()
                if self._consecutive_empty >= 3:
                    logger.warning("连续 %d 次无数据，停止翻页", self._consecutive_empty)
                    break
                continue

            self._reset_consecutive_empty()
            new_items = self._parse_search_data(data, search_type)
            if not new_items:
                await self._backoff_delay()
                if self._consecutive_empty >= 3:
                    break
                continue

            for item in new_items:
                if _passes_filter(item):
                    results.append(item)
                    if len(results) >= max_count:
                        return results

            if not data.get("has_more", False):
                logger.debug("has_more=False，搜索结果已到末页")
                break

        logger.info(
            "搜索完成: 关键词=%s，共翻 %d 页，保存 %d 条结果",
            keyword,
            page_num,
            len(results),
        )
        return results

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
                logger.warning("等待搜索 API 响应超时")
        finally:
            self._page.remove_listener("response", on_response)

        if result["data"] is not None:
            self._request_count += 1
            await self._maybe_save_cookies()

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
        video_filter: VideoFilter | None = None,
    ) -> list[VideoInfo]:
        """刷推荐流，自动滑动采集视频

        停止条件改为「已保存条数 >= max_count」而非总拦截数，
        确保设置了过滤条件时也能采集到足够的有效视频。

        Args:
            max_count: 最多保存的视频数（过滤后计数）
            on_video: 回调函数，每保存一个视频时调用 on_video(video, saved_idx)
            video_filter: 客户端过滤条件（None 表示不过滤）
        """
        videos: list[VideoInfo] = []  # 已通过过滤的视频
        seen_ids: set[str] = set()
        total_fetched = 0  # 总拦截数（含被过滤的）

        if video_filter and not video_filter.is_empty():
            logger.info("Feed 过滤条件: %s", video_filter.describe())

        logger.info("正在打开抖音推荐页...")
        await self._page.goto(BASE_URL, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(3000)

        # 关闭可能的登录弹窗/通知弹窗
        await self._dismiss_popups()

        # 尝试从首屏 SSR 数据获取第一个视频
        ssr = await self._extract_ssr_data()
        if ssr:
            for key, val in ssr.items():
                if isinstance(val, dict):
                    aweme = val.get("awemeDetail") or val.get("aweme_detail")
                    if aweme and aweme.get("aweme_id"):
                        vid = VideoInfo.from_aweme(aweme)
                        if vid.is_valid and vid.aweme_id not in seen_ids:
                            seen_ids.add(vid.aweme_id)
                            total_fetched += 1
                            # 通过过滤才保存
                            if video_filter is None or video_filter.is_empty() or video_filter.match(vid):
                                videos.append(vid)
                                if on_video:
                                    await on_video(vid, len(videos))

        # 持续监听推荐流 API 响应
        collected: asyncio.Queue = asyncio.Queue()

        async def on_response(response):
            url = response.url
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
                    logger.debug("拦截到 Feed API: %s", url[:120])
                    aweme_list = body.get("aweme_list", [])
                    if aweme_list:
                        for item in aweme_list:
                            await collected.put(item)
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

            swipe_count = 0
            stale_count = 0  # 连续无新视频的滑动次数

            # 停止条件：已保存视频数达到目标（而非总拦截数）
            while len(videos) < max_count:
                # 每 5 次滑动：关闭弹窗 + 验证码检测
                if swipe_count % 5 == 0:
                    await self._dismiss_popups()
                await self._check_captcha()

                # 每隔几次滑动模拟一次鼠标移动
                if random.random() < 0.4:
                    await self._human_move()

                # 模拟下滑（按下箭头键）
                await self._page.keyboard.press("ArrowDown")

                # 使用 _delay() 控制间隔（3-8s × rate_limit）
                wait = random.uniform(3.0, 8.0) * self.rate_limit
                logger.debug(
                    "滑动 #%d，等待 %.1fs（已保存 %d / 总拦截 %d）",
                    swipe_count + 1,
                    wait,
                    len(videos),
                    total_fetched,
                )
                await asyncio.sleep(wait)
                swipe_count += 1

                # 每 10 次滑动打印一次会话统计
                if swipe_count % 10 == 0:
                    self._log_session_stats()
                    self._request_count += 1  # 计一次 feed 请求

                # 从队列取出拦截到的数据，过滤后保存
                new_found = False
                while not collected.empty():
                    item = collected.get_nowait()
                    aid = str(item.get("aweme_id", ""))
                    if not aid or aid in seen_ids:
                        continue
                    seen_ids.add(aid)
                    total_fetched += 1

                    vid = VideoInfo.from_aweme(item)
                    if not vid.is_valid:
                        logger.debug("跳过无效条目: %s", aid)
                        continue

                    # 应用客户端过滤
                    if video_filter and not video_filter.is_empty():
                        if not video_filter.match(vid):
                            continue  # 不满足条件，不计入 videos

                    videos.append(vid)
                    new_found = True
                    if on_video:
                        await on_video(vid, len(videos))
                    if len(videos) >= max_count:
                        break

                if not new_found:
                    stale_count += 1
                    if stale_count >= 15:
                        logger.warning(
                            "连续 15 次滑动未获取到新视频（已保存 %d / 目标 %d），停止",
                            len(videos),
                            max_count,
                        )
                        break
                else:
                    stale_count = 0

        finally:
            self._page.remove_listener("response", on_response)

        logger.info(
            "Feed 采集完成: 总拦截 %d 条，过滤后保存 %d 条",
            total_fetched,
            len(videos),
        )
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
                video.author.verified = bool(
                    user.get("custom_verify", "")
                    or user.get("enterprise_verify_reason", "")
                )
                logger.debug(
                    "补全作者 %s: 粉丝 %d，认证 %s",
                    video.author.nickname,
                    video.author.follower_count,
                    video.author.verified,
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
                                    video.author.verified = bool(
                                        user.get("custom_verify", "")
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
                        logger.debug("关闭弹窗: %s", sel)
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
            logger.debug("提取 SSR 数据失败: %s", e)
            return None
