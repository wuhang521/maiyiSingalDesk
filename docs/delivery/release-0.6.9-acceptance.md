# 迈医协同 0.6.9 交付验收

状态：正式后台、本机 Windows 客户端、正式自动更新源已更新；全部待发布业务源码及测试、操作脚本已同步到 Git。用户实际业务验收待进行，本次未向真实成员发送微信测试消息。

- [正式发布 v0.6.9](https://github.com/wuhang521/maiyiSingalDesk/releases/tag/v0.6.9)；源码标签指向 `5183e1a8006797fd52e19b323fc826475ce9e923`。
- 功能入口：**用户管理 → 管理成员 → 频道订阅 → 勾选或取消 → 保存频道订阅**。
- 管理员代订阅直接生效，成员无需再次订阅。订阅资格仍在“智能频道 → 订阅权限”设置；缺少资格或微信绑定时会说明原因。
- 本机安装位置：`D:/maiyi/signaldesk-desktop/迈医协同.exe`，版本 0.6.9；保留既有用户数据。
- 同事端自动检查并下载更新，按客户端提示安装并重启后生效。旧版 0.6.8 已从正式源真实自动下载，完整 SHA-512 与发布包一致。

## 需求追踪与实际证据

| 验收项 | 实际结果 | 证据（相对于工程目录） |
|---|---|---|
| 管理员替指定成员开通订阅 | PASS，成员目录立即体现，管理员本人未被代订阅 | artifacts/admin-member-subscriptions-v0.6.9/package-e2e/electron-results.json |
| 管理员取消成员订阅 | PASS，重新打开读取真实状态，取消后目录立即撤销 | artifacts/admin-member-subscriptions-v0.6.9/installed-e2e/electron-results.json |
| 权限、不可订阅原因、旧表单保护 | PASS，47 项频道/微信测试；正式管理员 200、普通成员 403、旧绑定 409 | backend.xml、production-deployment.json |
| 正式后台升级与数据保留 | PASS，备份原文件和数据库；无结构迁移，既有成员/绑定/订阅等散列一致，正式订阅写入 0 | production-deployment.json |
| 本机真实安装更新 | PASS，0.6.8 → 0.6.9，128.05 秒 | local-install.json |
| 视频运行环境复用 | PASS，manifest/Python/FFmpeg 文件 ID、修改时间不变；Python 3.12.10 可执行 | local-install.json |
| 同事更新源 | PASS，旧客户端自动检查下载真实正式安装包并通过完整 SHA-512 校验 | production-download.json、release-published.json |
| Git 同步 | PASS，main、codex/admin-member-subscriptions-v0.6.9 和 v0.6.9；所有待发布业务差异已冻结 | public-commit.json、release-inputs.json |

## 当前版本测试报告

- 前端：27 个测试文件、218 项通过；类型检查与 Vite 构建通过。构建结果不作为实际界面运行的替代证据。
- 全量后台：真实运行 1035 项，首轮 1026 通过、7 跳过、2 项测试基线失败，另有 51 个子测试通过。旧测试用当前 ORM 字段冒充旧库，已修正历史 staff/staff_channel 基线；失败参数与成员迁移共 4 项复测通过。未再次重跑整个 1035 项套件，不把分批复测写成一次全量全绿。
- 桌面更新：38 项通过；实际打包客户端和本机已安装客户端各完成 5 项界面验收，浏览器错误和外网逃逸请求均为 0；后台为隔离合成 SQLite。
- 全量跳过的 7 项：2 项 POSIX 权限/UID 契约不适用 Windows；5 项视频编码需要单独设置本地 FFmpeg/技能 Python。视频运行环境本次未改动，验证了完整保留和 Python 可运行，未声明本次重新编码通过。
- Hermes：独立第一人称任务测试 8 项通过；8 个正式档案的共享任务插件源码散列与本版一致，复用现有已上线版本。旧解析器比较与修复前同为 19 通过、5 项已有差异，无新增失败。普通 Python 缺少 Hermes 运行时的整包 pytest 收集失败单独保留于 plugins.log，不记作通过。
- 未执行：真实成员微信端收发验收、正式 MySQL 并发写入压测、其他同事电脑的实际安装。已验证正式数据库上的鉴权/冲突拒绝、实际客户端的订阅写入与真实发布源下载。

## 产物索引与运行

- 主源码：`D:/maiyi/SignalDesk`，前端 `src/`、后台 `server/app/`、桌面 `electron/`；执行 `npm run typecheck`、`npm run build:web`，Vite 输出 `dist/`。后台依照 `server/README.md` 启动。依赖沿用现有锁文件，无新增依赖。
- 安装包及更新清单：`release/admin-member-subscriptions-v0.6.9/`，共 5 个发布资产；安装包 SHA-256 `ce196d7b4e0038bc41543eda69ab864d7b9dba9b5a44fb978312aa24e766b210`。
- 本次完整证据根目录：`artifacts/admin-member-subscriptions-v0.6.9/`。部署原文件、数据库私有备份在服务器 `/var/backups/signaldesk-task/member-subscriptions-069-1elve0br`，未提交 Git。
- 界面预览：`artifacts/admin-member-subscriptions-v0.6.9/installed-e2e/prototype.html`；桌面和紧凑窗口截图同目录，均为合成数据。

## 明确待验收与后续

1. 用户从正式客户端选择成员，按业务需要开通/取消频道，核对该成员微信收到后续定时推送。当前没有选择任意真实成员执行测试订阅，避免改变业务状态。
2. 旧 Hermes 解析器 5 项已有差异需在单独任务中确认业务契约，清单见 plugin-legacy-comparison.log；本次没有新增失败，未宣称整套插件全绿。
3. 若需验收 POSIX 文件权限、实际视频编码或 MySQL 并发，使用对应环境补验；不影响本次已验证的订阅功能、正式更新源和本机安装结果。

回退：恢复后台备份源文件并重启 API/worker；本次没有结构变更。客户端可回退 0.6.8，既有订阅记录保留。发布资产与源提交均已固定，回退更新源需重新校验清单和安装包散列。

复盘：全量比对发现发布源码漏同步已上线的微信绑定与档案路由，已补齐；迁移回归发现旧测试错误地预建了新字段，已修正测试基线；实际更新用文件 ID 验证了运行环境复用，未仅凭安装成功作结论。
