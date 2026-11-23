# 实现与运维

沿用 shared-task-router 原生 `pre_gateway_dispatch` 和原生命令注册。hook 仅核对当前档案自己的微信私聊身份并改写为一次性命令；实际保存位于原生消息接纳之后，再核对当前档案及扫码身份。群聊、其他用户和内部消息不进入个人引导。无需改动 Hermes 核心、原生复制、工具授权机制或客户端界面。

## 代码与运行

| 入口（相对 SignalDesk） | 职责 |
|---|---|
| hermes-plugin/shared-task-router/onboarding.py | 本地会话状态、原子保存、称呼及编号解析 |
| hermes-plugin/shared-task-router/onboarding_gateway.py | 本人私聊边界、一次性命令、换绑复核 |
| hermes-plugin/shared-task-router/onboarding_api.py | 只读本人档案的服务地址和凭据 |
| hermes-plugin/shared-task-router/operations-guide.md | 可重查的中文语句模板 |
| hermes-plugin/channel-nickname-prefix/__init__.py | 使用本人设置的称呼，兼容原生新进程及热加载 |
| server/app/channel_onboarding.py | 已绑定微信身份到迈医成员的受保护订阅桥接 |
| server/app/channel_subscriptions.py | 复用原管理员订阅事务，不改变其规则 |

API 使用现有服务级凭据，普通成员 token 不能调用。`POST /api/v1/channel-onboarding/catalog` 接收 `profile` 与 `recipient_id`，只返回本人可订阅频道、已有订阅和绑定修订指纹。`PUT /subscriptions` 再接收选择项及旧快照，取“已有订阅 ∪ 新选择”；绑定或订阅变化时返回冲突。无数据库迁移。

个人状态存放当前档案的 `personal-onboarding.json`，以档案名和当前微信身份的散列标记归属。文件锁与临时文件替换保护并发/重启；重复消息不再次推进步骤。两种称呼写入本档案 SOUL.md 的专属区块，保留其他规则。复制或换绑检测到身份变化后清除旧区块并重新收集，不借用昵称修改迈医身份。

目录或订阅接口失败时保留对话状态，回复中文重试提示；未关联迈医账号时可以先完成称呼并跳过订阅。频道授权由管理员维护，本引导不扩大权限，也不撤销管理员已有订阅。天气沿用已设置城市，不重新逐人询问；default 默认北京、工作日 09:00/18:00。

本地测试（工作目录为 SignalDesk）：

```powershell
D:/maiyi/.venv-signaldesk/Scripts/python.exe -X utf8 -m unittest discover -s hermes-plugin/shared-task-router/tests -p 'test_onboarding*.py'
D:/maiyi/.venv-signaldesk/Scripts/python.exe -X utf8 -m unittest discover -s hermes-plugin/channel-nickname-prefix/tests -p test_personal_prefix.py
D:/maiyi/.venv-signaldesk/Scripts/python.exe -X utf8 -m pytest server/tests/test_channel_subscriptions.py server/tests/test_channel_push_worker.py server/tests/test_wechat_pairing.py -q
```

后台沿用现有 FastAPI 启动方式；在原路由中包含 channel_onboarding.router。Hermes 沿用已安装插件，配置 `personal_onboarding.enabled: true`，并在该成员自己的 `.env` 配置现有 TASK_API_BASE_URL/TASK_API_KEY。default 不配置任何成员的消息连接或收件人。

## 发布与证据

正式暂存及非公开证据位于服务器 `/home/hermes/desktop-fixes/member-onboarding-20261123`，本机测试日志位于 `artifacts/hermes-first-bind-onboarding-20261123`。公开文档仅保留合成对话、结果和源码散列，不包含连接凭据或真实微信标识。

本次部署脚本是锁定本次基线的运维脚本，重复执行会拒绝，不应当作通用覆盖命令：

1. `server/scripts/check_hermes_onboarding_release.py`：实际 Hermes 容器及隔离 HTTP 后台，两个合成成员；验证原生复制和 SOUL 加载。
2. `server/scripts/deploy_hermes_onboarding.py`：核对基线，备份数据库与源文件，只发布两个接口文件及 main 路由锚点；保留正式 main 的其他差异。
3. `scripts/deploy-hermes-onboarding-profiles.py`：更新 8 个档案的插件和单项开关；现有微信成员种入完成标记；原生热加载。
4. `scripts/update-hermes-onboarding-prefix.py`：修复旧模块导入路径，只更新已有称呼插件的 default、futaotao、songwei 三份副本。
5. `scripts/check-hermes-onboarding-template.py`：从正式 default 原生复制到隔离目录，验证无渠道连接、无个人引导状态、默认城市和两条暂停待绑定的天气任务。
6. `scripts/check-hermes-onboarding-production.py`：只读检查正式配置、老成员不重新引导及真实频道目录；不订阅、不发送微信。

后端发布前暂停 API、worker、push 三个服务，完成备份及替换后全部启动并验证 active。staff、staff_channel、user_account、smart_channel、channel_subscription、channel_push_config 六表前后散列一致，真实订阅写入为 0。配置 `.env` 与微信路由文件不变。

后台备份 `/var/backups/signaldesk-task/hermes-onboarding-x752gs1s`；Hermes 首次备份 `/opt/data/.onboarding-backups/20261123T062403Z`；称呼插件补丁备份 `/opt/data/.onboarding-backups/prefix-native-path-s8zttbyw`（容器路径）。备份包含私人配置，只在服务器保留。

## 回退

如需停用引导，先把各档案 `personal_onboarding.enabled` 设为 false 并使用原生热加载；保留个人状态、称呼和已有订阅，不删除业务数据。完整代码回退时，从上述备份逐文件恢复插件、后台 main.py/channel_subscriptions.py，并移除本次新增的 channel_onboarding.py，再重启后台服务。称呼插件有第二份独立备份，按两次发布逆序恢复。

本次没有业务数据迁移，通常不需要还原数据库。用户使用后已新增的订阅不能被旧数据库覆盖。配置若在上线后由成员修改，应逐项合并，而非全量恢复备份。
