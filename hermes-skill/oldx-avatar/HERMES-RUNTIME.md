# Hermes 受控运行副本

本目录来源于已安装的 OLDX Avatar Skill，保留原始工具及四阶段确认。Hermes 入口是 `server/app/video_runtime.py`，不能把网页传来的任意命令传给 shell。本机默认无报价流程：主题 → 方案确认 → 试听确认 → 确认生成范围并点击生成 → 成片验收。首次云端制作前简短提示一次“云端生成按服务商规则计费。”界面不要求报价、预算金额或费用复选框。

新普通视频使用火山官方 Seedance 2.0 Mini，通过北京方舟接口 `https://ark.cn-beijing.volces.com/api/v3` 调用 `doubao-seedance-2-0-mini-260615`，优先复用 Hermes 已保存的独立 Ark 视频凭证。数字人保持 OLDX RunningHub 工作流及原企业共享 Key，其底层模型不能据此称为 Seedance。策划模型复用 Hermes 配置；千问配音和识别保持 Hermes 的 DashScope 凭证。各类凭证按用途独立加载，不将策划、RunningHub 或百炼 Key 当作火山视频凭证，也不在出错后静默改通道。

## 环境配置

| 变量 | 用途 |
| --- | --- |
| `TASK_VIDEO_PAID_ENABLED` | 默认 `0`；部署者显式设为 `1` 才允许付费适配器 |
| `TASK_VIDEO_COST_REVIEW_REQUIRED` | 本机配置为 `0`，直接生成，不调用估价；`1` 仅保留旧报价流程兼容 |
| `TASK_VIDEO_SCENE_PROVIDER` | 本机新普通视频为 `seedance`；历史 RunningHub/Ark 任务遵守已保存通道，不随默认配置切换 |
| `TASK_SEEDANCE_API_KEY` / `ARK_API_KEY` | 独立的火山视频凭证；正式客户端优先从现有受控视频配置加载 `seedance_api_key`，不复制到聊天或项目 |
| `TASK_SEEDANCE_BASE_URL` / `ARK_BASE_URL` | 当前固定北京接口 `https://ark.cn-beijing.volces.com/api/v3` |
| `TASK_SEEDANCE_MODEL` / `SEEDANCE_MODEL_ID` | 当前普通视频模型 `doubao-seedance-2-0-mini-260615` |
| `TASK_VIDEO_PYTHON` | 已预装 OLDX 依赖的专用 Python 解释器绝对路径 |
| `TASK_VIDEO_SKILL_DIR` | 可选，默认本目录；部署时不依赖 Codex 目录 |
| `TASK_VIDEO_FFMPEG` | 可选，ffmpeg 二进制；否则 PATH、imageio-ffmpeg 或专用解释器查找 |
| `TASK_VIDEO_STORAGE_DIR` | Hermes 项目媒体存储，由项目 API 管理 |
| `DASHSCOPE_API_KEY` / `BL_KEY_FILE` | 百炼环境密钥或部署者配置的受控文件 |
| `RUNNINGHUB_API_KEY` / `RH_KEY_FILE` | 数字人的原 RunningHub 企业共享凭证，也供已保存的历史 RunningHub 任务续查；不作为新普通视频的回退凭证 |
| `TASK_VIDEO_VOICE_ID` | 可选的固定系统声音 ID，方案指定 `audio_plan.voice_id` 可覆盖 |
| `TASK_VIDEO_RUNNINGHUB_KEY_TYPE` | 本机企业共享类型为 `SHARED`；Key 类型不代表所有模型都使用同一计费币种 |
| `TASK_VIDEO_PRICING_PROFILE` / `TASK_VIDEO_AVATAR_CURRENCY` | 仅旧报价模式使用；历史报价按当时供应商实际原币保存，不推定币种或换汇 |
| `TASK_VIDEO_SCENE_COST_PER_SECOND` / `TASK_VIDEO_VOICE_COST_PER_1000_CHARS` / `TASK_VIDEO_AVATAR_COST_PER_SECOND` | 仅旧报价模式的手工兼容配置，默认直接生成不依赖这些值 |

非秘密配置示例：保留原安全凭证，只修改当前新普通视频的通道、模型及地址，不覆盖策划、千问或 RunningHub 配置。

```json
{
  "TASK_VIDEO_SCENE_PROVIDER": "seedance",
  "TASK_VIDEO_COST_REVIEW_REQUIRED": "0",
  "seedance_base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "seedance_model": "doubao-seedance-2-0-mini-260615"
}
```

直接生成先读取 capabilities 的 `cost_review_required=false`，使用无网络的 `prepare_job` 准备实际生成参数，不执行 `estimate_job` 或 `price-preview`。每个新的云端任务要求用户实际点击对应生成按钮，API 发送 `generation_consent=true`；当前新普通单镜及批次还要求 `expected_provider=seedance`，并核对实际能力已就绪。本地合成不强制云端生成同意。后端 `budget_grant` 名称为兼容保留，直接模式只记录用户意图、版本和固定范围，没有 `max_cost` 或币种，不是假造金额预算。

