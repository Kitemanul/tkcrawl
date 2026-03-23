# 知空 (tkcrawl)

抖音数据采集工具，支持 CLI 命令行和 Web 可视化界面。

基于 Playwright 浏览器自动化，无需逆向签名算法，稳定可靠。

## 安装

```bash
# 克隆项目
git clone <repo-url>
cd tkcrawl

# 安装依赖
uv sync

# 安装浏览器
uv run playwright install chromium
```

## 快速开始

### Web 界面（推荐）

```bash
uv run tkcrawl web
# 打开 http://127.0.0.1:8000
```

Web 界面提供：
- **数据页** — 表格浏览已采集视频，支持 10 种筛选条件（关键词、ID、作者、粉丝数、点赞数、评论数、分享数、收藏数、话题标签、发布时间），点击表头排序
- **采集页** — 可视化配置所有采集参数，实时日志推送，支持扫码登录

### CLI 命令行

```bash
# 扫码登录（首次使用）
uv run tkcrawl login

# 刷推荐流采集
uv run tkcrawl feed --max-count 20

# 采集单个视频
uv run tkcrawl video https://www.douyin.com/video/7616324593950788915

# 采集用户作品
uv run tkcrawl user https://www.douyin.com/user/MS4wLjAB... --max-count 50

# 采集视频评论
uv run tkcrawl comments https://www.douyin.com/video/xxx --max-count 100 --with-replies

# 关键词搜索
uv run tkcrawl search "Python教程" --type video --max-count 30
```

## CLI 命令

| 命令 | 说明 | 关键参数 |
|------|------|----------|
| `login` | 扫码登录抖音 | `--cookie-path` |
| `feed` | 刷推荐流采集视频 | `--max-count` |
| `video <url>` | 采集单个视频信息 | — |
| `user <url>` | 采集用户信息及作品 | `--max-count` |
| `comments <url>` | 采集视频评论 | `--max-count` `--with-replies` |
| `search <keyword>` | 搜索视频或用户 | `--type` `--max-count` |
| `web` | 启动 Web 界面 | `--host` `--port` |

通用参数：`--headless/--no-headless`、`--cookie-path`、`-o/--output`

默认使用有头模式（`--no-headless`），如需显式改回无头模式可传 `--headless`。

## 数据存储

采集的数据以 JSON 格式保存在 `output/` 目录：

```
output/
├── videos/{aweme_id}.json      # 视频信息
├── users/{sec_uid}/
│   ├── profile.json            # 用户信息
│   └── posts.json              # 作品列表
├── comments/{aweme_id}_comments.json
└── search/{keyword}_{timestamp}.json
```

每个视频 JSON 包含：视频描述、作者信息（昵称、粉丝数、关注数、获赞总数、作品数）、播放/点赞/评论/分享/收藏数、封面 URL、视频 URL、话题标签、发布时间、时长。

## 反爬策略

工具内置多重反检测机制，自动生效无需配置：

- 隐藏 `navigator.webdriver` 等自动化特征
- 随机浏览器窗口大小、User-Agent
- 每次操作随机等待 1-5 秒
- 随机鼠标移动模拟真人
- 自动检测验证码并暂停等待
- 登录弹窗自动关闭

## 技术栈

| 组件 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| 包管理 | uv |
| 浏览器自动化 | Playwright |
| CLI | Click |
| 数据模型 | Pydantic |
| Web 后端 | FastAPI |
| Web 前端 | Vue 3 (CDN) |
| 实时通信 | WebSocket |

## License

MIT
