# 个人知识库 OCR · 0.5.3

2026-10-08：主 Hermes `113.46.159.100` 的个人知识库后台与插件已增量部署。接入的是豆包 / 火山方舟云端文字识别，并非新增服务器本地 OCR 模型。依据医疗项目已有适配协议，复用主 Hermes 服务器上的 Ark 凭据；没有更改医疗 Java 服务、Hermess2 或主 Hermes 原有 `knowledge-base` 插件。

## 使用

1. 安装 `release/knowledge-ocr/SignalDesk-Setup-0.5.3.exe`，用原有个人账号登录；现有已安装客户端未被自动替换。
2. 左侧“知识库 → 上传文件”，选择自己的 JPG、PNG 或 PDF。每件不超过 20 MB，云端 PDF 识别最多 10 页。普通文字型 PDF 仍可先用本地解析；图片和扫描 PDF 要主动请求 OCR。
3. 详情中选择原件，阅读整份文件发送到云端和可能计费的说明，主动勾选同意，点击“发送文件并识别”。不自动发送历史资料。
4. 识别结果为“待核对”，不参与检索或问答。下载原件逐页检查；有错可“补充 / 修订正文”，或确认“已核对，入库”。确认不再次调用模型；修订保留正文版本，但人工重写的定位可能变为正文段落。
5. 客户端“知识问答”按关键词查找原文。私聊 Hermes 可发送 `我的知识库搜索：关键词` 或 `我的知识库问：问题`，返回本人已入库正文与页码 / 版本出处。当前主实例此入口使用原文摘录，不调用额外综合模型。

其它私人指令：`收藏到我的知识库：公众号或视频号链接`、`我的知识库状态：资料编号`。这些指令要有冒号。原来的 `知识库搜索` 等指令仍归原生知识库，二者数据不混用。人员须在“用户管理”中绑定实际微信 / QQ / 企业微信接收 ID，并保持人员与个人账号启用；群聊不展示私人资料。

## 失败、隐私与边界

- 整份所选原件会发送到豆包 / 火山方舟，不会隐式脱敏。不要未经许可上传个人、患者或第三方保密资料。模型密钥仅存在服务器，客户端不持有。
- 不自动重复付费 OCR。失败、超时或识别进程中断保留原件；未知结果可能已计费，手动再次识别可能再次计费。
- PDF 云端上传使用通用文件名，请求约一天到期；在推理前登记持久清理任务，结束后尝试删除，失败退避重试。到期和删除均依赖供应商履约，不承诺绝对零留存。图片通过 Responses 请求发送；`store=false` 不等于所有供应商日志均无留存。
- 重新识别时，旧正文版本保留，但当前资料在待核对或失败期间不参与检索；不会把旧正文冒充新识别结果。
- OCR 为 AI 转录，可能漏字、错数字或幻觉，不作诊断；确认入库不等于真实性验证。模型必须返回完整逐页 JSON，否则不入库。
- 不自动取得视频号视频，不绕过微信验证。视频画面 OCR、云端语音转写、团队共享、语义向量检索、Hermes 聊天附件入库与识别完成推送仍不在此次实现范围。

## 上线与配置

后端目录 `/opt/signaldesk-task`，迁移 `20261008_03`；增量新增八张知识库相关表，保留原账号、任务和内容表。API、原任务 worker 和新增 `signaldesk-knowledge-worker.service` 分开运行。识别 worker 单进程串行；不把无自动重试当成总账单限额，正式开放需设置供应商预算和账号额度。

原件私有目录 `/var/lib/signaldesk-knowledge`。回收站自动过期保持关闭。服务器备份 `/var/backups/signaldesk-task/knowledge-20261008T060901Z` 含原代码、配置与数据库；保存在服务器，不复制到客户端。

新增配置见 `server/.env.example`：`TASK_KNOWLEDGE_OCR_ENABLED`、`TASK_KNOWLEDGE_OCR_API_KEY`、`TASK_KNOWLEDGE_OCR_MODEL`、`TASK_KNOWLEDGE_OCR_MAX_PDF_PAGES`。当前模型 `doubao-seed-2-0-lite-260428`，真实模型权限 / 识别质量尚未付费验收。关闭 OCR 后已有云端文件清理仍可用保留的 Key 执行；不要在清理队列未清空时删除该凭据。

主 Hermes 的专用连接文件 `/home/hermes/.hermes/signaldesk-knowledge.env` 映射为 `/opt/data/signaldesk-knowledge.env`，只保存连接地址和独立签名 secret；未改其原 `.env`。旧有全局 `TASK_API_KEY` 不用于决定私人资料所有者。

部署脚本 `scripts/deploy-knowledge-remote.py` 仅适用于此次已核实的旧版本基线，不是重复执行的通用安装器。它先校验候选导入、备份后暂停旧服务迁移，核对旧业务记录数量，再启动服务；失败恢复代码 / 配置并保留新增表和原件，不做有损数据库降级。

## 已验证与尚未验证

后台 / 原任务插件 133 项测试通过；客户端 64 项测试、类型检查及 Vite 构建通过。源码 Electron 完整流程在隔离 SQLite 与 MockTransport 上通过，覆盖主动授权、未核对排除、确认、页码引用、失败不重复提交及列表 / 详情同步。测试文字明确为合成结果，不能当作真实 OCR 质量证据。

生产健康检查、迁移、三个后台服务、插件实际注册、已绑定来源签名查询、未绑定拒绝、群聊拒绝及不截获原生指令通过。没有向真实聊天发送测试消息，没有上传真实资料，也没有调用付费 OCR。

源码及最终打包后的 `win-unpacked` Electron 全流程均通过，机器记录分别为 `artifacts/knowledge-ocr/result.json` 和 `artifacts/knowledge-ocr-package/result.json`。两者均 `passed=true`，后者 `packaged=true`，包含三个桌面尺寸的 OCR 六种状态。真实安装程序交互安装未进行，当前用户安装与登录状态未改变。

独立界面审阅最初要求修正列表 / 详情失败状态矛盾；一批修正、64 项前端测试及重新截图后，该唯一修正被评分为 resolved，最终 disposition 为 `ship`，范围是该修正的复核，不扩大为真实模型质量批准。自动检测器因 engine 0.1.5 缓存访问失败未运行成功。体系保留记录见 `KNOWLEDGE-OCR-DESIGN-REVIEW.md`。

Windows x64 安装包 `release/knowledge-ocr/SignalDesk-Setup-0.5.3.exe`：102,420,060 字节；SHA-256 `C81AB00EF66E9C65EE4448EF71B0AE037CD5D0C77501CA75752713AD14EEAC4F`。安装包只含桌面端，旧版本安装包保留。

接口约定参照[火山方舟文件上传](https://docs.volcengine.com/docs/ark/upload-files-api?lang=zh)、[文件管理](https://docs.volcengine.com/docs/ark/file-api?lang=zh)及[Responses 生命周期](https://docs.volcengine.com/docs/ark/response-lifecycle?lang=zh)。
