# 迈医协同 0.6.4 R3

当前分支包含知识库基线及健康管理商业情报频道修复。挂件存活时关闭主窗口会最小化，保留后台收件；明确退出仍会结束接收。构建、测试和本次 Git 同步边界见 [R3 交付记录](docs/delivery/r3-git-sync-20261104.md)。本分支用于源码交接；真实个人客户端更新、每天9点计划启用仍需原交付的实际验收。

以下为通用开发说明，版本与输出文件名以 `package.json` 为准。

SignalDesk 是一个面向 Windows 和 macOS 的通用智能信息频道客户端。用户用自然语言描述长期关注方向，桌面端保存可编辑规则，并通过 ntfy 接收历史消息和实时通知。

## 当前能力

- 自然语言创建通用智能频道
- 编辑包含项、排除项、时间、数量和颜色
- 配置公开 ntfy.sh 或自建 ntfy 服务器
- 支持 Bearer Token
- 读取 ntfy 缓存历史并保持实时订阅
- 发送频道测试消息
- Windows/macOS 系统通知
- 信息搜索、未读、收藏和反馈
- Markdown 正文阅读
- 本地持久化
- Windows/macOS 登录后启动
- Electron Builder 生成 Windows NSIS 与 macOS DMG/ZIP 安装包

## 开发

```powershell
npm install
npm run dev
```

## 检查

```powershell
npm run typecheck
npm test
npm run build:web
npm run e2e
npm run smoke:package
```

## Windows 安装包

```powershell
npm run dist:win
```

安装包输出到 `release/SignalDesk-Setup-${version}.exe`，本分支版本为0.6.4。

## macOS 安装包

macOS 安装包必须在 macOS 上构建：

```bash
npm run dist:mac
```

输出包括 Intel (`x64`) 和 Apple Silicon (`arm64`) 的 DMG/ZIP。仓库中的 GitHub Actions 工作流会同时构建两种架构，并把产物保存为 Actions Artifacts；推送 `v*` 标签时还会创建 GitHub Release。

未配置 Apple Developer 证书时，CI 会生成未签名内测包，首次打开需要在“系统设置 > 隐私与安全性”中确认。正式对外分发时，在 GitHub Secrets 配置 `CSC_LINK`、`CSC_KEY_PASSWORD`、`APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD` 和 `APPLE_TEAM_ID`，electron-builder 会自动签名并公证。

## 使用桌面端

1. 打开“智能频道”，创建或编辑频道。
2. 用自然语言写长期关注方向，再检查包含项、排除项、时间和数量。
3. 填写 ntfy 服务器与 Topic；公开服务可使用 `https://ntfy.sh`。
4. 保存并启用后，桌面端会读取缓存、保持实时订阅并显示 Windows 通知。
5. 频道表格中的纸飞机按钮可发送一条连接测试消息。

Markdown 正文中的 HTTP/HTTPS 链接和“打开原文”按钮都会直接调用系统默认浏览器。

## 后端接口

桌面端已经预留智能信息服务器地址。配置后，创建或编辑频道会向以下接口发送结构化频道规则：

```text
POST /api/v1/channels
```

服务端源码位于 `server/`，包含账号、文章与个人状态、知识库、健康频道别名兼容及采集投递模块。运行说明见 [R3 交付记录](docs/delivery/r3-git-sync-20261104.md)，健康采集说明见 [HEALTH_COLLECTOR.md](server/HEALTH_COLLECTOR.md)。
