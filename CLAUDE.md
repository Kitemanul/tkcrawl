# tkcrawl 项目快速参考

## 项目概述

抖音数据采集 CLI 工具，核心思路：用 Playwright 控制真实 Chromium 浏览器访问抖音，拦截浏览器自身发出的 API 响应来获取数据。浏览器天然处理所有签名（X-Bogus / a_bogus / msToken），无需逆向。

**技术栈**：Python 3.11+、Playwright、Pydantic v2、Click、FastAPI/uvicorn（Web 界面）、openpyxl（Excel 导出）、opencv-headless（验证码检测辅助）

**包管理**：`uv`（不用 pip）

---

## 模块职责

| 文件 | 职责 |
|---|---|
| `client.py` | `DouyinClient` 核心类：浏览器启动、API 响应拦截、各数据采集方法、反爬逻辑 |
| `cli.py` | Click CLI 入口，定义所有子命令及参数映射 |
| `endpoints.py` | API 端点常量、`COMMON_PARAMS`、请求参数构造函数 |
| `models.py` | Pydantic 模型：`VideoInfo`、`AuthorInfo`、`VideoStats`、`UserInfo`、`Comment`、`SearchResult` |
| `store.py` | JSON 文件存储，输出到 `output/` 目录树 |
| `auth.py` | Cookie 管理（`load_cookies` / `save_cookies`）和扫码登录（`login_interactive`） |
| `filters.py` | `VideoFilter` 数据类：存储前客户端过滤（第二层筛选） |
| `server.py` | FastAPI"知空"Web 界面后端，WebSocket 实时推送进度 |
| `utils.py` | `extract_aweme_id`、`extract_sec_user_id`、`get_random_user_agent` 等工具函数 |

---

## 数据流

```
启动 Playwright Chromium
  → 加载 cookies（cookies/douyin_cookies.json）
  → 导航到目标页面（视频页/用户页/搜索页）
  → page.on("response") 拦截匹配 URL 的 API 响应
  → 解析响应 JSON → Pydantic 模型（from_aweme / from_user_data）
  → VideoFilter 客户端过滤（可选）
  → store.py 写入 output/ 目录 JSON 文件
  → 关闭前自动保存最新 cookies
```

---

## 关键设计决策

### 搜索筛选：优先 URL 参数导航，失败时回退 UI 搜索

搜索时优先构造带有 `sort_type`、`publish_time`、`filter_selected` 参数的搜索 URL 进行导航，让浏览器自然触发带签名的 API 请求；如果搜索页直达未获取到结果，则回退到“搜索框输入关键词 + 回车”，必要时再点击搜索结果页的筛选按钮应用排序/时间/时长条件。

当前实现的核心目标是：
- 优先保留 URL 直达的简单链路
- 对高风险关键词增加 UI 搜索回退，避免 `page.goto(search_url)` 直接失败时整次搜索返回空
- 在 UI 回退路径上继续复用抖音页面自身的真实请求，而不是手搓 API

### 两层筛选架构

- **第一层（请求端）**：CLI 的 `--sort` / `--publish-time` / `--duration` 参数 → 转换为 API URL 参数，控制抖音服务器返回的数据范围
- **第二层（客户端）**：`VideoFilter.match()` 在存储前过滤，条件全部为 AND 关系；`is_empty()` 为 True 时跳过，零开销

### 反爬措施

- **随机延迟**：`_delay()` 随机等待 3-8 秒 × `rate_limit` 倍率
- **递增延迟**：`_progressive_delay()` 按翻页深度递增（第 1-3 页 1.0x，4-6 页 1.5x，7+ 页 2.0x）
- **指数退避**：`_backoff_delay()` 连续空结果时退避（5s → 10s → 20s → 上限 40s）
- **最大分页限制**：`MAX_SEARCH_PAGES = 10`，防止过度请求触发限流
- **验证码监控**：`_check_captcha()` 检测验证码出现，暂停等待，累计 ≥3 次触发降速建议
- **真人行为模拟**：`_human_move()` 随机鼠标移动、偶发滚动、停顿
- **Cookie 定期保存**：每 30 次请求自动保存，防止长时间运行崩溃丢失登录态
- **随机 UA**：启动时随机选取 User-Agent，随机视口尺寸