Hermes 子进程固定 `OLDX_PAID_MAX_TRIES=1`，防止配音模糊失败被自动重发。直接模式传 `OLDX_COST_REVIEW_REQUIRED=0`，配音不设置金额预算；数字人通过 `OLDX_AVATAR_GENERATION_CONSENT=1` 传递本次真实生成操作。数字人强制单路，范围、人物素材、动作和已提交回执均保留。`OLDX_AVATAR_BUDGET_JOB_ID` 的旧名称仅用于批次归属，不表示金额授权。配音仍有独立 `OLDX_VOICE_USAGE_LEDGER`，TTS、清理重认和逐词识别的用量按 call_id 最新事件聚合，保留故障核对依据；缺失金额或币种不能记作免费。

默认无自动下载依赖；专用环境预装 `numpy imageio-ffmpeg librosa opencv-python Pillow jieba` 及其依赖。Linux ffmpeg 需包含 libass，并配置支持中文的字体。

## 四阶段与实际产物

Hermes `reviews.inputs / voice / generation / video` 只接受当前方案版本的 `approved` 或 `skipped`，并保存用户真实原话。字段 `quote` 在这里是“确认原话”，与费用报价无关。适配器调用本包 `review.py present` 和 `approve/skip`，生成按文件指纹约束的 OLDX ledger；不将 `--yes` 当作用户批准。每个新云端任务的 `generation_consent` 来自真实生成操作，不自动继承为新批次的同意。

- 配音：受控 `pipeline.py voice` 产生真实 `merged.wav`、逐词时间；数字人图片在配音付费前先本地解码。
- 普通视频：准备并保存每镜固定 provider、model、route 和请求参数，写入 `snapshot._scene_execution`。新任务使用火山官方 Mini 及独立 Ark 凭证，批次必须绑定用户确认的有序 `scene_ids`，只制作缺失镜头。提交后立即保存原供应商任务号；同一次点击使用稳定幂等键，下载失败只重下。
- 数字人：准备图片、按真实配音分段、构建动作提示词、执行 RunningHub、拼接与质检。展示整条实际口播时长及段数，按钮明确生成整条项目。缺少已确认人物图或尚未完成真实配音时暂停人物生成；可使用千问系统声音，无需提供克隆样本。每次进程只等待云端一分钟，保留 task IDs，客户端继续查询；同批待完成段不会重复消耗创作确认，前段仍在云端时不继续新提交后段。
- 合成：本地 ffmpeg 统一 720×1280，保持完整配音长度，必要时循环镜头；按逐词时间生成字幕，可烧入画面；有用户音乐文件时低音量混音。三个平台目录含视频、字幕、封面、JSON 清单。

三个发布包目前使用**相同剪辑与统一竖屏布局**，清单明确 `same_edit_as_master=true`。没有制作不同平台的独立节奏剪辑，不应称为三种独立改编版本。导出包需完成 Hermes 成片审核后才可下载；不会自动发布到外部平台。

数字人方案必须在界面明确展示 `plan.avatar.image_type / hands / posture / motion`，并保存 `image_use_confirmed=true` 的真实肖像使用授权。缺少任何一项，配音或数字人付费前就会停止；不会用隐藏默认动作替代用户确认。可选 `mouth_box` 在图片复验时保留。

## 恢复与限制

普通视频首次提交前写入 submitting，保存 provider ID 后仅续查。数字人 RunningHub 在 POST 前写本地提交标记，收到 ID 后立即写回执并进入 pending ledger，Hermes 同步保存 ID。模糊 POST、失败回执或下载错误均不能触发自动重新付费生成。历史 RunningHub 和 Ark 回执继续各自原通道的查询或下载，不能随默认通道切换改派或重做；无法证明旧任务归属时暂停，不猜测供应商。历史批次未提交镜头须重新确认实际通道及范围。直接模式与旧报价模式各自保留原幂等结构，已完成的同次请求不因开关变化再提交；本地成功素材的复用保留原任务来源，不伪装成新供应商产物。

只有部署者显式开启 `TASK_VIDEO_COST_REVIEW_REQUIRED=1`，才使用旧报价、`quote_fingerprint` 和金额预算。该模式按相同 provider、model、route、参数及实际原币确认，缺失币种保留待核，不换汇或推定免费；软件预留不是云端硬上限。不要在本机默认直接生成流程中展示这些旧控件。

医疗主题按普通阶段确认处理：用户真实确认当前方案并点击生成后即可制作，不以医疗资质或专业审稿证明为生成、合成或导出条件，不额外增加必需确认。策划时核对事实来源，区分已核验和待核验的结论；专业医学审稿仅为可选建议。不得虚构医生身份、资质或已完成的审核结论，不能把用户内容确认描述为专业审稿。人物肖像与声音授权要求仍保留。

运行时未就绪、提交失败或没有真实可访问产物时，不得声称生成完成。离线测试与接口可读不证明实际云端生成可用或已结算；服务器需单独更新配置，本机启用不代表服务器已部署。
