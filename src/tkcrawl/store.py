"""JSON 文件存储逻辑"""

import json
import time
from pathlib import Path

from pydantic import BaseModel


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _save_json(path: Path, data: object) -> None:
    _ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(data, BaseModel):
            f.write(data.model_dump_json(indent=2))
        elif isinstance(data, list):
            json.dump(
                [
                    item.model_dump() if isinstance(item, BaseModel) else item
                    for item in data
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)


def save_video(output_dir: str, video) -> Path:
    path = Path(output_dir) / "videos" / f"{video.aweme_id}.json"
    _save_json(path, video)
    return path


def save_user_profile(output_dir: str, user) -> Path:
    path = Path(output_dir) / "users" / user.sec_uid / "profile.json"
    _save_json(path, user)
    return path


def save_user_posts(output_dir: str, sec_uid: str, posts: list) -> Path:
    path = Path(output_dir) / "users" / sec_uid / "posts.json"
    _save_json(path, posts)
    return path


def save_comments(output_dir: str, aweme_id: str, comments: list) -> Path:
    path = Path(output_dir) / "comments" / f"{aweme_id}_comments.json"
    _save_json(path, comments)
    return path


def save_search_results(
    output_dir: str, keyword: str, results: list
) -> Path:
    timestamp = int(time.time())
    safe_keyword = keyword.replace("/", "_").replace(" ", "_")[:50]
    path = (
        Path(output_dir) / "search" / f"{safe_keyword}_{timestamp}.json"
    )
    _save_json(path, results)
    return path
