# 阶段确认钩子（Hermes 当前约定）

默认四站：`inputs` 文案与人物图 → `voice` 配音试听 → `generation` 生成方案与范围（动作、参数、真实时长及段数） → `video` 成片效果。只做配音时到 voice 为止。Hermes 本机 `TASK_VIDEO_COST_REVIEW_REQUIRED=0`，后端子进程传 `OLDX_HERMES=1` 与 `OLDX_COST_REVIEW_REQUIRED=0`：不估价、不要求金额预算或费用复选框。首次云端制作前只提示一次“云端生成按服务商规则计费。”

用户确认当前范围后实际点击生成，项目 API 发送 `generation_consent=true`，固定本次项目版本、范围及幂等键。数字人范围是整条真实配音，按检测结果自动分段；不能因选中一条分镜就声称只生成该镜头。后端映射确认并执行脚本，客户端或 agent 不通过手工 `--yes` 绕过 API。同一点击超时先查原任务；新批次或失败后的重新制作需要新的生成操作，不能自动重复付费。

当前新普通视频使用火山官方 Seedance 2.0 Mini（北京方舟接口，模型 `doubao-seedance-2-0-mini-260615`），API 对应 `expected_provider=seedance`，使用 Hermes 已保存的独立 Ark 视频凭证；数字人保持 OLDX RunningHub 及原企业共享 Key，千问配音保持原 DashScope 凭证。旧 RunningHub/Ark 已提交任务只能按原供应商续查或下载，切换默认通道不构成重新提交授权；旧批次未提交部分需按实际通道重新确认，不把旧确认改写成新的生成操作。

Hermes 由受控适配器初始化并衔接确认记录。下面保留的 CLI 初始化和命令用于后端内部、非 Hermes 独立运行或显式 legacy 项目追踪，不覆盖本机当前流程。独立脚本新建项目和 `project.py init` 命中旧项目时自动启用；直接接手旧项目先运行 `python scripts/review.py init --workdir <项目目录>`。没有 `review_hooks.json` 的旧脚本调用保留原有试听与跳剪检查。不要通过删除记录规避钩子。

## 怎么走

1. 文案放 `copy.txt`，已授权的人物图等素材放项目 `assets/`。先备齐本次要用的素材，再展示文案、人物图和声音来源；Hermes 可用千问系统声，不强制提供克隆样本。
2. 由适配器运行 `present` 冻结当前版本，把对应素材实际展示给用户；命令输出路径不等于用户已经看过。`generation` 的本地预览展示动作、参数、段数、真实时长和范围，不调用费用估算。仅非 Hermes 或明确启用旧报价模式时使用历史费用步骤。
3. 用户实际回复或点击确认后，运行 `approve`，只记录真实原话或实际操作。记录的 `quote` 表示确认原话，不是报价。用户明确免确认某一阶段时用 `skip` 记录那句话；只作用于指定阶段和当前版本，不代替新任务的 `generation_consent`。已经授权的跳过不重复询问，`--yes` 不是确认原话。
4. 继续下一步；素材或参数变化会让相关记录变成 `stale`，需重新展示和确认。

以下是独立/legacy 脚本衔接示例；Hermes 本机由 API 适配器执行相应步骤，不把这些命令直接当作用户生成授权：

```text
python scripts/review.py present inputs --workdir <项目目录> --json
python scripts/review.py approve inputs --workdir <项目目录> --user-approved "用户真实回复的原话" --json
python scripts/pipeline.py voice --workdir <项目目录> --yes --json
python scripts/review.py approve voice --workdir <项目目录> --user-approved "用户真实回复的原话" --json
python scripts/avatar/run_avatar.py --workdir <项目目录> --json
python scripts/review.py present generation --workdir <项目目录> --json
python scripts/review.py approve generation --workdir <项目目录> --user-approved "用户真实回复的原话" --json
python scripts/avatar/run_avatar.py --workdir <项目目录> --yes --json
python scripts/pipeline.py finish --workdir <项目目录> --json
python scripts/review.py approve video --workdir <项目目录> --user-approved "用户确认可以跳剪的真实原话" --json
python scripts/avatar/jumpcut.py --workdir <项目目录> --yes --json
```

`present generation` 的 `--instance`、`--concurrency`、`--only` / `--all`、`--seed` 必须与实际生成一致。例如独立模式只重跑第二段时，两条命令都带 `--only 2`。确认绑定实例、应用和工作流、有效并发、实际段数、时长、范围及素材和提示词内容；改变这些条件不能沿用旧确认。Hermes 默认直接模式的上下文包含 `cost_review_required=false`，不包含 `estimate_coins`。估价指纹只适用于非 Hermes 或显式开启 `TASK_VIDEO_COST_REVIEW_REQUIRED=1` 的 legacy 模式。

配音完成后 `pipeline.py voice` 自动准备 voice 确认；拼接质检完成后 `pipeline.py finish` 自动准备 video 确认。单独调用其他工具时手工 `present`。统一确认会衔接原有 `voice_review.json` / `avatar/review.json` 检查，不需重复填写原话。

Hermes 数字人默认不去停顿、不烧录字幕。启用去停顿时，先播放当前版本最新成功数字人任务的原片，再记录关联其 `job_id` 的 `postprocess` 确认，不能跳过；适配器将其映射到 OLDX 原片确认。后处理完成后仍需重新验收最终成片，不能用原片确认冒充最终 `video` 验收。

## 中断、重做和交付

`python scripts/review.py status --workdir <项目目录> --json` 查看每站：`missing`、`pending`、`approved`、`skipped`、`stale`、`consumed`。事件与真实确认原话存在项目 `review_hooks.json`；不会保存 API Key。

生成方案的确认只供一次提交批次使用，开始上传前标为 consumed。云端 pending 任务续查可继续沿用原任务，不重新提交。新任务、失败后重试一个新批次或 `--only` / `--all` 重做，都要准备并确认新方案；Hermes 还需新的实际生成操作，不能自动重发。非 Hermes 独立运行原有重试策略仅适用于独立流程，不能覆盖此约定。预览、查询本地状态和取消任务不受阶段钩子拦截。

缺少普通阶段确认时执行命令退出 5（`need_user`），输出 `review_stage`、`wait_for_user=true` 与下一步；不能自动重试绕过。展示真实 `final_avatar.mp4` 请用户确认，后处理按已确认开关执行。费用字段缺失不构成本机直接模式的阻断，也不能据此宣称免费。人物与声音授权要求始终保留。

医疗主题使用同样的普通阶段确认，用户确认当前方案并点击生成后即可制作，不另设医疗资质、专业审稿证明或额外必需确认钩子。策划时核对事实来源，专业医学审稿仅为可选建议；不得虚构医生身份或已审结论，不能把用户的内容确认记录成专业医学审核。

Hermes 通过客户端实际确认与生成操作记录这些检查点；非 Hermes 独立运行通过真实对话回复记录。它们不是 RunningHub 网页按钮，也不能验证用户是否真正听完音频或看完视频。不要把脚本接受一段文字说成已经取得真实用户确认。
