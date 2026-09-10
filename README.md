# DouyinFlame 抖音续火助手

抖音网页版火花（好友连聊天数标记）自动续挂工具。带 Web 控制台，支持联系人管理、火花状态抓取、每日定时发送、开机自启与漏发补发，可一键打包成 Windows 安装包。

> 仅操作自己的账号、发送自己编辑的内容，属于个人自动化范畴。火焰规则与页面结构归抖音所有，本工具随时可能因平台改版失效。

## 功能

- **火花抓取**：滚动读取 IM 抽屉全部会话，解析火花天数、重燃进度（「重燃中 X/3」「N 天后消失」两种文案），一键勾选导入为联系人
- **定时续火**：每个联系人独立发送时间，APScheduler cron 触发，队列 + 单工人串行消费，当天已发自动跳过
- **启动补发**：服务启动时比对 `last_sent`，计划时间已过但今天没发的自动入队，覆盖关机错过 cron 的场景
- **送达判定**：真实键盘事件输入 + 回车后校验编辑器清空，规避富文本编辑器零宽字符与虚拟列表气泡计数不稳的误判
- **Web 控制台**：联系人增删改、手动单发/全发、日志实时滚动、登录二维码弹出
- **exe 安装包**：PyInstaller + Inno Setup，自带 Chromium 内核，开机自启（注册表 Run 键），卸载保留数据

## 工作原理速览

抖音已下线独立 `/message` 页，IM 是首页右侧抽屉。核心难点与对策：

| 坑 | 对策 |
| --- | --- |
| DOM 类名混淆且常变 | 多级候选选择器 + `data-e2e` 锚点 + 文本匹配兜底 |
| 会话条目名字约 1s 才渲染，提前读到 UID 占位符 | 滚动比对前等待名字渲染完成 |
| 标题节点混入零宽字符 `\u200b` | 规范化剔除后精确比较 |
| 打开聊天后列表节点留在 DOM 但被隐藏 | 每续一人整页重开面板，状态确定性优先 |
| editor-kit 富文本 `fill()` 不同步内部状态 | `keyboard.type` 真实键盘事件 |
| 虚拟列表滚动渲染 UID 占位行 | 抓取按 scrollTop 停滞判底 + 回顶二次复核 |

## 快速开始（源码运行）

```bash
# Python 3.12+，先装依赖
pip install -r requirements.txt
playwright install chromium

# 启动（默认 127.0.0.1:8765）
python -m app.main
# 或双击 start.bat
```

浏览器打开 `http://127.0.0.1:8765`，点「扫码登录」用抖音 App 扫码，登录态持久化到 `user_data/`。

## 构建安装包

```bash
# 1. PyInstaller 打包 + 复制 Chromium 内核（需本机已有 ms-playwright 内核）
python build_exe.py

# 2. Inno Setup 编译安装包（需安装 Inno Setup 7）
ISCC.exe DouyinFlame.iss
# 产物：installer/DouyinFlameSetup.exe
```

安装位置 `%LOCALAPPDATA%\DouyinFlame`，免管理员权限；数据（`douyin.db`、登录态）落在 exe 旁，卸载自动保留。

## 目录结构

```
app/
  bot.py         # Playwright 自动化内核：登录/抓取/查找/发送
  scheduler.py   # cron 调度 + 发送队列 + 看门狗 + 启动补发
  main.py        # FastAPI 路由 + 控制台静态页
  db.py          # SQLite 数据层（contacts/logs/settings）
static/index.html# 单页控制台（深色 UI）
run_app.py       # PyInstaller 入口（单实例/自启动/浏览器内核路径）
DouyinFlame.spec # PyInstaller 配置
DouyinFlame.iss  # Inno Setup 配置
build_exe.py     # 一键构建脚本
```

## 风险提示

- 频繁自动化操作可能触发抖音风控（验证码、限流甚至账号限制），请保持低频、内容个性化
- 登录态文件等同于账号凭证，**不要分享** `user_data/` 目录
- 页面结构随平台改版可能失效，选择器均带兜底候选，坏了按报错日志调

## License

[MIT](LICENSE)
