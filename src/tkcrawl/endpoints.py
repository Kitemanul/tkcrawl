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
    keyword: str, search_type: str = "video", offset: int = 0, count: int = 20
) -> dict:
    params = {
        **COMMON_PARAMS,
        "keyword": keyword,
        "offset": str(offset),
        "count": str(count),
        "search_source": "normal_search",
        "sort_type": "0",
        "publish_time": "0",
    }
    if search_type == "video":
        params["search_channel"] = "aweme_video_web"
        params["filter_selected"] = ""
    return params
