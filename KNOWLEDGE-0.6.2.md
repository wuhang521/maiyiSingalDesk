# 知识库 0.6.2：原生 SILK

基于已验收 0.6.1，加入标准 / Tencent SILK 本地解码、SLK/AUD/AMR 原件导入及按文件头识别。服务端需安装 requirements-silk.txt，配置 TASK_KNOWLEDGE_SILK_ENABLED=true。只在本人明确同意后发送已转换音轨到千问；草稿需核对后参与引用。

启动：npm ci；npm run dev。后端沿用 KNOWLEDGE-0.6.1.md 的启动配置与数据库迁移 20261008_12。原生依赖为服务端 pilk 0.2.4，Linux CPython 3.10 x86_64 wheel 及对应原始源码、许可证、散列在本轮交付目录。其他平台需在低权限离线环境重新构建。

本交付快照排除了共享源码中正在进行的 PaddleOCR 迁移，保留 0.6.1 的 OCR 协议；当前共享工作区后续变更不能视为本包源代码。

公开测试语音已跑通真实解码与千问草稿/引用；其微信导出来源尚无证明。受环境站点策略限制，公众号真实页面及原生扩展授权、私聊真实附件仍有待验收项。QQ 测试已由用户取消。详细证据见 D:/maiyi/docs/delivery/knowledge-final-acceptance-20261028/delivery.md。
