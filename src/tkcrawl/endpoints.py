"""抖音 API 端点常量和请求参数构造"""

BASE_URL = "https://www.douyin.com"
API_BASE = "https://www.douyin.com/aweme/v1/web"

# 通用请求参数
COMMON_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "170400",
    "version_name": "17.4.0",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "MacIntel",
    "browser_name": "Mozilla",
    "browser_version": "5.0 (Macintosh)",
    "browser_online": "true",
    "engine_name": "Blink",
    "os_name": "Mac OS",
    "os_version": "10.15.7",
    "platform": "PC",
}

# API 端点路径
ENDPOINTS = {
    "video_detail": f"{API_BASE}/aweme/detail/",
    "user_profile": f"{API_BASE}/user/profile/other/",
    "user_posts": f"{API_BASE}/aweme/post/",
    "comment_list": f"{API_BASE}/comment/list/",
    "comment_reply": f"{API_BASE}/comment/list/reply/",
    "search_general": f"{API_BASE}/general/search/single/",
    "search_user": f"{API_BASE}/search/user/",
}


def build_video_detail_params(aweme_id: str) -> dict:
    return {**COMMON_PARAMS, "aweme_id": aweme_id}


def build_user_profile_params(sec_user_id: str) -> dict:
    return {**COMMON_PARAMS, "sec_user_id": sec_user_id}


def build_user_posts_params(
    sec_user_id: str, max_cursor: int = 0, count: int = 20
) -> dict:
    return {
        **COMMON_PARAMS,
        "sec_user_id": sec_user_id,
        "max_cursor": str(max_cursor),
        "count": str(count),
    }


def build_comment_list_params(
    aweme_id: str, cursor: int = 0, count: int = 20
) -> dict:
    return {
        **COMMON_PARAMS,
        "aweme_id": aweme_id,
        "cursor": str(cursor),
        "count": str(count),
    }


def build_comment_reply_params(
    comment_id: str, aweme_id: str, cursor: int = 0, count: int = 20
) -> dict:
    return {
        **COMMON_PARAMS,
        "item_id": aweme_id,
        "comment_id": comment_id,
        "cursor": str(cursor),
        "count": str(count),
    }


def build_search_params(
    keyword: str,
    search_type: str = "video",
    offset: int = 0,
    count: int = 20,
    sort_type: str = "0",
    publish_time: str = "0",
    filter_duration: str = "",
) -> dict:
    """构造搜索 API 请求参数。

    Args:
        keyword: 搜索关键词
        search_type: "video" 或 "user"
        offset: 分页偏移量
        count: 每页返回数量
        sort_type: 排序方式 — "0" 综合 / "1" 最多点赞 / "2" 最新发布
        publish_time: 发布时间 — "0" 不限 / "1" 一天内 / "7" 一周内 / "182" 六个月内
        filter_duration: 视频时长 — "" 不限 / "0" 1分钟内 / "1" 1-5分钟 / "2" 5分钟以上

    Note:
        这些参数由浏览器在发出真实 API 请求时携带（通过 URL 导航或筛选 UI 点击触发），
        无需手动构造签名。此函数主要用于 URL 回退路径的参数拼接。
    """
    params = {
        **COMMON_PARAMS,
        "keyword": keyword,
        "offset": str(offset),
        "count": str(count),
        "search_source": "normal_search",
        "sort_type": sort_type,
        "publish_time": publish_time,
    }
    if search_type == "video":
        params["search_channel"] = "aweme_video_web"
        # filter_duration 为空字符串时不传（代表不限时长）
        params["filter_selected"] = filter_duration
    return params


# 搜索筛选条件的 UI 文本映射（用于模拟点击搜索页筛选按钮）
SEARCH_SORT_LABELS: dict[str, str] = {
    "0": "",          # 综合排序（默认，无需点击）
    "1": "最多点赞",
    "2": "最新发布",
}

SEARCH_TIME_LABELS: dict[str, str] = {
    "0": "",          # 不限（默认，无需点击）
    "1": "一天内",
    "7": "一周内",
    "182": "六个月内",
}

SEARCH_DURATION_LABELS: dict[str, str] = {
    "": "",           # 不限（默认，无需点击）
    "0": "1分钟以内",
    "1": "1-5分钟",
    "2": "5分钟以上",
}
