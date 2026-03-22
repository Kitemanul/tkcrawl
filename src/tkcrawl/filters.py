"""数据过滤器 — 存储前的客户端筛选（第二层防护）

设计原则：
- 所有条件均为 AND 关系（同时满足才保留视频）
- 关键词匹配在描述文本 + 话题标签中进行
- 任何条件为默认值（0 / 空）表示该条件不启用
- is_empty() 为 True 时跳过过滤逻辑，保持零性能开销
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("tkcrawl")


@dataclass
class VideoFilter:
    """视频筛选条件（存储前过滤，作为第二层防护）

    字段说明：
    - 数值类条件：0 表示不限，> 0 表示最小值要求
    - max_followers：0 表示不限上限
    - keywords：OR 关系，含任一词即通过
    - exclude_keywords：OR 关系，含任一词则排除
    """

    # 互动数下限（0 = 不限）
    min_likes: int = 0
    min_comments: int = 0
    min_shares: int = 0
    min_collects: int = 0

    # 作者粉丝数范围（0 = 不限）
    min_followers: int = 0
    max_followers: int = 0  # 0 = 无上限

    # 文本关键词（空列表 = 不限）
    keywords: list[str] = field(default_factory=list)          # 须含任一词（OR）
    exclude_keywords: list[str] = field(default_factory=list)  # 含任一词则排除（OR）

    # 作者认证状态
    verified_only: bool = False  # True = 仅保留已认证用户的视频

    # ---- 公开接口 ----

    def is_empty(self) -> bool:
        """判断是否没有启用任何筛选条件，用于快速跳过过滤逻辑"""
        return (
            self.min_likes == 0
            and self.min_comments == 0
            and self.min_shares == 0
            and self.min_collects == 0
            and self.min_followers == 0
            and self.max_followers == 0
            and not self.keywords
            and not self.exclude_keywords
            and not self.verified_only
        )

    def match(self, video) -> bool:
        """判断视频是否满足所有筛选条件。

        Args:
            video: VideoInfo 实例

        Returns:
            True — 保留此视频；False — 跳过
        """
        s = video.stats
        a = video.author
        aid = video.aweme_id

        # ---- 互动数过滤 ----
        if s.digg_count < self.min_likes:
            logger.debug(
                "过滤[点赞不足] %s: %d < %d", aid, s.digg_count, self.min_likes
            )
            return False

        if s.comment_count < self.min_comments:
            logger.debug(
                "过滤[评论不足] %s: %d < %d", aid, s.comment_count, self.min_comments
            )
            return False

        if s.share_count < self.min_shares:
            logger.debug(
                "过滤[分享不足] %s: %d < %d", aid, s.share_count, self.min_shares
            )
            return False

        if s.collect_count < self.min_collects:
            logger.debug(
                "过滤[收藏不足] %s: %d < %d", aid, s.collect_count, self.min_collects
            )
            return False

        # ---- 粉丝数过滤 ----
        if self.min_followers > 0 and a.follower_count < self.min_followers:
            logger.debug(
                "过滤[粉丝不足] %s: %d < %d", aid, a.follower_count, self.min_followers
            )
            return False

        if self.max_followers > 0 and a.follower_count > self.max_followers:
            logger.debug(
                "过滤[粉丝超限] %s: %d > %d", aid, a.follower_count, self.max_followers
            )
            return False

        # ---- 认证状态过滤 ----
        if self.verified_only and not getattr(a, "verified", False):
            logger.debug("过滤[未认证] %s: 作者 %s", aid, a.nickname)
            return False

        # ---- 关键词过滤 ----
        # 将描述 + 话题标签合并为一段文本进行匹配（不区分大小写）
        text = (video.desc + " " + " ".join(video.hashtags)).lower()

        if self.keywords and not any(kw.lower() in text for kw in self.keywords):
            logger.debug("过滤[无关键词] %s: 须含 %s", aid, self.keywords)
            return False

        if self.exclude_keywords and any(
            kw.lower() in text for kw in self.exclude_keywords
        ):
            logger.debug("过滤[含排除词] %s: 含 %s", aid, self.exclude_keywords)
            return False

        return True

    def describe(self) -> str:
        """返回当前过滤条件的可读描述（用于日志和控制台输出）"""
        parts = []
        if self.min_likes:
            parts.append(f"点赞≥{self.min_likes:,}")
        if self.min_comments:
            parts.append(f"评论≥{self.min_comments:,}")
        if self.min_shares:
            parts.append(f"分享≥{self.min_shares:,}")
        if self.min_collects:
            parts.append(f"收藏≥{self.min_collects:,}")
        if self.min_followers:
            parts.append(f"粉丝≥{self.min_followers:,}")
        if self.max_followers:
            parts.append(f"粉丝≤{self.max_followers:,}")
        if self.keywords:
            parts.append(f"含词{self.keywords}")
        if self.exclude_keywords:
            parts.append(f"排除{self.exclude_keywords}")
        if self.verified_only:
            parts.append("仅认证")
        return "、".join(parts) if parts else "无"
