"""Pydantic 数据模型"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthorInfo(BaseModel):
    uid: str = ""
    sec_uid: str = ""
    nickname: str = ""
    avatar_url: str = ""


class VideoStats(BaseModel):
    play_count: int = 0
    digg_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    collect_count: int = 0


class VideoInfo(BaseModel):
    aweme_id: str
    desc: str = ""
    author: AuthorInfo = Field(default_factory=AuthorInfo)
    stats: VideoStats = Field(default_factory=VideoStats)
    cover_url: str = ""
    video_url: str = ""
    create_time: int = 0
    duration: int = 0
    hashtags: list[str] = Field(default_factory=list)

    @classmethod
    def from_aweme(cls, data: dict) -> VideoInfo:
        """从抖音 aweme 数据构造 VideoInfo"""
        author_data = data.get("author", {})
        stats_data = data.get("statistics", {})
        video_data = data.get("video", {})

        # 提取无水印视频链接
        video_url = ""
        play_addr = video_data.get("play_addr", {})
        url_list = play_addr.get("url_list", [])
        if url_list:
            video_url = url_list[0]

        # 提取封面
        cover_url = ""
        cover = video_data.get("cover", {})
        cover_urls = cover.get("url_list", [])
        if cover_urls:
            cover_url = cover_urls[0]

        # 提取话题标签
        hashtags = []
        text_extra = data.get("text_extra", [])
        for item in text_extra:
            tag = item.get("hashtag_name", "")
            if tag:
                hashtags.append(tag)

        return cls(
            aweme_id=str(data.get("aweme_id", "")),
            desc=data.get("desc", ""),
            author=AuthorInfo(
                uid=str(author_data.get("uid", "")),
                sec_uid=author_data.get("sec_uid", ""),
                nickname=author_data.get("nickname", ""),
                avatar_url=(
                    author_data.get("avatar_thumb", {})
                    .get("url_list", [""])[0]
                ),
            ),
            stats=VideoStats(
                play_count=stats_data.get("play_count", 0),
                digg_count=stats_data.get("digg_count", 0),
                comment_count=stats_data.get("comment_count", 0),
                share_count=stats_data.get("share_count", 0),
                collect_count=stats_data.get("collect_count", 0),
            ),
            cover_url=cover_url,
            video_url=video_url,
            create_time=data.get("create_time", 0),
            duration=video_data.get("duration", 0),
            hashtags=hashtags,
        )


class UserInfo(BaseModel):
    sec_uid: str
    nickname: str = ""
    signature: str = ""
    avatar_url: str = ""
    follower_count: int = 0
    following_count: int = 0
    total_favorited: int = 0
    aweme_count: int = 0
    verified: bool = False

    @classmethod
    def from_user_data(cls, data: dict) -> UserInfo:
        """从抖音用户数据构造 UserInfo"""
        user = data.get("user", data)
        return cls(
            sec_uid=user.get("sec_uid", ""),
            nickname=user.get("nickname", ""),
            signature=user.get("signature", ""),
            avatar_url=(
                user.get("avatar_larger", {}).get("url_list", [""])[0]
            ),
            follower_count=user.get("follower_count", 0),
            following_count=user.get("following_count", 0),
            total_favorited=user.get("total_favorited", "0"),
            aweme_count=user.get("aweme_count", 0),
            verified=bool(user.get("custom_verify", "")),
        )


class Comment(BaseModel):
    cid: str
    text: str = ""
    author_nickname: str = ""
    author_uid: str = ""
    like_count: int = 0
    create_time: int = 0
    reply_count: int = 0
    sub_comments: list[Comment] = Field(default_factory=list)

    @classmethod
    def from_comment_data(cls, data: dict) -> Comment:
        """从抖音评论数据构造 Comment"""
        user = data.get("user", {})
        return cls(
            cid=str(data.get("cid", "")),
            text=data.get("text", ""),
            author_nickname=user.get("nickname", ""),
            author_uid=str(user.get("uid", "")),
            like_count=data.get("digg_count", 0),
            create_time=data.get("create_time", 0),
            reply_count=data.get("reply_comment_total", 0),
        )


class SearchResult(BaseModel):
    items: list[VideoInfo | UserInfo] = Field(default_factory=list)
    cursor: int = 0
    has_more: bool = False
