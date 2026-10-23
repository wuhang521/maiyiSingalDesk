import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronRight, Download, FileText, History, Plus, RefreshCw, Save, Settings2, Sparkles, UserRound } from "lucide-react";
import type { AppSettings } from "../../types";
import { blankContent, contentApi, contentStatus, type ContentAccount, type ContentData, type ContentEpisode, type ContentMember, type ContentSchema, type ContentSummary } from "../../lib/contentApi";
import { ContentFields, ContentPreview } from "./ContentFields";
import "./content-studio.css";

type Page = "new" | "accounts" | "episode" | "archive";
const STEP_NAMES = ["选题与价值", "观点与立场", "资料与专业核查", "内容结构", "完整初稿审改", "平台版本与定稿"];
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const message = (error: unknown) => error instanceof Error ? error.message : "操作失败，请重试。";
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

function accountExample(schema: ContentSchema): ContentData {
  return {
    ...blankContent(schema.account_fields), account_name: "账号 A", positioning: "帮助家庭医疗决策人，把家庭医疗决策中的问题想清楚。",
    core_audience: "40—60 岁的家庭医疗决策人；需要为自己与家人理解信息、比较选择、与医生沟通。",
    extension_rules: "围绕家庭医疗决策延展，说明与核心定位的关联。", prohibited_content: "不点评批评医疗体系。",
    voice_rules: "真诚、克制、有思考深度。解释专业术语，避免制造焦虑。\n正例：先把问题和适用条件说清楚。\n反例：所有人都必须这样做。",
    default_platforms: ["视频号"], format_defaults: { format: "双人对话稿", target: 180, unit: "秒", maximum: 300 },
    expertise_policy: { required: true, scope: "家庭医疗决策中的专业事实", source_requirements: "引用可核查的专业材料，由具备相应职责的专业审核人员确认适用条件。" },
  };
}

