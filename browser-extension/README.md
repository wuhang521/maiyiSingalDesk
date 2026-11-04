# Hermes 浏览器采集（开发源码，未安装 / 未验收）

Chrome / Edge 扩展管理页开启开发者模式，选择“加载已解压的扩展”，指向本目录。不要在业务电脑上当作已验收版本直接发布；先在最后联合验收时使用授权、去敏样本。

扩展单独登录本人后台，密码不保存，登录 token 仅在 `chrome.storage.session` 的扩展上下文保存，不传入网页。只向用户明确选择的后台 origin 申请权限；远程 HTTP 要明确接受风险，建议先配置 HTTPS。

打开公众号文章或视频号 HTTPS 分享页，点扩展 → “预览当前微信页面” → 核对 / 编辑 → 勾选同意 → 保存。正文只从可见公众号 `#js_content` 读取，没有可见正文时不会把验证码页面当资料。已存在同链接不会自动覆盖旧正文，应回到客户端修订。

视频只有页面直接提供腾讯域名下 HTTPS 原文件、用户确认有权保存、用户批准该 origin 时才尝试获取。最多 25 MB，拒绝重定向、加密 / blob / HLS / DASH、非视频返回以及不符 MP4 / WebM 文件头。来源链接与原视频分别保存；不自动调用云端 OCR / 转写。没有可用原件时请合法保存或录制后到客户端上传。

保持弹窗打开到结束；关闭弹窗或上传未知不自动重试。再次操作先查客户端，手动原件入口有账号范围内幂等去重；原资料已移除不会恢复。

没有持续 content script、Cookie / history 权限或后台监控。`optional_host_permissions` 是申请能力范围，不代表实际取得全站授权；每次仅请求具体后台或媒体的主机 / 端口。不支持精确端口的浏览器拒绝操作，提示升级或改用客户端上传，不把权限放宽到其他端口。参见[官方匹配规则](https://developer.chrome.com/docs/extensions/develop/concepts/match-patterns)。

API 依据：[Chrome activeTab / scripting 文档](https://developer.chrome.com/docs/extensions/reference/api/scripting)、[跨域网络请求](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)、[可选权限](https://developer.chrome.com/docs/extensions/reference/api/permissions)。微信页面 DOM / 媒体可访问性不是承诺，需最后实页验收。
