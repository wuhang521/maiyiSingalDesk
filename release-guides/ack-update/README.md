# 单 Worker ACK 增量助手：仅准备，未部署

候选文件为 `scripts/apply-health-ack-update.py` 和 `scripts/test_apply_health_ack_update.py`。本代理没有 SSH、部署、操作服务、读写业务库、修改配置或触发消息发送。原两文件 updater 与其“collector 原不存在”的历史证明保持原样；本助手明确要求现有 Collector 存在且为 `04e179b61df6d86e3c714c49fca297e9517fc01b8df674c083743d0fa87cf5be`，从不写、删除或伪记它为 absent。

唯一目的文件固定为 `/opt/signaldesk-task/app/health_delivery.py`，before 必须为 `aad239c7566837705a9ef8d9c9bccae90adfa0c3f27eb90fa315816a84bf586e`。输入只能是直接位于 `/var/tmp`、命名 `signaldesk-health-ack-*.py` 的普通单硬链接文件，并匹配 Root 冻结的 after SHA。路径及每级父目录拒绝 symlink；文件大小有上限。Linux 读取使用 `O_NOFOLLOW/O_NOATIME/O_NONBLOCK`，不会阻塞在 FIFO 或为了哈希改变源文件访问时间。

默认 dry-run，固定 Task Python 3.10 `-I -B` 子进程只内存 compile，不 import/exec 新代码。示例：

```text
/opt/signaldesk-task/.venv/bin/python /var/tmp/apply-health-ack-update.py \
  --payload /var/tmp/signaldesk-health-ack-ROOT_REVISION.py \
  --after-sha256 91e5f649ff6773b08b0ba40e92656ed5457b42c811ac873c2a1d3302f1a3130d
```

只有 Root 审核后显式加 `--apply` 才能原子更新这一个 Worker 文件。Root 若后续改变产品 after SHA，应重新冻结输入，不套用上述示例。

apply 使用原 Worker inode 的非阻塞 flock，私有备份路径唯一，目录 root 0700、旧源码及 metadata 为 root 0600。metadata 记录旧 Worker 的字节 SHA/大小、device/inode、uid/gid、mode、nlink、atime/mtime/ctime ns 和扩展属性；也记录现有 Collector 的真实只读 guard metadata，不备份/改写 Collector 正文。xattr 值只保留在服务器私有备份，不输出到 CLI。

更新继承旧 uid/gid/mode/xattrs，新代码保留新文件 mtime 以避免复用旧 `.pyc`。回退恢复旧源码、uid/gid/mode、atime/mtime 与 xattrs；原 inode/device/ctime 只作为审计，原子 replace 不冒称恢复旧 inode/ctime。临时文件写完并 fsync 后，在 replace 前再次核对完整当前 image；安装后再次核对 Worker 与 Collector。失败只在 Worker 仍等于本次精确 afterimage 时恢复；第三 image 保留并明确报告。任何情形都不恢复、删除或覆盖 Collector，不动 DB/env/services/timers，不调用投递。

**13 项本地合成测试 PASS**，覆盖默认零写、单文件安装/私有备份、固定路径/SHA/Collector 必须存在、compile 拒绝、replace 后失败恢复、回退第三 image 保留、暂存期间并发修改拒绝、Collector 外部变化时只回退 Worker、内存 compile 合同。测试使用独立临时目录，生产路径/身份/锁/Task interpreter 均注入替代。Windows 测试不证明 Linux root 权限、flock 或 POSIX xattr/ACL/SELinux 恢复；实际服务器 dry-run/部署由 Root 单独执行。

首轮 12 PASS/1 harness FAIL 是 fixture 将 compile adapter 替换后，该测试误用替代函数，未进入其 spy。只修测试保存原 adapter 的引用；产品 helper 未为此红调整行为。原红日志保留为 `synthetic-tests-harness-red.log`，最终日志为 `synthetic-tests.log`。

已实际发过的旧测试消息不得重发。本助手没有 Worker prepare/deliver/status 或 ntfy/API 请求入口；更新代码本身不证明真实 ACK/客户端验收。最新产品以新 `full-ack-final.*` 后端回归和 Root 的独立真实新 run 为准。