export function ContentStudio({ settings }: { settings: AppSettings }) {
  const [schema, setSchema] = useState<ContentSchema | null>(null);
  const [members, setMembers] = useState<ContentMember[]>([]);
  const [accounts, setAccounts] = useState<ContentAccount[]>([]);
  const [summaries, setSummaries] = useState<ContentSummary[]>([]);
  const [accountId, setAccountId] = useState("");
  const [page, setPage] = useState<Page>("new");
  const [episode, setEpisode] = useState<ContentEpisode | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [draft, setDraft] = useState<ContentData>({});
  const [accountDraft, setAccountDraft] = useState<ContentData>({});
  const [brief, setBrief] = useState<ContentData>({});
  const [editingBrief, setEditingBrief] = useState(false);
  const [unresolved, setUnresolved] = useState("");
  const [reviewComment, setReviewComment] = useState("");
  const [instruction, setInstruction] = useState("");
  const [changeKind, setChangeKind] = useState("content");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [previewTab, setPreviewTab] = useState("preview");
  const [historyVersion, setHistoryVersion] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [archiveFilter, setArchiveFilter] = useState("all");

  const account = accounts.find((a) => a.id === accountId);
  const activeAccounts = accounts.filter((a) => a.active_data && a.status !== "disabled");
  const current = episode?.steps[stepIndex];
  const dirty = page === "episode" && episode ? editingBrief ? !same(brief, episode.brief) : !same(draft, current?.data) || !same(unresolved.split("\n").filter((v) => v.trim()), current?.unresolved_items) :
    page === "accounts" && schema ? !same(accountDraft, account?.data || blankContent(schema.account_fields)) :
      page === "new" && schema ? !same(brief, freshBrief(schema, account)) : false;
  const sources: ContentData[] = episode ? [...(episode.account_snapshot.knowledge_collections || []), ...(episode.brief.reference_materials || [])] : [];
  const claims = stepIndex === 2 ? draft.claims || [] : episode?.steps[2].data.claims || [];
  const role = stepIndex === 5 ? "final" : stepIndex === 2 && (episode?.account_snapshot.expertise_policy?.required ||
    (draft.content_attributes || []).some((v: string) => ["健康科普", "产品事实", "其他专业内容"].includes(v)) ||
    (draft.claims || []).some((v: ContentData) => v.claim_type === "专业事实")) ? "professional" : "content";
  const reviewerId = episode?.brief.episode_reviewers?.[role];
  const reviewerName = members.find((m) => m.id === reviewerId)?.name || "未配置";
  const canReview = schema?.current_user.id === reviewerId;
  const priorApproved = episode?.steps.slice(0, stepIndex).every((s) => s.status === "approved");

  function freshBrief(s: ContentSchema, a?: ContentAccount) {
    const config = a?.active_data;
    return { ...blankContent(s.episode_fields), target_platforms: config?.default_platforms || [],
      output_format: config?.format_defaults?.format || "", length_target: config?.format_defaults?.target || 0,
      episode_reviewers: config?.reviewer_assignments || {}, delivery: "纯文案" };
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(""); setSchema(null); setEpisode(null);
    Promise.all([contentApi.schema(settings), contentApi.members(settings), contentApi.accounts(settings), contentApi.episodes(settings)])
      .then(async ([nextSchema, nextMembers, nextAccounts, nextSummaries]) => {
        if (cancelled) return;
        setSchema(nextSchema); setMembers(nextMembers); setAccounts(nextAccounts); setSummaries(nextSummaries);
        const selected = nextAccounts.find((a) => a.active_data && a.status !== "disabled") || nextAccounts[0];
        setAccountId(selected?.id || ""); setAccountDraft(clone(selected?.data || blankContent(nextSchema.account_fields)));
        setBrief(freshBrief(nextSchema, selected)); setPage(nextAccounts.length ? "new" : "accounts");
        // 未保存内容留在当前登录成员的本机会话中；服务端版本仍需正常校验。
        const key = `signaldesk.content-recovery:${settings.backendUrl}:${nextSchema.current_user.id}`;
        let saved: ContentData | null = null;
        try { saved = JSON.parse(sessionStorage.getItem(key) || "null"); } catch { /* 无恢复内容时正常打开。 */ }
        if (saved?.dirty) {
          if (saved.page === "episode" && saved.episode_id) {
            const restored = await contentApi.episode(settings, saved.episode_id);
            if (cancelled) return;
            restored.revision = saved.episode_revision;
            setEpisode(restored); setStepIndex(saved.stepIndex); setDraft(saved.draft); setUnresolved(saved.unresolved || ""); setEditingBrief(saved.editingBrief);
          }
          setAccountId(saved.accountId); setPage(saved.page); setAccountDraft(saved.accountDraft); setBrief(saved.brief);
          setReviewComment(saved.reviewComment || ""); setInstruction(saved.instruction || "");
          if (saved.page === "accounts") setAccounts(nextAccounts.map((a) => a.id === saved.accountId ? { ...a, revision: saved.account_revision } : a));
          setNotice("已恢复本机未保存修改。若其他成员更新了服务端版本，保存时会要求先核对最新内容。");
        }
      }).catch((err) => { if (!cancelled) setError(message(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [settings.backendUrl, settings.backendAuthToken, retry]);

  useEffect(() => {
    if (!schema || loading) return;
    const key = `signaldesk.content-recovery:${settings.backendUrl}:${schema.current_user.id}`;
    try {
      if (dirty) sessionStorage.setItem(key, JSON.stringify({ dirty, page, accountId, account_revision: account?.revision,
        episode_id: episode?.id, episode_revision: episode?.revision, stepIndex, draft, accountDraft, brief,
        editingBrief, unresolved, reviewComment, instruction }));
      else sessionStorage.removeItem(key);
    } catch { /* 会话存储不可用时，仍通过离开提示保护编辑内容。 */ }
    function leave(event: BeforeUnloadEvent) { if (dirty) { event.preventDefault(); event.returnValue = ""; } }
    window.addEventListener("beforeunload", leave);
    return () => window.removeEventListener("beforeunload", leave);
  }, [schema, loading, settings.backendUrl, dirty, page, accountId, account?.revision, episode?.id, episode?.revision, stepIndex, draft, accountDraft, brief, editingBrief, unresolved, reviewComment, instruction]);

  async function run(label: string, task: () => Promise<void>) {
    if (busy) return;
    setBusy(label); setError(""); setNotice("");
    try { await task(); } catch (err) { setError(message(err)); }
    finally { setBusy(""); }
  }

  function mayLeave() { return !dirty || window.confirm("当前修改还未保存。确定离开编辑区吗？"); }
  function showEpisode(next: ContentEpisode, index = next.current_step) {
    setEpisode(next); setAccountId(next.account_id); setPage("episode"); setStepIndex(index);
    setDraft(clone(next.steps[index].data)); setUnresolved(next.steps[index].unresolved_items.join("\n"));
    setBrief(clone(next.brief)); setEditingBrief(false); setReviewComment(""); setInstruction(""); setChangeKind("content"); setHistoryVersion(null);
    setSummaries((items) => [{ id: next.id, account_id: next.account_id, title: next.steps[0].data.approved_topic || next.brief.topic_brief,
      status: next.status, current_step: next.current_step, episode_number: next.episode_number, updated_at: new Date().toISOString(), assignee: next.assignee }, ...items.filter((item) => item.id !== next.id)]);
  }
  async function openEpisode(id: string) {
    if (!mayLeave()) return;
    await run("正在读取内容", async () => showEpisode(await contentApi.episode(settings, id)));
  }
  async function saveCurrent() {
    if (!episode) throw new Error("请先选择内容。");
    return contentApi.saveStep(settings, episode, stepIndex, draft, unresolved.split("\n").filter((v) => v.trim()), changeKind);
  }
  async function generate(next = episode, index = stepIndex) {
    if (!next) return;
    try { showEpisode(await contentApi.generate(settings, next, index, instruction), index); setNotice("建议已生成，请逐项审阅。新增表述已列入资料核查。"); }
    catch (err) {
      // 生成失败也会记录服务端版本，刷新后保留已保存的人工工作稿。
      try { showEpisode(await contentApi.episode(settings, next.id), index); } catch { /* 保留当前可见版本。 */ }
      throw err;
    }
  }
  async function approve() {
    if (!episode) return;
    let next = await contentApi.review(settings, episode, stepIndex, "approve", reviewComment);
    showEpisode(next);
    if (next.status === "final") { setNotice("各平台版本已确认定稿，可导出文案和完整档案。"); return; }
    const nextStep = next.current_step;
    if (!Object.keys(next.steps[nextStep].data).length) await generate(next, nextStep);
    else setNotice("已确认当前版本，请继续处理下一项待确认内容。");
  }
  async function download(format: string) {
    if (!episode) return;
    const result = await contentApi.export(settings, episode, format);
    const url = URL.createObjectURL(new Blob([result.content], { type: format === "json" ? "application/json;charset=utf-8" : "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = result.filename; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showEpisode(result.episode, 5); setNotice(`已导出：${result.filename}`);
  }

  if (loading) return <div className="creation-loading" role="status"><RefreshCw size={22} className="creation-spin" /><h2>正在打开内容创作工作台</h2><p>读取账号配置、内容档案和审核身份。</p></div>;
  if (!schema) return <div className="creation-loading"><FileText size={32} /><h2>内容创作工作台</h2><p role="alert">{error}</p><button className="secondary-button" onClick={() => setRetry((n) => n + 1)}><RefreshCw size={15} />重新连接</button></div>;

  const fieldProps = { members, settings, disabled: Boolean(busy), sources, claims,
    columns: (page === "episode" ? episode?.account_snapshot : account?.active_data)?.pillars_and_columns || [] };
  const refs = Object.fromEntries([...sources, ...claims].map((row) => [row.id, row.title || row.claim_text]));
  const visibleSummaries = summaries.filter((s) => (!accountId || s.account_id === accountId) && s.title.includes(search) && (archiveFilter === "all" || s.status === archiveFilter));
  const version = current?.history.find((v) => v.version === historyVersion);
  const oldVersion = current?.history.find((v) => v.version === (historyVersion || current.version) - 1);

  return <div className="creation-studio" aria-label="内容创作工作台">
    <aside className="creation-rail">
      <label className="creation-rail-caption" htmlFor="creation-account">当前创作账号</label>
      <select id="creation-account" value={accountId} disabled={Boolean(busy)} onChange={(e) => {
        if (!mayLeave()) return;
        const a = accounts.find((item) => item.id === e.target.value); setAccountId(a?.id || "");
        setAccountDraft(clone(a?.data || blankContent(schema.account_fields))); setBrief(freshBrief(schema, a)); setPage("new"); setError("");
      }}><option value="">全部账号</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.data.account_name || "未命名账号"}</option>)}</select>
      <button className={`creation-rail-button ${page === "new" ? "active" : ""}`} disabled={Boolean(busy)} onClick={() => {
        if (!mayLeave()) return; setBrief(freshBrief(schema, account)); setPage("new"); setError("");
      }}><Plus size={16} />新建内容</button>
      <button className={`creation-rail-button ${page === "accounts" ? "active" : ""}`} disabled={Boolean(busy)} onClick={() => {
        if (!mayLeave()) return; setAccountDraft(clone(account?.data || blankContent(schema.account_fields))); setPage("accounts"); setError("");
      }}><Settings2 size={16} />账号配置</button>
      <button className={`creation-rail-button ${page === "archive" ? "active" : ""}`} disabled={Boolean(busy)} onClick={() => { if (mayLeave()) { setPage("archive"); setError(""); } }}><History size={16} />内容档案</button>
      <div className="creation-rail-divider" />
      <span className="creation-rail-caption">最近内容</span>
      <div className="creation-recent">{summaries.filter((s) => !accountId || s.account_id === accountId).slice(0, 12).map((s) =>
        <button key={s.id} className={page === "episode" && episode?.id === s.id ? "active" : ""} disabled={Boolean(busy)} onClick={() => void openEpisode(s.id)}>
          <strong>{s.title}</strong><small>第 {s.episode_number} 期 · {s.status === "final" ? "已定稿" : STEP_NAMES[s.current_step]}</small>
        </button>)}{!summaries.length && <p className="creation-hint">创建第一期后，内容会保存在这里。</p>}</div>
      <div className="creation-identity"><UserRound size={15} /><span>{schema.current_user.name}<small>当前登录成员</small></span></div>
    </aside>

    <main className="creation-main">
      {(error || notice || busy) && <div className={`creation-notice ${error ? "error" : ""}`} role={error ? "alert" : "status"}>
        {busy && <RefreshCw size={15} className="creation-spin" />}<span>{error || busy || notice}</span>
        {!busy && <button aria-label="关闭提示" onClick={() => { setError(""); setNotice(""); }}>关闭</button>}
      </div>}

      {page === "accounts" && <>
        <header className="creation-header"><div><h2>{account ? "账号配置" : "配置第一个创作账号"}</h2><p>规则确认一次，每期自动带入。当前修改保留为独立配置版本。</p></div>
          <button className="secondary-button" disabled={Boolean(busy)} onClick={() => { if (!mayLeave()) return; setAccountId(""); setAccountDraft(blankContent(schema.account_fields)); }}><Plus size={14} />新建账号</button>
        </header>
        <div className="creation-account-status"><span className={`creation-status ${account?.status || "draft"}`}>{contentStatus[account?.status || "draft"]}</span>
          <span>{account?.active_version ? `当前生效版本 v${account.active_version}` : "尚无已生效版本"}</span>
          {!account && <button className="creation-text-button" onClick={() => setAccountDraft(accountExample(schema))}>载入账号 A 待确认示例</button>}
        </div>
        <div className="creation-scroll creation-account-editor">
          {!account && <p className="creation-help">补充真实身份、已确认立场、栏目、模板和审核人员后，才能启用账号。示例中的经历与审核人员需要你实际填写。</p>}
          <ContentFields {...fieldProps} fields={schema.account_fields.slice(0, 3)} value={accountDraft} onChange={setAccountDraft} />
          {[{ title: "身份、立场与内容范围", from: 3, to: 8 }, { title: "表达风格、模板与样稿", from: 8, to: 11 },
            { title: "发布默认值与专业审核", from: 11, to: 14 }, { title: "来源素材、审核分工与视觉模板", from: 14, to: 17 }].map((group) =>
            <details key={group.title} className="creation-section" open><summary>{group.title}</summary>
              <ContentFields {...fieldProps} fields={schema.account_fields.slice(group.from, group.to)} value={accountDraft} onChange={setAccountDraft} />
            </details>)}
          {account && account.config_history.length > 0 && <details className="creation-section"><summary>已生效配置历史 · {account.config_history.length} 个版本</summary>
            {account.config_history.map((item) => <details key={item.version} className="creation-version"><summary>v{item.version} · {item.actor.name} · {time(item.at)}</summary>
              <ContentPreview members={members} fields={schema.account_fields} value={item.data} /></details>)}
          </details>}
        </div>
        <footer className="creation-actions"><span>{dirty ? "配置有未保存修改" : "只有已生效配置可用于新一期"}</span><div>
          {account && <button className="creation-text-button" disabled={Boolean(busy)} onClick={() => void run("正在停用账号", async () => {
            const next = await contentApi.saveAccount(settings, account, accountDraft, "disable"); setAccounts((items) => items.map((a) => a.id === next.id ? next : a)); setNotice("账号已停用。");
          })}>停用账号</button>}
          {[["save", "保存草稿"], ["submit", "提交确认"], ["activate", "确认并启用"]].map(([action, label]) =>
            <button key={action} className={action === "activate" ? "primary-button" : "secondary-button"} disabled={Boolean(busy)} onClick={() => void run(`正在${label}`, async () => {
              const next = await contentApi.saveAccount(settings, account, accountDraft, action); setAccounts((items) => [...items.filter((a) => a.id !== next.id), next]);
              setAccountId(next.id); setAccountDraft(clone(next.data)); setNotice(action === "activate" ? `账号配置 v${next.active_version} 已生效，可以新建内容。` : "配置已保存。");
            })}>{action === "activate" && <Check size={15} />}{label}</button>)}
        </div></footer>
      </>}

      {page === "new" && <>
        <header className="creation-header"><div><h2>这期，想把什么问题讲清楚？</h2><p>选好账号后，只需输入主题。AI 起草每一步，由你确认后继续。</p></div></header>
        <div className="creation-new-layout creation-scroll"><section className="creation-new-form">
          <div className="creation-field"><label htmlFor="episode-account">所属账号 *</label><select id="episode-account" value={account?.active_data ? accountId : ""} disabled={Boolean(busy)} onChange={(e) => {
            const a = accounts.find((v) => v.id === e.target.value); setAccountId(e.target.value); setBrief(freshBrief(schema, a));
          }}><option value="">选择已生效的创作账号</option>{activeAccounts.map((a) => <option key={a.id} value={a.id}>{a.active_data?.account_name} · v{a.active_version}</option>)}</select></div>
          {!activeAccounts.length && <div className="creation-empty-inline"><p>先确认账号定位、栏目和审核分工，再开始第一期。</p><button className="secondary-button" onClick={() => setPage("accounts")}>去配置账号<ArrowRight size={14} /></button></div>}
          <ContentFields {...fieldProps} fields={schema.episode_fields.slice(0, 3)} value={brief} onChange={setBrief} />
          {summaries.some((s) => s.account_id === accountId && s.approved_topic) && <div className="creation-field creation-topic-pool"><label htmlFor="creation-approved-topic">或选用已确认选题</label>
            <select id="creation-approved-topic" value="" onChange={(e) => setBrief({ ...brief, topic_brief: e.target.value })}><option value="">从该账号已确认选题中选择</option>
              {summaries.filter((s) => s.account_id === accountId && s.approved_topic).map((s) => <option key={s.id} value={s.approved_topic}>第 {s.episode_number} 期 · {s.approved_topic}</option>)}</select>
          </div>}
          <details className="creation-section"><summary>补充设置 <span>受众场景、指定内容、资料与审核人员</span></summary>
            <ContentFields {...fieldProps} fields={schema.episode_fields.slice(3)} value={brief} onChange={setBrief} />
          </details>
        </section><aside className="creation-brief-preview"><h3>本期自动带入</h3>
          {account?.active_data ? <><dl><dt>核心受众</dt><dd>{account.active_data.core_audience}</dd><dt>表达风格</dt><dd>{account.active_data.voice_rules}</dd>
            <dt>默认长度</dt><dd>{account.active_data.format_defaults.target} {account.active_data.format_defaults.unit}，上限 {account.active_data.format_defaults.maximum} {account.active_data.format_defaults.unit}</dd>
            <dt>内容确认人</dt><dd>{members.find((m) => m.id === brief.episode_reviewers?.content)?.name || "未配置"}</dd></dl>
            <p className="creation-hint">本期绑定账号配置 v{account.active_version}，后续规则更新由指定人员选择是否应用。</p></> : <p>选择账号后，在这里查看带入的规则。</p>}
          <ol className="creation-workflow-overview">{STEP_NAMES.map((name) => <li key={name}>{name}</li>)}</ol>
        </aside></div>
        <footer className="creation-actions"><span>先保存本期，再生成选题建议</span><button className="primary-button" disabled={Boolean(busy) || !account?.active_data || !brief.topic_brief?.trim()} onClick={() => void run("正在生成选题建议", async () => {
          const next = await contentApi.create(settings, accountId, brief); showEpisode(next);
          setAccounts(await contentApi.accounts(settings)); await generate(next, 0);
        })}><Sparkles size={16} />生成选题建议</button></footer>
      </>}

      {page === "archive" && <>
        <header className="creation-header"><div><h2>内容档案</h2><p>回看各期版本、资料来源和实际确认记录。</p></div></header>
        <div className="creation-archive-filters"><input aria-label="搜索内容档案" placeholder="搜索主题" value={search} onChange={(e) => setSearch(e.target.value)} />
          <select aria-label="档案状态" value={archiveFilter} onChange={(e) => setArchiveFilter(e.target.value)}><option value="all">全部状态</option><option value="draft">创作中</option><option value="final">已定稿</option></select>
          <button className="secondary-button" disabled={Boolean(busy)} onClick={() => void run("正在刷新档案", async () => setSummaries(await contentApi.episodes(settings)))}><RefreshCw size={14} />刷新</button>
        </div><div className="creation-scroll"><table className="creation-archive"><thead><tr><th>内容主题</th><th>期号</th><th>当前进度</th><th>更新时间</th><th /></tr></thead>
          <tbody>{visibleSummaries.map((s) => <tr key={s.id}><td><button onClick={() => void openEpisode(s.id)}>{s.title}</button></td><td>{s.episode_number}</td><td>{s.status === "final" ? "已定稿" : STEP_NAMES[s.current_step]}</td><td>{time(s.updated_at)}</td><td><button aria-label={`打开${s.title}`} onClick={() => void openEpisode(s.id)}><ChevronRight size={16} /></button></td></tr>)}</tbody></table>
          {!visibleSummaries.length && <div className="creation-empty-inline"><FileText size={28} /><h3>还没有符合条件的内容</h3><p>新建一期，或调整搜索条件。</p></div>}
        </div>
      </>}

      {page === "episode" && episode && current && <>
        <header className="creation-header creation-episode-heading"><div><h2>{episode.steps[0].data.approved_topic || episode.brief.topic_brief}</h2><p>{episode.account_snapshot.account_name} · 第 {episode.episode_number} 期 · 配置 v{episode.account_config_version}</p></div>
          <div><button className="creation-text-button" disabled={Boolean(busy)} onClick={() => {
            if (!mayLeave()) return; setEditingBrief(!editingBrief); setBrief(clone(episode.brief));
          }}><Settings2 size={14} />{editingBrief ? "返回创作" : "本期设置"}</button>
          <button className="creation-icon" aria-label="刷新当前内容" disabled={Boolean(busy)} onClick={() => void openEpisode(episode.id)}><RefreshCw size={16} /></button></div>
        </header>
        {account && account.active_version > episode.account_config_version && <details className="creation-migration"><summary>账号有新规则可应用 · v{account.active_version}</summary>
          <p>应用后会保留旧配置和审核记录，并将已有步骤标记为待复核。</p>
          <ContentPreview members={members} fields={schema.account_fields.filter((f) => !same(account.active_data?.[f.key], episode.account_snapshot[f.key]))} value={account.active_data || {}} />
          <button className="secondary-button" disabled={Boolean(busy) || dirty} onClick={() => void run("正在应用新规则", async () => { showEpisode(await contentApi.migrate(settings, episode)); setNotice("已应用新配置，请复核受影响内容。"); })}>应用此配置并复核</button>
        </details>}
        {editingBrief ? <><div className="creation-scroll creation-account-editor"><p className="creation-help">修改本期设置后，相关内容将重新待审，原有版本保留。</p>
          <ContentFields {...fieldProps} fields={schema.episode_fields} value={brief} onChange={setBrief} />
        </div><footer className="creation-actions"><span>{dirty ? "本期设置有未保存修改" : "账号规则仍使用本期快照"}</span><button className="primary-button" disabled={Boolean(busy)} onClick={() => void run("正在保存本期设置", async () => {
          showEpisode(await contentApi.saveBrief(settings, episode, brief)); setNotice("本期设置已保存，相关步骤需要复核。");
        })}><Save size={15} />保存本期设置</button></footer></> : <>
          <nav className="creation-steps" aria-label="六步创作流程">{schema.steps.map((step, index) => <button key={step.title} aria-current={stepIndex === index ? "step" : undefined}
            className={`${stepIndex === index ? "active" : ""} ${episode.steps[index].status}`} disabled={Boolean(busy)} onClick={() => {
              if (!mayLeave()) return; showEpisode(episode, index); setPreviewTab("preview");
            }}><span>{episode.steps[index].status === "approved" ? <Check size={13} /> : index + 1}</span><div>{step.title}<small>{contentStatus[episode.steps[index].status]}</small></div></button>)}</nav>
          <div className="creation-workbench"><section className="creation-step-editor">
            <div className="creation-step-heading"><h3>{schema.steps[stepIndex].title}<span className={`creation-status ${current.status}`}>{contentStatus[current.status]} · v{current.version}</span></h3><p>{schema.steps[stepIndex].description}</p></div>
            <div className="creation-scroll creation-step-scroll">
              {!priorApproved && <p className="creation-help">前序步骤需要确认或复核。已有内容可查看和编辑，确认后才能继续。</p>}
              {current.last_error && <p role="alert" className="creation-error-inline">上次生成未完成：{current.last_error}</p>}
              {current.status === "generating" && !busy && <div className="creation-help"><p>当前步骤正在生成，请稍后刷新。已保存版本保留在版本记录中。</p>
                <button className="creation-text-button" onClick={() => void run("正在恢复工作稿", async () => showEpisode(await contentApi.recover(settings, episode, stepIndex), stepIndex))}>结束等待，保留工作稿</button></div>}
              {!Object.keys(current.data).length && !Object.keys(draft).length ? <div className="creation-empty-inline"><FileText size={26} /><h3>等待生成{schema.steps[stepIndex].title}</h3>
                <p>{priorApproved ? "AI 会结合账号配置和已确认内容给出建议。" : "请先完成前序步骤。"}</p>
                <button className="primary-button" disabled={Boolean(busy) || !priorApproved} onClick={() => void run("正在生成本步建议", async () => generate())}><Sparkles size={15} />生成本步建议</button>
                <button className="creation-text-button" disabled={Boolean(busy) || !priorApproved} onClick={() => setDraft(blankContent(schema.steps[stepIndex].fields))}>手工填写本步内容</button>
              </div> : <>
                {stepIndex === 2 && <p className="creation-help">专业核查人：{reviewerName}。保存核查结论时记录实际确认人；AI 检查提示与人工结论分别保留。</p>}
                <ContentFields {...fieldProps} disabled={Boolean(busy) || current.status === "generating"} fields={schema.steps[stepIndex].fields} value={draft} onChange={setDraft} />
                <details className="creation-section"><summary>未解决问题与修改意见</summary>
                  <div className="creation-field"><label htmlFor="creation-unresolved">未解决问题</label><textarea id="creation-unresolved" rows={3} value={unresolved} onChange={(e) => setUnresolved(e.target.value)} placeholder="每行一条；影响结论的问题未解决时不能通过" /></div>
                  <div className="creation-field"><label htmlFor="creation-instruction">本次 AI 修改要求</label><textarea id="creation-instruction" value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={3} placeholder="说明需要调整的字段、段落或表达" /></div>
                  {stepIndex >= 4 && canReview && <label className="creation-checkbox"><input type="checkbox" checked={changeKind === "copyedit"} onChange={(e) => setChangeKind(e.target.checked ? "copyedit" : "content")} />本次仅排版或错别字修正，没有改变主张与适用条件</label>}
                </details>
                <div className="creation-field"><label htmlFor="creation-review-comment">审核意见</label><textarea id="creation-review-comment" value={reviewComment} onChange={(e) => setReviewComment(e.target.value)} rows={2} placeholder="退回时必须说明修改原因；通过时可留空" /></div>
                <p className="creation-hint">本步确认人：{reviewerName}{canReview ? "（你）" : ""}。{dirty ? "请先保存修改，再确认所见版本。" : "确认将绑定当前版本。"}</p>
              </>}
            </div>
          </section><aside className="creation-preview">
            <div className="creation-preview-tabs" role="tablist" aria-label="内容辅助面板">{[["preview", "内容预览"], ["sources", "资料"], ["history", "版本与审核"]].map(([key, label]) =>
              <button role="tab" aria-selected={previewTab === key} key={key} onClick={() => setPreviewTab(key)}>{label}</button>)}</div>
            <div className="creation-scroll creation-preview-scroll">
              {previewTab === "preview" && <><div className="creation-preview-label">{dirty ? "未保存修改预览" : `${contentStatus[current.status]} · v${current.version}`}</div>
                <ContentPreview members={members} fields={schema.steps[stepIndex].fields.filter((f) => !["inline_comments", "revision_brief"].includes(f.key))} value={draft} refs={refs} />
                {!Object.keys(draft).length && <p className="creation-hint">本步结果生成后在这里预览。</p>}
                {stepIndex === 5 && <p className="creation-hint">当前为文本与版式方案预览，尚未制作实际封面图片。</p>}
              </>}
              {previewTab === "sources" && <><h3>本期可引用资料 · {sources.length}</h3>{sources.map((source) => <details className="creation-source" key={source.id} open><summary>{source.title || "未命名材料"}</summary>
                <p>{[source.kind, source.publisher, source.date, source.locator].filter(Boolean).join(" · ")}</p>
                {/^https?:\/\//i.test(source.url || "") && <a href={source.url} target="_blank" rel="noreferrer">查看原始来源</a>}
                <div className="creation-source-excerpt">{source.content || "尚未补充原文或摘录"}</div>
              </details>)}{!sources.length && <p className="creation-hint">在“本期设置”添加参考材料，或在账号配置中维护来源素材库。</p>}</>}
              {previewTab === "history" && <>
                <h3>本步版本</h3><select aria-label="查看历史版本" value={historyVersion || current.version} onChange={(e) => setHistoryVersion(Number(e.target.value))}>
                  {current.history.map((h) => <option key={h.version} value={h.version}>v{h.version} · {h.actor.name} · {time(h.at)}</option>)}
                </select>
                {(version || current.history.at(-1)) && <>
                  <p className="creation-hint">{(version || current.history.at(-1))?.reason}</p>
                  <details className="creation-section"><summary>查看此版本内容</summary><ContentPreview members={members} fields={schema.steps[stepIndex].fields} value={(version || current.history.at(-1))!.data} /></details>
                  <h3>与上一版本的变化</h3>
                  {schema.steps[stepIndex].fields.filter((f) => !same(oldVersion?.data[f.key], (version || current.history.at(-1))?.data[f.key])).map((f) => <details className="creation-diff" key={f.key}>
                    <summary>{f.label}</summary><div><strong>修改前</strong><ContentPreview members={members} fields={[f]} value={oldVersion?.data || {}} /><strong>修改后</strong><ContentPreview members={members} fields={[f]} value={(version || current.history.at(-1))!.data} /></div>
                  </details>)}
                  {version && version.version !== current.version && <button className="secondary-button" disabled={Boolean(busy)} onClick={() => { setDraft(clone(version.data)); setNotice("旧版本已载入编辑区。保存时会创建新版本，原有记录保留。"); }}>载入此版本到编辑区</button>}
                </>}
                <h3>审核记录</h3>{episode.approvals.filter((a) => a.step === stepIndex).slice().reverse().map((a, i) => <div className="creation-approval" key={i}>
                  <strong>{a.reviewer.name} · {a.decision === "approve" ? "通过" : "退回修改"} · v{a.version}</strong><small>{time(a.at)}</small>{a.comment && <p>{a.comment}</p>}
                </div>)}
                <h3>导出记录</h3>{episode.exports.map((entry, i) => <p key={i} className="creation-hint">{entry.actor.name} · {entry.format} · {time(entry.at)}</p>)}
              </>}
            </div>
          </aside></div>
          <footer className="creation-actions"><span>{dirty ? "有未保存修改" : `当前版本 v${current.version} · ${contentStatus[current.status]}`}</span><div>
            {episode.status === "final" ? <><button className="secondary-button" disabled={Boolean(busy) || dirty} onClick={() => void run("正在导出档案", async () => download("json"))}><Download size={15} />导出完整档案</button>
              <button className="primary-button" disabled={Boolean(busy) || dirty} onClick={() => void run("正在导出文案", async () => download("markdown"))}><Download size={15} />导出文案</button></> : null}
            {Object.keys(draft).length > 0 && <>
              <button className="secondary-button" disabled={Boolean(busy) || current.status === "generating"} onClick={() => void run("正在保存草稿", async () => { showEpisode(await saveCurrent(), stepIndex); setNotice("已保存新版本。请确认当前内容与相关核查结果。"); })}><Save size={15} />保存草稿</button>
              {episode.status !== "final" && <>
                <button className="secondary-button" disabled={Boolean(busy) || !priorApproved || dirty || current.status === "generating"} onClick={() => void run("AI 正在按意见修改", async () => generate())}><Sparkles size={15} />按意见修改</button>
                <button className="secondary-button" disabled={Boolean(busy) || dirty || !canReview || !current.version || current.status === "generating"} onClick={() => void run("正在退回修改", async () => {
                  showEpisode(await contentApi.review(settings, episode, stepIndex, "return", reviewComment), stepIndex); setNotice("已退回，请按审核意见修改。");
                })}><ArrowLeft size={14} />退回</button>
                <button className="primary-button" disabled={Boolean(busy) || dirty || !canReview || !priorApproved || !current.version || current.status === "approved" || current.status === "generating"} onClick={() => void run("正在确认当前版本", approve)}><Check size={15} />{stepIndex === 5 ? "确认定稿" : "通过并进入下一步"}</button>
              </>}
            </>}
          </div></footer>
        </>}
      </>}
    </main>
  </div>;
}
