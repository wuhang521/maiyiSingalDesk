---
name: video-prompt-director
description: Plan an ordinary AI video or an explicitly requested digital human video from a topic, including audience, persona, script, viewer-interest hooks, storyboard and platform versions. Return an editable plan for confirmation before generation.
---

# Video Prompt Director

Create a structured production plan. New ordinary scene video uses official Volcengine Seedance 2.0 Mini: Beijing Ark base `https://ark.cn-beijing.volces.com/api/v3`, model `doubao-seedance-2-0-mini-260615`, and Hermes' existing dedicated Ark video credential. Digital humans keep the OLDX RunningHub workflow and its original enterprise shared RunningHub credential. Do not claim that the OLDX workflow uses Seedance. Planning reuses the Hermes model, while Qwen narration and recognition keep Hermes' DashScope credential. Do not substitute credentials across these services or silently change providers after a failure. The plan is an editable contract between the operator, the prompt model, and the video generation API.

Only a topic is required. Infer a useful audience, one core conclusion, friendly persona, evidence needs, narration and a feasible visual treatment, then present those choices for confirmation. Default to `standard`; use `avatar` only when explicitly requested. The default platforms are `weixin`, `douyin`, `xiaohongshu`. Never invent a professional credential, verified real person's identity, licensed asset, approved voice, or completed source review.

The local default is `TASK_VIDEO_COST_REVIEW_REQUIRED=0`. Guide the user through topic → plan confirmation → narration playback confirmation → confirmation of generation scope and a Generate click → final video acceptance. Do not introduce a quotation, budget amount, pricing checkbox or price-preview request. Before the first cloud generation show only one short notice: “云端生成按服务商规则计费。” The companion execution skill records the actual click; this planning skill must not supply consent on the user's behalf.

In `avatar` mode also return an `avatar` object with `motion` (specific visible movement sequence), `hands` (initial hand position), `posture` (initial body position), and `image_type` (the configured OLDX image category, normally `D`). Describe these choices to the user before the generation review. Do not supply a made-up `image_path`: an actual selected/uploaded asset must provide it. No hidden movement defaults may be treated as user-approved instructions.

## Operating modes

Choose one target from the request and return only that target's JSON shape:

- `plan`: generate the full brief, audio plan, and shot list.
- `brief`: revise only positioning fields.
- `brief.title`: return `{ "title": "..." }`.
- `brief.audience`: return `{ "audience": "..." }`.
- `brief.core_message`: return `{ "core_message": "..." }`.
- `audio_plan`: revise only narration, voice, music, and sound direction.
- `audio_plan.narration_style`: return `{ "narration_style": "..." }`.
- `audio_plan.voice`: return `{ "voice": "..." }`.
- `audio_plan.music`: return `{ "music": "..." }`.
- `audio_plan.sound_notes`: return `{ "sound_notes": "..." }`.
- `scene`: revise one complete scene.
- `scene.narration`: return `{ "narration": "..." }`.
- `scene.visual_intent`: return `{ "visual_intent": "..." }`.
- `scene.seedance_prompt`: return `{ "seedance_prompt": "..." }`.

Never change a field named in `locked_fields`. Treat the supplied current plan as truth unless the user's instruction explicitly changes it. When an upstream edit makes downstream material stale, preserve it and list its path in `stale_fields`; do not silently rewrite it.

## Full plan schema

Return strict JSON without Markdown fences:

```json
{
  "mode": "standard",
  "brief": {
    "title": "",
    "core_message": "",
    "audience": "",
    "goal": "健康科普|品牌认知|产品介绍|活动传播",
    "tone": "",
    "total_duration": 30
  },
  "audio_plan": {
    "narration_style": "",
    "voice": "",
    "music": "",
    "sound_notes": ""
  },
  "persona": "知识讲解员，以通俗语言解释，不暗示专业执业资质。",
  "visual_style": "清晰自然的科普画面，重点使用易懂的实景或示意。",
  "layout": "9:16 竖屏，主体居中，重点文字与字幕分区，避开平台操作区。",
  "interest_hooks": [
    {"id": "hook-1", "scene_id": "scene-1", "kind": "opening", "viewer_question": "观众关心的问题", "copy": "开场问题", "visual": "与问题对应的画面", "promise": "本片实际会回答的内容", "payoff_scene_id": "scene-2", "source_refs": []}
  ],
  "sources": [],
  "claims": [],
  "platform_variants": {
    "weixin": {"title": "", "cover_direction": "", "caption": "", "layout_notes": ""},
    "douyin": {"title": "", "cover_direction": "", "caption": "", "layout_notes": ""},
    "xiaohongshu": {"title": "", "cover_direction": "", "caption": "", "layout_notes": ""}
  },
  "postproduction": {"trim_pauses": false, "burn_subtitles": true},
  "scenes": [
    {
      "id": "scene-1",
      "title": "",
      "duration": 6,
      "narration": "",
      "visual_intent": "",
      "seedance_prompt": "",
      "reference_bindings": [],
      "locked_fields": []
    }
  ],
  "stale_fields": []
}
```

Choose the number of scenes from the intended duration and content. Honor `context.scene_constraints`, including its provider, model, route, allowed durations and image roles. The current ordinary-video channel uses integer 4–15 second scenes. In `avatar` mode each planned editorial segment lasts 1–14 seconds; this is a planning estimate, not the actual provider segment count. The sum must equal `brief.total_duration`. An opening hook can occupy the first 0–3 seconds of a longer scene: never turn that editorial beat into a 3-second ordinary-video provider request. Match narration to duration using roughly 3.5–4.5 Chinese characters per second as a draft estimate. Actual digital-human generation consumes the entire narration and automatically segments the measured voice audio; show its real total duration and segments before the Generate click, rather than pretending that selecting a storyboard scene changes the digital-human scope.

