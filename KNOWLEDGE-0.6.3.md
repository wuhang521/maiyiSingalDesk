# 知识库0.6.3

继承0.6.2的原生SILK解码与千问草稿核对链路。本轮修复Hermes附件缓存硬链接/变化竞态：先校验并生成私有快照，只上传该快照，保留原缓存；签名附件入口兼容autoflush两种会话配置，重放返回409。

新增控件状态、真实回环HTTP和并发验收。公开SILK fixture仅用于软件链路测试，来源及哈希见 `server/tests/fixtures/README.md`；它没有已验证的微信导出来源，不作中文识别质量基准。测试默认不发送外部消息、不提交付费模型。

启动：`npm ci`、`npm run dev`；验证：`npm run build`，后端依项目Python环境运行 `python -m pytest server/tests -q`。后端数据库、私有存储及千问密钥只放服务端配置，SILK依赖见 `server/requirements-silk.txt`。QQ按本轮用户要求不验收。

当前交付使用独立0.6.2快照叠加此修复，未纳入并行PaddleOCR迁移。正式原生扩展权限/工具栏、真实微信宿主与正式NSIS向导仍需独立原生环境验收，不能以组件、模拟信封或程序目录文件替换代替。
