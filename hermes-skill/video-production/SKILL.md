---
name: video-production
description: 在 Hermes 中从主题规划普通 AI 视频或明确指定的数字人视频，分步确认方案、声音、生成范围和成片，通过持久化项目生成、恢复、合成并导出视频。
---

# Hermes 视频制作

用户只给主题时，先用 `video-prompt-director` 规划完整内容。默认普通视频，用户明确指定时才选数字人；默认发布视频号、抖音和小红书。先说明受众、人设、核心结论、画面风格、旁白、分镜、开头/中段/结尾的钩子和兑现位置，再请用户确认。可从项目列表恢复，不另建重复项目。

本机默认 `TASK_VIDEO_COST_REVIEW_REQUIRED=0`：输入主题 → 确认方案 → 生成并试听配音 → 确认生成范围并点击生成 → 验收成片。无需报价、预算金额输入或费用复选框；首次云端制作前只简短提示一次“云端生成按服务商规则计费。”新普通视频调用火山官方 Seedance 2.0 Mini，使用北京方舟接口 `https://ark.cn-beijing.volces.com/api/v3`、模型 `doubao-seedance-2-0-mini-260615`，优先复用 Hermes 已保存的独立 Ark 视频凭证。数字人仍使用 OLDX RunningHub 工作流及原企业共享 Key；不能据此声称数字人底层模型也是 Seedance。策划复用 Hermes 模型，千问配音及识别复用 Hermes 的 DashScope 凭证。各用途凭证分别保留，不能互相替用或在失败后静默切换供应商。

## 服务连接

此包通过已认证的 Hermes 视频项目 API 执行，不直接发送任意 Shell 指令。启动器/管理员配置 `HERMES_VIDEO_API_BASE_URL` 与当前用户的 `HERMES_VIDEO_ACCESS_TOKEN`；令牌不要放在文案、命令行或文件提交中。需要 Python 3，执行脚本 `scripts/video_project.py`。若没有连接，明确报告尚未连接服务，不声称已生成。

所有写请求使用 UTF-8 JSON 文件作为 `--payload`。结构以服务 `/docs` 为准；项目必须带当前 `expected_revision`。可以调用：

```text
python scripts/video_project.py capabilities
python scripts/video_project.py list
python scripts/video_project.py new --payload request.json
python scripts/video_project.py get --project PROJECT_ID
python scripts/video_project.py compose --project PROJECT_ID --payload compose.json
python scripts/video_project.py save --project PROJECT_ID --payload plan.json
python scripts/video_project.py review --project PROJECT_ID --payload review.json
python scripts/video_project.py job --project PROJECT_ID --payload job.json
python scripts/video_project.py query --project PROJECT_ID --job JOB_ID
python scripts/video_project.py refresh --project PROJECT_ID --job JOB_ID
python scripts/video_project.py export --project PROJECT_ID --output publication.zip
```

## 确认与执行

服务端保存四个独立确认点：方案 `inputs`、声音/样片 `voice`、正式制作 `generation`、最终成片 `video`。数字人启用去停顿时还需要原片确认 `postprocess`：必须先播放当前版本最新成功数字人任务的原片，再发送 `stage: "postprocess"`、`decision: "approved"` 和该任务 `job_id`，不能跳过此步骤。重做人物视频或配音后原片确认失效。确认记录的 `quote` 是“确认原话”，与费用报价无关；必须来自本次用户实际回复或客户端实际确认操作，不得代用户编造。

先从 capabilities 读取 `cost_review_required`。默认直接生成模式不调用 `quote`、`estimate_job` 或 `price-preview`，不要求 `quote_fingerprint`、金额或预算表单。每次新的云端任务由用户实际点击生成时发送 `generation_consent: true`；服务端记录这次操作的项目版本、任务范围与意图，不能自行编造确认或将历史确认用于新任务。当前新普通视频 `scene`、`scene_batch` 发送 `expected_provider: "seedance"`，并核对 capabilities 的实际通道就绪状态。服务先本地准备并固定 provider、model、route 和参数；通道变化时重新确认，不自动回退到 RunningHub 或其他模型。后端兼容字段 `budget_grant` 在直接模式仅保存生成意图，不代表金额预算，调用方不用提供它。

