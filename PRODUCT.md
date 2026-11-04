# 迈医协同

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

使用 Windows Electron 客户端协作的团队成员。管理员负责人员加入、停用和移除。

## Product Purpose

在现有情报、任务和内容创作流程中保存公众号及视频号来源，形成可追溯、可检索的个人知识资料。

## Operating Context

Hermes 消息入口与 React / Electron 客户端共用 FastAPI 后台和个人账号。用户在微信看到内容后收集链接，必要时补充文件或正文。

## Capabilities and Constraints

现有任务、用户管理和内容创作不能因知识库扩展而改变。新功能以 WECHAT-KNOWLEDGE-BASE-PLAN.md 为规划；实际范围见 KNOWLEDGE-BASE.md 与 KNOWLEDGE-OCR.md。视频号分享链接不等于视频文件。私人知识库不授予人员管理员默认阅读权。0.5.3 已接入需主动授权、本人核对后入库的豆包图片 / 扫描 PDF OCR，后台部署至主 Hermes；真实付费识别质量尚未验收。“我的知识库”私聊指令只查询本人已入库资料，原生知识库保持独立。0.5.2 文字综合接口保留，生产默认不启用。团队库、视频画面 OCR、云端语音识别和语义检索尚未实现。

## Brand Commitments

保留“迈医协同”名称、中文操作语言、现有标志和桌面工作台的界面体系。

## Evidence on Hand

用户截图及现有源代码。尚未成功识别用户提供的视频号样例，不以示例数据冒充真实识别结果。

## Product Principles

- 收集成功与识别成功分别呈现。
- 原始素材可保留，检索结果能回到版本及出处。
- 私人内容不越权展示。
- 外部服务不可用时允许补充正文或字幕继续使用。