Return `persona`, `visual_style`, and `layout` as editable strings. Every platform entry must contain all four string fields shown above. Do not return `platform_versions`: this old alias is accepted only during migration and normalized into `platform_variants`. Missing optional directions receive visible editable defaults from the server; they still need the user's content confirmation. `postproduction` defaults to `{ "trim_pauses": false, "burn_subtitles": true }` for ordinary video and `{ "trim_pauses": false, "burn_subtitles": false }` for digital humans. The operator may revise these switches after viewing the raw avatar footage; a change only to these switches reuses existing unmodified local media and invalidates final review, without another provider generation.

## Viewer-interest hooks and publication layout

Plan a concrete opening question in the first 0–3 seconds, a useful information turn near the middle, and a resolved takeaway at the end. Use hook kinds `opening`, `progression`, `payoff` and only as many hooks as the duration supports. Every promise must point to a later scene that actually fulfils it (a single-scene short sample can resolve within that scene). Prefer curiosity, a relatable situation or a bounded misconception; avoid frightening symptoms, guaranteed cures, inflated certainty and requests for personal medical details. Explain the hook positions in ordinary language. Give each platform a title, cover direction, caption and layout notes; use a 9:16 master with readable subtitles and space around platform controls. Treat platform layout settings as editable presets, not permanently verified platform rules.

## Confirmation and execution boundary

This skill plans content; it does not approve or execute cloud jobs. The companion `video-production` skill records four revision-bound reviews: `inputs`, `voice`, `generation`, `video`, plus `postprocess` before trimming avatar pauses. The latter must reference the latest real raw-avatar job ID and cannot be skipped. A review record's `quote` field holds the user's actual confirmation words, not a price. A changed creative plan invalidates previous reviews. Only a pure `postproduction` switch change can inherit unchanged input/voice/generation confirmations, with revision provenance; final review is always fresh.

In the default direct mode, each new cloud task records the user's real Generate click as `generation_consent: true`; new ordinary scene jobs send `expected_provider: "seedance"` after checking the active capability is ready. Do not request or invent a budget grant, amount, quote or quote fingerprint. The server locally pins provider, model, route and request parameters before execution. Ordinary `scene_batch` approval fixes the ordered list of unfinished scene IDs; it cannot silently expand or redo finished footage. Voice and avatar jobs cover the entire project, not one selected storyboard scene. Retrying one click reuses its idempotency key; an ambiguous submission resumes its original task instead of paying for another. Historical RunningHub and Ark task receipts remain with their original provider for queries and downloads; the new default does not reroute or resubmit them. Any unsubmitted remainder needs confirmation for its actual provider and scope. A genuinely new generation requires another user click. Present actual media before asking for its review; do not manufacture a preview URL or completion status.

Only an explicitly enabled legacy deployment with `TASK_VIDEO_COST_REVIEW_REQUIRED=1` uses quotation and scoped budget approval. Its quote fingerprint binds the exact provider, model, route, parameters, amount and original currency. Do not apply those legacy requirements to the default local direct workflow. Health topics use the same ordinary content confirmations as other topics; professional credentials and medical review are not prerequisites for generation, assembly or export. Preserve real-person identity and asset authorization requirements in both modes.

## Seedance prompt construction

Write one self-contained prompt per scene in this order:

1. clip purpose and continuity with the previous scene;
2. subject identity, clothing, props, and environment;
3. visible action and physical causality;
4. shot size, camera position, movement, focus, and pacing;
5. lighting, color, texture, and visual style;
6. second-by-second timing when the scene contains multiple beats;
7. reference bindings using `@图片1`, `@图片2`, etc.;
8. preservation constraints such as logo shape, product appearance, or character consistency.

Describe what should appear, not a list of abstract adjectives. Keep actions feasible within the scene duration. Do not ask Seedance to draw subtitles, paragraphs, medical values, UI labels, or dense typography; those are post-production layers. Avoid contradictory camera directions and unnecessary subject changes.

## Reference images

Use uploaded images only for their declared role:

- `first_frame`: composition and starting state;
- `last_frame`: ending composition;
- `reference_image`: character, product, prop, environment, or style reference.

Bind images in visible order and state what to inherit from each image. Apply the active channel's `context.scene_constraints` for supported roles, combinations and reference counts; historical RunningHub restrictions do not define the new Volcengine adapter. Use `reference_image` or combined first/last frames only when that active adapter explicitly supports them. Do not invent an uploaded image, silently omit an unsupported binding or change to another model to satisfy a reference role. Do not infer permission to use a real person's face. Flag real-person material for trusted-asset authorization instead of treating a local upload as sufficient consent.

## Content rules

- Check factual sources and claims when planning health content. Distinguish verified material from claims still awaiting verification; never invent medical evidence, efficacy, dosage, statistics, guarantees, a doctor's identity, or a completed review. A real user's confirmation of the current plan and Generate click permit production through the ordinary stages. Do not require medical qualifications, proof of professional review, or an additional mandatory health-content checkbox or confirmation. Professional medical review may be suggested as an optional improvement. Do not describe creative approval as professional medical review. Keep general science explanations distinct from personalized diagnosis, treatment or dosage instructions, and state the scope and source of relevant factual claims accurately.
- Put spoken words in `narration`, not inside the visual prompt unless on-screen dialogue must be generated.
- Keep narration, visual intent, and prompt semantically aligned.
- Treat voice, music, subtitles, stitching, and final publishing as downstream work. Plan them here, but do not claim they have been rendered.
- Prefer one clear communication job per scene.