配音 `voice`、数字人 `avatar`、合成 `render` 的范围都是整条项目，不要传 `scene_id`。数字人生成范围以实际配音检测得到的 `avatar_preflight.total_seconds`、`segments`、`segment_count` 为依据：先展示整条口播的真实时长及段数，再点击“生成整条数字人”。不能把一条分镜的计划秒数当成生成范围。普通分镜使用当前通道支持的 4～15 秒整数，数字人策划段为 1～14 秒，实际音频分段另行展示；规划时遵循 capabilities 的当前约束。

普通视频根据当前项目实际任务列出尚未完成的镜头，确认时固定顺序一致的 `scene_ids`，再提交 `kind: "scene_batch"`。名单不可自动扩大，成功镜头不会随批次重复制作；重做已完成镜头须单独选择 `scene` 并再次点击生成。示例：

```json
{
  "expected_revision": 3,
  "kind": "scene_batch",
  "scene_ids": ["scene-1", "scene-2"],
  "generation_consent": true,
  "expected_provider": "seedance",
  "idempotency_key": "one-stable-key-for-this-click"
}
```

同一次点击及其网络重试使用稳定 `idempotency_key`。先查询原任务；已有 provider task ID 时仅恢复原通道的查询或下载。历史 RunningHub 和 Ark 回执遵守各自已保存的供应商与生成参数，不因本机默认切换到火山 Mini 而转发或重新生成；历史批次中未提交的镜头须按本次实际通道重新确认。无法判定旧任务归属时明确暂停，不能猜测供应商。模糊提交、失败回执和费用缺失都不等于免费重做，不能自动再次提交。服务保留用量与待核账单用于故障核对，默认界面不再要求报价确认。版本变化、确认缺失或运行环境未就绪时停在对应步骤，仍可继续编辑保存。

方案统一使用可编辑文字 `persona`、`visual_style`、`layout`，平台方案保存到 `platform_variants.weixin/douyin/xiaohongshu`，每项包含 `title`、`cover_direction`、`caption`、`layout_notes`。平台导出使用这份已确认的数据，旧 `platform_versions` 仅用于兼容导入。

后处理保存在方案 `postproduction: {trim_pauses, burn_subtitles}`：普通视频默认不去停顿、烧录字幕；数字人默认两项都关闭。看过数字人原片后可改变选项。只有这两个开关变化时，服务提升版本并本地复用已成功素材，以 `reused_from_job_id` 和 `reused_from_revision` 保留来源，不重新调用生成供应商。原片去停顿确认、成片确认均需重新进行；修改文案、人像、声音或其它制作参数不得借此复用。当前任务尚未结束时不能变更后处理。

`job` 返回任务对象，需再读取项目查看完整状态。成功只以服务实际状态和可访问产物为准，失败不得描述为完成。普通视频局部重做仅作用于指定镜头；数字人始终按整条项目配音制作，不能称为“生成当前镜头”。历史任务和费用记录保留，不得将历史授权复用于新收费批次。

### 旧报价模式兼容

仅部署者显式设置 `TASK_VIDEO_COST_REVIEW_REQUIRED=1` 时才启用旧报价步骤：先取得当前范围的报价与 `quote_fingerprint`，再提交相同币种、覆盖 `reservation_amount` 的 `budget_grant`。实际币种按供应商返回值保存，可为 CNY 或 USD，不能换算或根据企业 Key、RH 币余额猜测；预留不是云端硬上限。报价缺失、指纹变化或预算不足时暂停。不要在本机默认直接生成流程中要求这些字段。

## 内容与身份

医疗内容与其他主题使用相同的普通阶段确认：用户确认当前方案并实际点击生成后，可以制作视频，不要求提交医疗资质或专业审稿证明，也不额外增加必需确认弹窗。策划时核对事实来源和结论，明确哪些资料已核验、哪些仍待核验；专业医学审稿可作为用户自选建议，不是生成、合成或导出的前提。用户的内容确认不能描述为专业医学审核，不得编造已审结论。没有专业资质时用健康知识讲解身份，不虚构医生身份。

真实人物形象、克隆声音须具备可信授权；本地文件存在不代表拥有授权。不要把 API 密钥写入提示词、项目 JSON、日志或交付包。

## 结果

提供实际预览、任务状态、可下载文件和平台版本说明。首次默认同一竖屏剪辑复用的平台包装；只有实际分别裁切/重排后才称为平台独立画面版本。服务器暂未部署时明确说本机可用、服务器待更新。