### 默认有头模式

- CLI / `DouyinClient` / FastAPI 请求模型 / Web 前端默认都使用有头模式
- 只有显式传 `--headless` 时才走无头模式
- 这样做的原因不是“彻底规避验证码”，而是降低搜索链路在高风险关键词上直接被拦的概率

### Excel 导出与分析

- 根目录有独立脚本 `export_to_excel.py`
- 支持把搜索结果 JSON 导出为 Excel
- 当前会生成 4 个工作表：`抖音视频数据`、`分析概览`、`高赞样本`、`高收藏样本`
- 适合把一批采集结果快速整理成可读分析文件

### Cookie 与登录

- 默认路径：`cookies/douyin_cookies.json`（相对于运行目录）
- `tkcrawl login` 命令打开有头浏览器，扫码后自动保存
- 登录态判断依据：`sessionid` cookie 是否存在
- 每次 `DouyinClient` 关闭时自动保存最新 cookies

---

## CLI 命令速查

```bash
# 安装
uv pip install -e .

# 扫码登录（必须先登录才能采集）
tkcrawl login

# 采集单个视频
tkcrawl video https://www.douyin.com/video/AWEME_ID

# 采集用户信息 + 作品列表
tkcrawl user https://www.douyin.com/user/SEC_UID --max-count 100

# 采集视频评论（含回复）
tkcrawl comments URL --max-count 200 --with-replies

# 搜索视频（默认有头模式，更稳）
tkcrawl search Python --sort latest --publish-time week --min-likes 1000

# 显式切回无头模式（不推荐用于搜索）
tkcrawl search Python --sort likes --publish-time halfyear --headless

# 刷推荐流
tkcrawl feed --max-count 50 --min-likes 5000 --min-followers 50000 --rate-limit 1.5

# 启动 Web 界面（知空）
tkcrawl web --host 127.0.0.1 --port 8000
```

### 搜索参数映射

`--sort`：`comprehensive`(0) / `likes`(1) / `latest`(2)

`--publish-time`：`any`(0) / `day`(1) / `week`(7) / `halfyear`(182)

`--duration`：`any`("") / `short`(0，<1分钟) / `medium`(1，1-5分钟) / `long`(2，>5分钟)

`--rate-limit`：默认 1.0（3-8s 间隔），长时间采集建议 1.5-2.0，触发限流后建议 2.0-3.0

---

## 输出目录结构

```
output/
  videos/{aweme_id}.json          # 视频信息
  users/{sec_uid}/
    profile.json                  # 用户 profile
    posts.json                    # 用户作品列表
  comments/{aweme_id}_comments.json
  search/{keyword}_{timestamp}.json

cookies/
  douyin_cookies.json             # 登录 Cookie（默认路径）
```

---

## 已知限制

- 抖音搜索最多翻 10 页（`MAX_SEARCH_PAGES = 10`），超过后停止
- 搜索比其他功能更容易触发验证码；项目默认使用有头模式，除非显式传 `--headless`
- 即使用有头模式，连续搜索、高风险关键词或账号状态较差时仍可能触发验证码
- 某些关键词会在“搜索页直达”阶段超时；当前已增加 UI 搜索回退，但不能保证所有场景都绕开风控
- Feed 采集时 `follower_count` 数据来自视频接口，同一作者只额外请求一次 profile 补全
- Cookie 过期后需重新 `tkcrawl login`；长时间不使用需重新登录
- API 响应结构随抖音版本变动可能失效，`COMMON_PARAMS` 中固定了 `version_code=170400`

---

## 开发注意事项

- 用 `uv` 管理依赖，修改依赖后运行 `uv pip install -e .`
- `DouyinClient` 是异步 context manager，必须用 `async with DouyinClient(...) as client:` 使用
- 新增采集功能的标准模式：在 `_wait_for_api()` 外套一层导航，拦截对应 endpoint URL
- `VideoInfo.from_aweme(data)` 是解析抖音 aweme 数据的统一入口，新字段从这里扩展
- `verified` 字段来自 `custom_verify` 或 `enterprise_verify_reason`，非空则视为已认证
- Web 界面（server.py）通过 WebSocket 向前端推送实时进度
