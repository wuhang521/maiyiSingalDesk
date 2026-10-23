"""表单和生成共用字段规格，审核结果始终由服务端记录。"""


def field(key, label, kind="textarea", required=False, **options):
    return dict(key=key, label=label, kind=kind, required=required, **options)


PLATFORMS = ["视频号", "小红书", "抖音"]
FORMATS = ["口播文案", "双人对话稿", "旁白稿", "图文草稿"]
OBJECTIVES = ["提供实用方法", "建立专业信任", "表达人物观点", "品牌认知", "产品介绍", "活动传播"]
SOURCE_FIELDS = [
    field("title", "材料标题", "text", True),
    field("kind", "材料性质", "select", True, options=["专业来源", "真实经历", "风格参考", "演示素材"]),
    field("url", "来源链接", "text"), field("publisher", "来源 / 发布方", "text"),
    field("date", "资料日期", "text"), field("locator", "页码 / 段落位置", "text"),
    field("content", "原文 / 摘录", "material", True),
]
REVIEWERS = [field("content", "内容确认人", "reviewer", True),
             field("professional", "专业审核人", "reviewer"),
             field("final", "最终定稿人", "reviewer", True)]
ACCOUNT_FIELDS = [
    field("account_name", "账号名称", "text", True),
    field("positioning", "核心定位", required=True, hint="为谁，在什么问题上，提供什么价值"),
    field("core_audience", "核心受众", required=True, hint="记录人群、角色及典型处境"),
    field("persona_facts", "主理人 / 主体身份与经历", "records", True, fields=[
        field("fact", "身份或经历事实", required=True), field("source", "事实来源", required=True),
        field("public_scope", "允许公开范围", required=True), field("approved_expression", "批准表述", required=True)]),
    field("stance_rules", "价值立场与判断原则", "records", True, fields=[
        field("topic", "议题", "text", True), field("position", "立场", required=True),
        field("reason", "理由", required=True), field("conditions", "适用条件", required=True),
        field("exceptions", "例外", required=True)]),
    field("pillars_and_columns", "内容支柱与栏目", "records", True, fields=[
        field("name", "栏目名称", "text", True), field("question", "栏目核心问题", required=True),
        field("takeaway", "观众收获", required=True), field("formats", "可用内容形式", "multi", True, options=FORMATS)]),
    field("extension_rules", "允许延展的范围", required=True, hint="可明确填写不延展"),
    field("prohibited_content", "内容禁区", required=True, hint="可明确填写无额外限制"),
    field("voice_rules", "表达风格与正反例", required=True),
    field("writing_templates", "标题与结构模板", "records", True, fields=[
        field("name", "模板名称", "text", True), field("column", "适用栏目", "text"),
        field("structure", "标题与结构规则", required=True)]),
    field("approved_examples", "认可样稿", "records", fields=[
        field("title", "样稿标题", "text", True), field("body", "样稿正文", "material", True),
        field("reason", "具体认可原因", required=True)], hint="校准期可空，稳定运行前补齐"),
    field("default_platforms", "默认发布平台", "multi", True, options=PLATFORMS),
    field("format_defaults", "默认内容形式与长度", "object", True, fields=[
        field("format", "默认内容形式", "select", True, options=FORMATS),
        field("target", "目标长度", "number", True), field("unit", "长度单位", "select", True, options=["秒", "字"]),
        field("maximum", "硬性上限", "number", True)]),
    field("expertise_policy", "专业范围与来源要求", "object", True, fields=[
        field("required", "涉及专门领域", "boolean"), field("scope", "专业范围", required=True),
        field("source_requirements", "来源要求与审核职责", required=True)]),
    field("knowledge_collections", "来源与素材库", "records", fields=SOURCE_FIELDS),
    field("reviewer_assignments", "默认审核人员", "object", True, fields=REVIEWERS),
    field("presentation_templates", "视觉与发布模板", hint="封面 / 图文交付时填写，纯文案阶段可空"),
]
EPISODE_FIELDS = [
    field("topic_brief", "本期主题", "text", True, hint="这期想帮助谁，把什么问题想清楚？"),
    field("target_platforms", "发布平台", "multi", True, options=PLATFORMS),
    field("output_format", "内容形式", "select", True, options=FORMATS),
    field("episode_context", "本期具体受众 / 场景"),
    field("primary_objective", "主要内容目的", "select", options=OBJECTIVES),
    field("secondary_objective", "次要目的", "text"),
    field("must_include", "必须讲到的内容", "lines"),
    field("episode_exclusions", "本期避免的内容", "lines"),
    field("reference_materials", "参考资料与真实素材", "records", fields=SOURCE_FIELDS),
    field("length_target", "本期长度", "number", True),
    field("series_column_id", "系列与栏目", "column"),
    field("episode_reviewers", "本期审核人员", "object", True, fields=REVIEWERS),
    field("delivery", "交付范围", "select", True, options=["纯文案", "文案与封面方案"]),
    field("product_details", "产品资料、主张、适用范围与限制", show_if=["primary_objective", "产品介绍"], required=True),
    field("campaign_details", "活动名称、时间、地点 / 参与方式、负责人", show_if=["primary_objective", "活动传播"], required=True),
    field("uses_real_story", "涉及真实经历", "boolean"),
    field("story_details", "讲述人、核实状态、公开范围与授权状态", show_if=["uses_real_story", True], required=True),
    field("commercial_cooperation", "涉及商业合作", "boolean"),
    field("commercial_details", "合作对象、利益关系与披露内容", show_if=["commercial_cooperation", True], required=True),
]
CLAIM_FIELDS = [
    field("claim_text", "待核查表述", required=True),
    field("claim_type", "表述性质", "select", True, options=["专业事实", "一般事实", "个人经历", "价值判断", "假设演示"]),
    field("supporting_sources", "支持资料", "sources"),
    field("applicability", "适用对象与限制", required=True),
    field("ai_check_note", "AI 检查提示", "readonly"),
    field("verification_result", "人工核查结论", "select", True, options=["待核查", "支持", "需限定或改写", "不支持", "不适用"]),
    field("verification_note", "核查意见"),
    field("verified_by_at", "核查人及日期", "readonly"),
]
STEPS = [
    dict(title="选题与价值", description="确认给谁讲、为什么值得讲，以及观众能获得什么。", fields=[
        field("approved_topic", "选题表述", "text", True), field("audience_scenario", "具体受众与触发场景", required=True),
        field("core_question", "核心问题", required=True), field("primary_objective", "主要内容目的", "select", True, options=OBJECTIVES),
        field("audience_takeaway", "观众收获", required=True), field("column_fit", "所属栏目与延展理由", required=True),
        field("novelty_note", "与历史内容的区别", required=True)]),
    dict(title="观点与立场", description="明确核心观点、判断依据及适用边界。", fields=[
        field("core_position", "一句话核心观点", required=True), field("stance_basis", "立场依据与规则引用", required=True),
        field("scope_and_exceptions", "适用条件与例外", required=True), field("narrative_angle", "切入角度与叙事方式", required=True),
        field("competing_perspectives", "需要呈现的不同处境", hint="存在伦理或价值冲突时填写；无则说明不适用"),
        field("intended_response", "希望形成的理解或情绪", required=True),
        field("stance_conflict", "存在账号未覆盖的重大价值冲突", "boolean"),
        field("conflict_resolution", "指定确认人的本期决定", show_if=["stance_conflict", True], required=True)]),
    dict(title="资料与专业核查", description="逐条对应主张与依据，由实际审核人员确认。", fields=[
        field("content_attributes", "内容属性", "multi", True, options=["普通价值讨论", "健康科普", "产品事实", "真实经历", "其他专业内容"]),
        field("professional_scope", "需专业审核的范围", required=True),
        field("claims", "主张核查表", "records", True, fields=CLAIM_FIELDS),
        field("authenticity_tasks", "真实性 / 授权待办", "lines")]),
    dict(title="内容结构", description="把已认可的观点和材料组织成可写作的大纲。", fields=[
        field("opening", "开头方案", required=True),
        field("outline_sections", "分段大纲", "records", True, fields=[
            field("purpose", "段落用途", "text", True), field("points", "内容要点", required=True),
            field("claim_refs", "主张引用", "claims"), field("source_refs", "素材 / 来源引用", "sources"),
            field("length", "预计长度", "number", True)]),
        field("deeper_insight", "深一层的解释", required=True), field("ending_takeaway", "结尾交付", required=True),
        field("engagement_prompt", "互动引导"), field("estimated_length", "总长度估算", "readonly")]),
    dict(title="完整初稿审改", description="直接修改正文，或通过段落批注让 AI 定向调整。", fields=[
        field("draft_body", "正文 / 完整脚本", "richtext", True, hint="对话稿标明角色，图文稿用分页分隔线区分页面"),
        field("visual_notes", "简要画面提示"),
        field("inline_comments", "段落批注", "records", fields=[field("quote", "引用原文", required=True), field("comment", "修改意见", required=True)]),
        field("revision_brief", "全局修改要求"), field("editorial_checks", "初稿检查结果", "readonly"),
        field("editorial_confirmed", "已人工检查立场、事实、标题承诺、重复、口语和长度", "boolean", True)]),
    dict(title="平台版本与定稿", description="确认每个平台的标题、正文和包装，再导出交付。", fields=[
        field("platform_variants", "平台版本", "records", True, fields=[
            field("platform", "平台", "select", True, options=PLATFORMS), field("final_title", "最终标题", "text", True),
            field("final_body", "平台正文 / 脚本", "richtext", True), field("shared_body", "与主稿共用正文", "boolean"),
            field("summary", "简介 / 摘要"), field("cover", "封面文字与版式"),
            field("metadata", "标签与置顶评论"), field("public_notes", "资料出处与必要说明"),
            field("confirmed", "已审核本平台标题、正文及包装", "boolean", True)])]),
]


def blank(fields):
    return {f["key"]: ([] if f["kind"] in ("records", "multi", "lines", "sources", "claims") else
                       blank(f["fields"]) if f["kind"] == "object" else
                       False if f["kind"] == "boolean" else 0 if f["kind"] in ("number", "reviewer") else "")
            for f in fields}


def missing_fields(fields, data, prefix=""):
    problems = []
    for f in fields:
        if f.get("show_if") and data.get(f["show_if"][0]) != f["show_if"][1]:
            continue
        value = data.get(f["key"])
        label = prefix + f["label"]
        if f["required"] and (not value or isinstance(value, str) and not value.strip()):
            problems.append(f"请填写或确认：{label}")
        if f["kind"] == "object" and isinstance(value, dict):
            problems.extend(missing_fields(f["fields"], value, label + " / "))
        if f["kind"] == "records" and isinstance(value, list):
            for index, row in enumerate(value):
                if isinstance(row, dict):
                    problems.extend(missing_fields(f["fields"], row, f"{label} {index + 1} / "))
    return problems
