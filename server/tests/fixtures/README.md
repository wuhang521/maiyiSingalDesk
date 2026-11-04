# 公开 Tencent SILK 测试样本

`public-tencent-fixture.amr` 原样复制自[公开仓库的 test.amr](https://github.com/Lidure/astrbot_plugin_airi_voice/blob/master/test.amr)，本地留存日期为 2026-10-28。文件为 Tencent SILK 编码，虽然后缀为 AMR，文件头是 `02 23 21 53 49 4c 4b 5f 56 33`。

- 大小：3,773 字节。
- SHA-256：`e267dac1cef824b188e5c8659a513bbe147dc140fbefdf90ec2fc92ea0c734f3`。
- 公开来源不能证明它由微信导出；测试不将它冒充真实微信私聊原件或识别准确率基准。
- 测试默认使用本目录文件；可通过 `KNOWLEDGE_TEST_PUBLIC_SILK` 覆盖路径，替代文件仍需满足测试中的相同散列断言。
