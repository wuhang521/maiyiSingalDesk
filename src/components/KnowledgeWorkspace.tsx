import { useEffect, useRef, useState } from "react";
import { BookOpen, FileText, Link2, LockKeyhole, Plus, RefreshCw, Search, Upload } from "lucide-react";
import type { AppSettings } from "../types";
import { knowledgeApi, knowledgeKind, knowledgeStatus, type KnowledgeAnswer, type KnowledgeCapabilities, type KnowledgeDetail, type KnowledgeInput, type KnowledgeItem } from "../lib/knowledgeApi";
import "./knowledge-workspace.css";
import { KnowledgeHistory } from "./KnowledgeHistory";
import { KnowledgeOcr } from "./KnowledgeOcr";
import { KnowledgeAsr } from "./KnowledgeAsr";
import { KnowledgeOrganization } from "./KnowledgeOrganization";
import { KnowledgeVideoOcr } from "./KnowledgeVideoOcr";
import { KnowledgeUsage } from "./KnowledgeUsage";
import { KnowledgeLinkImport, type LinkImportResult } from "./KnowledgeLinkImport";
import { KnowledgeOperations } from "./KnowledgeOperations";
import { KnowledgeSemantic } from "./KnowledgeSemantic";
import { KnowledgeTeams } from "./KnowledgeTeams";
import { KnowledgeShare } from "./KnowledgeShare";
import { KnowledgeNotifications } from "./KnowledgeNotifications";
import { KnowledgeMediaMerge } from "./KnowledgeMediaMerge";
import { KnowledgeFolderWatch } from "./KnowledgeFolderWatch";
import { KnowledgeSemanticBatch } from "./KnowledgeSemanticBatch";
import { KnowledgeCosts } from "./KnowledgeCosts";

const blank = (): KnowledgeInput => ({ title: "", source_url: "", publisher: "", tags: [], content: "" });
const accepted = ".jpg,.jpeg,.png,.txt,.md,.html,.htm,.pdf,.srt,.vtt,.mp4,.mov,.mkv,.webm,.mp3,.m4a,.wav,.aac,.ogg,.silk";
const waiting = new Set(["received", "extracting", "retry_wait"]);
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "操作失败，请稍后重试。";
const draftValue = (value: KnowledgeInput) => JSON.stringify([value.title, value.source_url, value.publisher,
  value.tags.map((tag) => tag.trim()).filter(Boolean), value.content]);
function date(value: string) { return new Date(value.endsWith("Z") ? value : `${value}Z`).toLocaleDateString("zh-CN"); }

export function KnowledgeWorkspace({ settings, canManage = false }: { settings: AppSettings; canManage?: boolean }) {
  const [tab, setTab] = useState("active");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeDetail | null>(null);
  const [caps, setCaps] = useState<KnowledgeCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tick, setTick] = useState(0);
  const [form, setForm] = useState<"new" | "edit" | null>(null);
  const [draft, setDraft] = useState<KnowledgeInput>(blank);
  const [editingSnapshot, setEditingSnapshot] = useState<KnowledgeDetail | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<KnowledgeAnswer | null>(null);
  const [cloud, setCloud] = useState(false);
  const [semantic, setSemantic] = useState(false);
  const [purging, setPurging] = useState(false);
  const [purgeConfirmation, setPurgeConfirmation] = useState("");
  const [batchProgress, setBatchProgress] = useState("");
  const [batchResults, setBatchResults] = useState<{ filename: string; saved: boolean; note: string }[]>([]);
  const stopBatch = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement | null>(null);
  const supplementInput = useRef<HTMLInputElement>(null);
  const detailEpoch = useRef(0);
  const sessionEpoch = useRef(0);
  const refresh = () => setTick((n) => n + 1);
  const selectedListItem = items.find((item) => item.id === selected);
  const draftDirty = Boolean(form) && draftValue(draft) !== draftValue(form === "edit" && editingSnapshot ? editingSnapshot : blank());

  useEffect(() => {
    if (!draftDirty) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [draftDirty]);

  useEffect(() => {
    let live = true;
    ++sessionEpoch.current;
    setCaps(null); setCloud(false); setAnswer(null); setQuestion("");
    setSemantic(false);
    stopBatch.current = true;
    setBatchProgress(""); setBatchResults([]); setNotice(""); setError("");
    setItems([]); setTotal(0); setSelected(null); setDetail(null); setForm(null); setDraft(blank()); setEditingSnapshot(null);
    knowledgeApi.capabilities(settings).then((result) => { if (live) setCaps(result); }).catch((e) => { if (live) setError(errorMessage(e)); });
    return () => { live = false; };
  }, [settings.backendUrl, settings.backendAuthToken]);

  useEffect(() => {
    if (tab === "ask" || tab === "teams" || busy) return;
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      knowledgeApi.items(settings, query, tab, offset, controller.signal).then((result) => {
        if (offset && offset >= result.total) { setOffset(Math.floor(Math.max(0, result.total - 1) / 50) * 50); return; }
        setItems(result.items); setTotal(result.total); setLoading(false);
        setSelected((id) => result.items.some((item) => item.id === id) ? id : result.items[0]?.id || null);
      }).catch((e) => { if (!controller.signal.aborted) { setError(errorMessage(e)); setLoading(false); } });
    }, query ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [settings.backendUrl, settings.backendAuthToken, query, tab, offset, tick, busy]);

  useEffect(() => {
    const epoch = ++detailEpoch.current;
    if (!selected || tab === "ask" || tab === "teams") { setDetail(null); return; }
    const controller = new AbortController();
    setDetail((current) => current?.id === selected ? current : null);
    knowledgeApi.detail(settings, selected, controller.signal).then((result) => {
      if (epoch === detailEpoch.current) {
        setDetail(result);
        // Detail can finish ahead of the independently polled library list.
        // Never keep a stale "waiting" row beside a terminal OCR result.
        setItems((current) => current.map((row) => row.id === result.id &&
          result.revision >= row.revision && result.updated_at >= row.updated_at
          ? { ...row, ...result } : row));
        if (!waiting.has(result.status)) setNotice((current) => /^(已提交云端(?: OCR|转写|画面 OCR)|文件已保存，等待识别)/.test(current) ? "" : current);
      }
    }).catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [selected, tick, tab, settings.backendUrl, settings.backendAuthToken, selectedListItem?.status, selectedListItem?.revision, selectedListItem?.updated_at]);

  useEffect(() => { setPurging(false); setPurgeConfirmation(""); }, [selected, tab]);

  useEffect(() => {
    if (tab === "teams" || tab === "ask" || form || busy || !items.some((item) => waiting.has(item.status))) return;
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, [items, form, busy, tab]);

  async function operate(action: () => Promise<void>) {
    const epoch = sessionEpoch.current;
    ++detailEpoch.current;
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { if (epoch === sessionEpoch.current) setError(errorMessage(e)); } finally { setBusy(false); }
  }

  function chooseTab(value: string) {
    if (busy) return;
    if (form && !window.confirm("离开将放弃尚未保存的填写内容，继续吗？")) return;
    setTab(value); setOffset(0); setQuery(""); setForm(null); setError(""); setNotice("");
    // Evidence may become stale after edits/removal: never retain answers across tabs.
    setAnswer(null);
    setCloud(false);
    setSemantic(false);
  }

  async function save() {
    const epoch = sessionEpoch.current;
    await operate(async () => {
      const result = form === "edit" && editingSnapshot ? await knowledgeApi.update(settings, editingSnapshot, draft) : await knowledgeApi.collect(settings, draft);
      if (epoch !== sessionEpoch.current) return;
      setForm(null); setDetail(result); setSelected(result.id); setTab("active"); setQuery(""); setOffset(0);
      setNotice(result.status === "ready" ? "正文已入库，可搜索。" : "资料已保存，请查看识别状态。输入的同一链接会自动去重。");
      refresh();
    });
  }

  async function upload(file: File, existing?: KnowledgeDetail) {
    const epoch = sessionEpoch.current;
    await operate(async () => {
      const maximum = /\.(txt|md|srt|vtt|html?|pdf|jpe?g|png)$/i.test(file.name) ? Math.min(caps?.max_upload_mb || 500, 20) : caps?.max_upload_mb || 500;
      if (!file.size || file.size > maximum * 1024 * 1024) throw new Error(`文件不能为空，且不能超过 ${maximum} MB。`);
      let item = existing;
      if (!item) item = await knowledgeApi.collect(settings, { ...blank(), title: file.name });
      if (epoch !== sessionEpoch.current) return;
      // Keep the placeholder reachable if the transfer fails.
      setSelected(item.id); setDetail(item); setTab("active"); setQuery(""); setOffset(0); refresh();
      const result = await knowledgeApi.upload(settings, item, file, maximum);
      if (epoch !== sessionEpoch.current) return;
      setSelected(result.id); setDetail(result); setForm(null); setNotice("文件已保存，等待识别。可在下方查看处理状态。"); refresh();
    });
  }

  async function importLinks(links: string[], stopped: () => boolean) {
    const epoch = sessionEpoch.current;
    const results: LinkImportResult[] = [];
    await operate(async () => {
      for (const url of links) {
        if (stopped() || epoch !== sessionEpoch.current) break;
        try {
          const item = await knowledgeApi.collect(settings, { ...blank(), source_url: url });
          if (epoch !== sessionEpoch.current) break;
          results.push({ url, saved: true, note: `已保存 / 匹配已有资料；${knowledgeStatus[item.status] || item.status}。${item.status_note}` });
        } catch (error) {
          if (epoch !== sessionEpoch.current) break;
          results.push({ url, saved: false, note: errorMessage(error) + " 请先核对列表，未自动重试。" });
        }
      }
      if (epoch === sessionEpoch.current) { setNotice(`本批已确认保存 ${results.filter((row) => row.saved).length} 个链接，请查看资料识别状态。`); refresh(); }
    });
    return results;
  }

  async function uploadBatch(files: File[]) {
    if (files.length === 1) { await upload(files[0]); return; }
    if (!files.length) return;
    if (files.length > 20) { setError("每批最多选择 20 个文件；没有开始上传，请减少文件数量。"); return; }
    stopBatch.current = false; setBatchResults([]);
    const epoch = sessionEpoch.current;
    const results: { filename: string; saved: boolean; note: string }[] = [];
    await operate(async () => {
      for (const [index, file] of files.entries()) {
        if (stopBatch.current || epoch !== sessionEpoch.current) break;
        setBatchProgress(`正在保存 ${index + 1} / ${files.length}：${file.name}`);
        try {
          if (!accepted.split(",").some((suffix) => file.name.toLowerCase().endsWith(suffix))) throw new Error("文件格式不支持，未上传。");
          const maximum = /\.(txt|md|srt|vtt|html?|pdf|jpe?g|png)$/i.test(file.name) ? Math.min(caps?.max_upload_mb || 500, 20) : caps?.max_upload_mb || 500;
          if (!file.size || file.size > maximum * 1024 * 1024) throw new Error(`文件不能为空，且不能超过 ${maximum} MB；未上传。`);
          const item = await knowledgeApi.collect(settings, { ...blank(), title: file.name });
          if (epoch !== sessionEpoch.current) break;
          setSelected(item.id); setDetail(item); setTab("active"); setQuery(""); setOffset(0); setForm(null);
          const result = await knowledgeApi.upload(settings, item, file, maximum);
          if (epoch !== sessionEpoch.current) break;
          setSelected(result.id); setDetail(result);
          results.push({ filename: file.name, saved: true, note: "原件已保存；不等于识别完成，不自动发起云端识别。" });
        } catch (error) {
          if (epoch !== sessionEpoch.current) break;
          results.push({ filename: file.name, saved: false, note: errorMessage(error) + " 未自动重试；如已创建资料占位，可到列表补充。" });
        }
        setBatchResults([...results]);
      }
      if (epoch !== sessionEpoch.current) return;
      setBatchProgress("");
      const saved = results.filter((result) => result.saved).length;
      setNotice(`本批已保存 ${saved} 个文件，${results.length - saved} 个未确认保存，${files.length - results.length} 个未开始。识别状态请到资料详情查看。`);
      refresh();
    });
  }

  function openEdit() {
    if (!detail) return;
    setDraft({ title: detail.title, source_url: detail.source_url, publisher: detail.publisher, tags: detail.tags, content: detail.content });
    setEditingSnapshot(detail);
    setForm("edit"); setError("");
  }

  function confirmOcr() {
    if (!detail) return;
    const epoch = sessionEpoch.current;
    void operate(async () => {
      const result = await (detail.method === "volc_asr_draft" ? knowledgeApi.confirmAsr(settings, detail) : detail.method === "video_ocr_draft" ? knowledgeApi.confirmVideoOcr(settings, detail) : knowledgeApi.confirmOcr(settings, detail));
      if (epoch !== sessionEpoch.current) return;
      setDetail(result); setNotice("已按你的核对确认入库，Hermes 可检索引用。"); refresh();
    });
  }

  return <section className="knowledge-workspace" aria-label="个人知识库">
    <div className="kb-toolbar">
      <div className="kb-scope"><LockKeyhole size={16} /><strong>{tab === "teams" ? "团队知识库" : "我的知识库"}</strong><span>{tab === "teams" ? "仅主动共享的当前正文" : "仅本人可见"}</span></div>
      {tab !== "teams" && <div className="kb-actions">
        <button className="secondary-button" disabled={busy || !!form} onClick={() => fileInput.current?.click()}><Upload size={15} />上传文件</button>
        <button className="secondary-button" disabled={busy || !!form} onClick={() => folderInput.current?.click()}>导入文件夹</button>
        <button className="primary-button" disabled={busy || !!form} onClick={() => { setDraft(blank()); setForm("new"); setError(""); }}><Plus size={15} />收集资料</button>
      </div>}
      <input ref={fileInput} aria-label="上传知识文件" type="file" accept={accepted} multiple hidden onChange={(event) => { const files = Array.from(event.target.files || []); if (files.length) void uploadBatch(files); event.target.value = ""; }} />
      <input ref={(node) => { folderInput.current = node; node?.setAttribute("webkitdirectory", ""); }} aria-label="导入知识文件夹" type="file" multiple hidden onChange={(event) => {
        const files = Array.from(event.target.files || []); event.target.value = "";
        if (files.length > 20) { setError("文件夹包含超过 20 个文件，未开始上传。请选择较小文件夹或用“上传文件”分批选择。"); return; }
        if (files.length && window.confirm(`将选定文件夹内的 ${files.length} 个文件逐个上传到本人知识库？包含子文件夹，不上传本机目录路径，不监控后续变化。云端识别需另行同意。`)) void uploadBatch(files);
      }} />
      <input ref={supplementInput} aria-label="补充知识文件" type="file" accept={accepted} hidden onChange={(event) => { const file = event.target.files?.[0]; if (file && detail) void upload(file, detail); event.target.value = ""; }} />
    </div>
    <div className="kb-auxiliary-tools">
      {tab !== "teams" && <KnowledgeFolderWatch key={`folder-watch-${settings.backendUrl}-${settings.backendAuthToken}`} disabled={busy || !!form} onImport={() => fileInput.current?.click()} />}
      {caps?.semantic_enabled && tab === "active" && !form && <KnowledgeSemanticBatch key={`semantic-batch-${settings.backendUrl}-${settings.backendAuthToken}`} settings={settings} items={items} disabled={busy} onBusy={setBusy} />}
      {caps?.costs_available && <KnowledgeCosts key={`costs-${settings.backendUrl}-${settings.backendAuthToken}`} settings={settings} canManage={canManage} disabled={busy} />}
      {caps?.notifications_available && <KnowledgeNotifications key={`notifications-${settings.backendUrl}-${settings.backendAuthToken}`} settings={settings} canManage={canManage} disabled={busy} />}
      {caps?.usage_available && <KnowledgeUsage key={`usage-${settings.backendUrl}-${settings.backendAuthToken}`} settings={settings} busy={busy} />}
      {tab !== "teams" && <KnowledgeLinkImport key={`links-${settings.backendUrl}-${settings.backendAuthToken}`} disabled={busy || !!form} onImport={importLinks} />}
      {canManage && caps?.usage_available && <KnowledgeOperations key={`operations-${settings.backendUrl}-${settings.backendAuthToken}`} settings={settings} busy={busy} />}
    </div>
    {batchProgress && <div className="kb-notice" role="status"><span>{batchProgress}</span> <button className="secondary-button" onClick={() => { stopBatch.current = true; setBatchProgress("将在当前文件保存结束后停止，不删除已经保存的资料。"); }}>停止后续上传</button></div>}
    {!!batchResults.length && <details className="kb-notice"><summary>查看本批文件保存结果 · {batchResults.length} 个</summary><ul>{batchResults.map((result, index) => <li key={index}>{result.filename}：{result.note}</li>)}</ul></details>}
    <div className="kb-tabs" role="tablist" aria-label="知识库视图">
      {[["active", "全部资料"], ["inbox", "待处理"], ["ask", "知识问答"], ...(caps?.teams_available ? [["teams", "团队知识库"]] : []), ["removed", "知识回收站"]].map(([value, label]) =>
        <button key={value} id={`kb-tab-${value}`} role="tab" aria-controls="kb-panel" tabIndex={tab === value ? 0 : -1} aria-selected={tab === value} disabled={busy} onClick={() => chooseTab(value)}
          onKeyDown={(event) => {
            const values = ["active", "inbox", "ask", ...(caps?.teams_available ? ["teams"] : []), "removed"];
            const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
            if (!delta && event.key !== "Home" && event.key !== "End") return;
            event.preventDefault();
            const next = event.key === "Home" ? "active" : event.key === "End" ? "removed" : values[(values.indexOf(tab) + delta + values.length) % values.length];
            chooseTab(next); document.getElementById(`kb-tab-${next}`)?.focus();
          }}>{label}</button>)}
    </div>
    {error && <div className="kb-error" role="alert">{error}<button type="button" className="secondary-button" disabled={busy} onClick={() => { setError(""); if (tab === "ask" && cloud) { setCloud(false); setAnswer(null); } else refresh(); }}>{tab === "ask" && cloud ? "改用原文摘录" : "刷新重试"}</button></div>}
    {notice && <p className="kb-notice" role="status">{notice}</p>}
    <div id="kb-panel" className="kb-panel" role="tabpanel" aria-labelledby={`kb-tab-${tab}`}>
    {tab === "teams" ? <KnowledgeTeams key={`teams-${settings.backendUrl}-${settings.backendAuthToken}`} settings={settings} disabled={busy} onBusy={setBusy} /> : form ? <form className="kb-collect" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <h2>{form === "edit" ? "补充或修订正文" : "收集公众号、视频号与笔记"}</h2>
      <p>公众号可尝试提取正文；视频号先保存链接，再上传视频、字幕或补充文字。不会绕过微信验证。</p>
      <fieldset disabled={busy}>
      <div className="kb-form-row"><label>资料标题{form === "new" && "（选填）"}<input required={form === "edit"} maxLength={300} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
        <label>来源 / 公众号名称（选填）<input maxLength={200} value={draft.publisher} onChange={(e) => setDraft({ ...draft, publisher: e.target.value })} /></label></div>
      <label>微信链接{form === "edit" ? "（保留原始来源）" : "（选填）"}<input type="url" readOnly={form === "edit"} maxLength={4000} placeholder="https://mp.weixin.qq.com/s/… 或视频号分享链接" value={draft.source_url} onChange={(e) => setDraft({ ...draft, source_url: e.target.value })} /></label>
      <label>正文 / 转写文字{form === "new" && "（选填）"}<textarea required={form === "edit"} maxLength={1000000} rows={10} placeholder="粘贴文章正文、视频转写或自己的笔记；保存后即可搜索。" value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} /></label>
      <label>标签（用中文或英文逗号分隔）<input value={draft.tags.join("，")} onChange={(e) => setDraft({ ...draft, tags: e.target.value.split(/[,，]/) })} /></label>
      <div className="kb-actions"><button className="secondary-button" type="button" disabled={busy} onClick={() => { if (window.confirm("放弃尚未保存的填写内容？")) setForm(null); }}>取消</button><button className="primary-button" disabled={busy}>{busy ? "正在保存…" : form === "edit" ? "保存修订" : "保存到知识库"}</button></div>
      </fieldset>
    </form> : tab === "ask" ? <div className="kb-ask">
      <h2>从收藏的资料里找依据</h2><p>默认采用关键词检索与原文摘录，云端 AI 综合可选。只有已入库的正文参与问答。</p>
      <div className="kb-cloud-choice">
        <label><input type="checkbox" checked={cloud} disabled={busy || !caps?.cloud_qa_enabled} aria-describedby="kb-cloud-disclosure" onChange={(e) => { setCloud(e.target.checked); setSemantic(false); setAnswer(null); setError(""); }} />使用云端 AI 综合（可选）</label>
        <p id="kb-cloud-disclosure">{caps?.cloud_qa_enabled ? `勾选后提交，将向${caps.cloud_provider || "后台配置的云服务"}发送本次问题及最多 5 段相关资料文字，可能产生费用；不会发送整库或原始文件。` : "后台尚未启用云端问答或缺少凭据，请联系管理员；原文摘录仍可使用。"}</p>
      </div>
      {caps?.semantic_enabled !== undefined && <div className="kb-cloud-choice">
        <label><input type="checkbox" checked={semantic} disabled={busy || !caps.semantic_enabled} aria-describedby="kb-semantic-query-disclosure" onChange={(event) => { setSemantic(event.target.checked); setCloud(false); setAnswer(null); setError(""); }} />使用语义原文检索（可选）</label>
        <p id="kb-semantic-query-disclosure">{caps.semantic_enabled ? "勾选后提交，把本次问题发送给火山文本向量模型，可能计费并占用一次提交额度。只检索本人已经主动建立索引的当前正文，在后台匹配，不发送整库，不生成结论；与 AI 综合二选一。" : "后台尚未启用语义检索或缺少专用向量模型。默认关键词检索不受影响。"}</p>
      </div>}
      <form onSubmit={(e) => { e.preventDefault(); const epoch = sessionEpoch.current; void operate(async () => { setAnswer(null); const result = semantic ? await knowledgeApi.semanticSearch(settings, question.trim()) : await knowledgeApi.ask(settings, question.trim(), cloud); if (epoch === sessionEpoch.current) setAnswer(result); }); }}>
        <label>问题或关键词<input minLength={2} maxLength={500} required disabled={busy} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="例如：健康管理、内容运营" /></label>
        <button className="primary-button" disabled={busy}>{busy ? cloud ? "正在云端综合…" : "正在查找…" : semantic ? "发送问题并语义检索" : cloud ? "发送片段并综合" : "查找依据"}</button>
      </form>
      {answer && <div className="kb-answer" aria-live="polite"><h3>{answer.mode === "cloud" ? "AI 综合 · 请核对出处" : answer.mode === "semantic" ? "语义检索 · 原文片段" : "资料摘录"}</h3>{answer.mode === "cloud" && <p className="kb-ai-warning">引用编号已校验，但模型结论仍可能有误；不替代专业判断或医疗建议。</p>}<div className="kb-source-text">{answer.answer}</div>
        {!!answer.citations.length && <div className="kb-citations"><h3>出处</h3>{answer.citations.map((citation) => <button className="kb-citation" key={citation.chunk_id} onClick={() => { setSelected(citation.item_id); setTab("active"); setQuery(citation.title.slice(0, 200)); setOffset(0); setAnswer(null); refresh(); }}>
          <span>[{citation.index}] {citation.title}</span><small>{citation.locator} · 版本 {citation.revision}</small></button>)}</div>}
      </div>}
    </div> : <div className="kb-columns">
      <div className="kb-library">
        <div className="kb-list-tools"><label className="kb-search"><Search size={16} /><input aria-label="搜索知识资料" value={query} onChange={(e) => { setQuery(e.target.value); setOffset(0); }} placeholder="搜索标题、正文、来源或标签" /></label><button className="icon-button" aria-label="刷新知识库" disabled={busy} onClick={refresh}><RefreshCw size={16} className={loading ? "spin" : ""} /></button></div>
        <div className="kb-list-summary"><span>{tab === "inbox" ? "等待识别或补充的资料" : tab === "removed" ? caps?.trash_retention_days ? `保留 ${caps.trash_retention_days} 天，之后自动永久删除` : "可恢复；永久删除后不可找回" : "保留来源，积累可用知识"}</span><span>{total} 条</span></div>
        <div className="kb-list" aria-busy={loading}>
          {loading && !items.length ? <p className="kb-empty">正在读取知识库…</p> : !items.length ? <div className="kb-empty"><BookOpen size={32} /><h3>{query ? "没有匹配资料" : tab === "inbox" ? "没有待处理资料" : tab === "removed" ? "回收站为空" : "把有价值的内容留下来"}</h3><p>{query ? "换一个关键词，或清空搜索查看全部。" : "点击“收集资料”保存链接或正文，也可以上传文件。"}</p></div> : items.map((item) =>
            <button key={item.id} className={`kb-list-item ${selected === item.id ? "selected" : ""}`} disabled={busy} onClick={() => { setSelected(item.id); setError(""); }} aria-pressed={selected === item.id}>
              <div className="kb-item-meta"><span>{knowledgeKind[item.kind] || "资料"}</span><span className={`kb-state ${item.deleted_at ? "removed" : item.status === "ready" ? "ready" : "pending"}`}>{item.deleted_at ? "已移除" : knowledgeStatus[item.status] || item.status}</span></div>
              <strong>{item.title}</strong><p>{item.summary || item.status_note}</p><small>{item.publisher || "来源待补充"} · {date(item.updated_at)}</small>
            </button>)}
        </div>
        {total > 50 && <div className="kb-pagination"><button className="secondary-button" disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button><span>{Math.floor(offset / 50) + 1} / {Math.ceil(total / 50)}</span><button className="secondary-button" disabled={offset + 50 >= total || busy} onClick={() => setOffset(offset + 50)}>下一页</button></div>}
      </div>
      <article className="kb-detail" aria-label="知识资料详情">
        {!detail ? <div className="kb-empty"><FileText size={32} /><h3>{selected ? "正在读取资料…" : "选择一份资料"}</h3><p>正文、原始文件与识别状态会显示在这里。</p></div> : <>
          <div className="kb-detail-heading"><span className={`kb-state ${detail.status === "ready" ? "ready" : "pending"}`}>{detail.deleted_at ? "已移入回收站" : knowledgeStatus[detail.status] || detail.status}</span><h2>{detail.title}</h2><p>{detail.publisher || "来源待补充"} · 版本 {detail.revision} · {date(detail.updated_at)}</p></div>
          {detail.source_url && <a className="kb-source-link" href={detail.source_url} target="_blank" rel="noreferrer"><Link2 size={14} />查看原始微信链接</a>}
          {detail.tags.length > 0 && <p className="kb-tags">{detail.tags.join(" · ")}</p>}
          <p className={`kb-processing ${detail.status === "ready" ? "complete" : ""}`} role="status">{detail.status_note}</p>
          <div className="kb-actions kb-detail-actions">
            {detail.deleted_at ? <><button className="primary-button" disabled={busy || purging} onClick={() => void operate(async () => { await knowledgeApi.restore(settings, detail); setNotice("资料已恢复。"); refresh(); })}>恢复资料</button>
              <button className="kb-remove" disabled={busy || purging} onClick={() => { setPurging(true); setPurgeConfirmation(""); }}>永久删除…</button></> : <>
              <button className="secondary-button" disabled={busy} onClick={openEdit}>补充 / 修订正文</button>
              <button className="secondary-button" disabled={busy} onClick={() => supplementInput.current?.click()}>补充文件</button>
              {!waiting.has(detail.status) && !/^(doubao_ocr|volc_asr|video_ocr)/.test(detail.method) && (!detail.assets.length || detail.assets.some((a) => !/\.(jpe?g|png)$/i.test(a.filename))) && <button className="secondary-button" disabled={busy || (!detail.assets.length && detail.kind !== "article")} onClick={() => void operate(async () => { await knowledgeApi.retry(settings, detail); setNotice("已重新加入识别队列。"); refresh(); })}>重试识别</button>}
              <button className="kb-remove" disabled={busy} onClick={() => { if (window.confirm(`将“${detail.title}”移入知识回收站？移除后不再参与检索，原始文件仍保留。`)) void operate(async () => { await knowledgeApi.remove(settings, detail); setNotice("已移入知识回收站，不再参与检索。"); setDetail(null); refresh(); }); }}>移入回收站</button>
            </>}
          </div>
          {purging && detail.deleted_at && <form className="kb-purge" aria-label="永久删除资料" onSubmit={(e) => {
            e.preventDefault(); if (busy || purgeConfirmation !== "永久删除") return;
            void operate(async () => {
              const result = await knowledgeApi.purge(settings, detail, purgeConfirmation);
              setPurging(false); setDetail(null); setSelected(null);
              setNotice(result.files_pending ? "资料和历史记录已永久删除，原始文件已加入后台清理队列。" : "资料和历史记录已永久删除。"); refresh();
            });
          }}><h3>永久删除“{detail.title}”</h3><p>正文、历史版本和检索记录将立即删除，无法恢复。原始文件由后台清理进程删除；备份及已经下载的副本不会自动清除。</p>
            <label>输入“永久删除”确认<input autoFocus maxLength={20} value={purgeConfirmation} disabled={busy} onChange={(e) => setPurgeConfirmation(e.target.value)} /></label>
            <div className="kb-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => { setPurging(false); setPurgeConfirmation(""); }}>取消永久删除</button>
              <button type="submit" className="kb-remove" disabled={busy || purgeConfirmation !== "永久删除"}>{busy ? "正在永久删除…" : "确认永久删除"}</button></div>
          </form>}
          {!!detail.assets.length && <section className="kb-assets"><h3>原始文件</h3>{detail.assets.map((asset) => <button className="kb-asset" key={asset.id} disabled={busy || !!detail.deleted_at} onClick={() => void operate(() => knowledgeApi.download(settings, asset))}><FileText size={15} /><span>{asset.filename}</span><small>{(asset.size / 1024 / 1024).toFixed(2)} MB · 下载</small></button>)}</section>}
          <KnowledgeOcr key={`${settings.backendUrl}-${settings.backendAuthToken}-${tab}-${detail.id}-${detail.revision}`} item={detail} caps={caps} busy={busy} onSubmit={async (assetId) => {
            const epoch = sessionEpoch.current;
            await operate(async () => {
              const result = await knowledgeApi.ocr(settings, detail, assetId);
              if (epoch !== sessionEpoch.current) return;
              setDetail(result); setNotice("已提交云端 OCR。完成后请核对正文，未确认前不参与检索。"); refresh();
            });
          }} />
          <KnowledgeAsr key={`asr-${settings.backendUrl}-${settings.backendAuthToken}-${tab}-${detail.id}-${detail.revision}`} item={detail} caps={caps} busy={busy} onSubmit={async (assetId) => {
            const epoch = sessionEpoch.current;
            await operate(async () => {
              const result = await knowledgeApi.asr(settings, detail, assetId);
              if (epoch !== sessionEpoch.current) return;
              setDetail(result); setNotice("已提交云端转写。完成后请核对正文，未确认前不参与检索。"); refresh();
            });
          }} />
          <KnowledgeVideoOcr key={`frames-${settings.backendUrl}-${settings.backendAuthToken}-${tab}-${detail.id}-${detail.revision}`} item={detail} caps={caps} busy={busy} onSubmit={async (assetId) => {
            const epoch=sessionEpoch.current;
            await operate(async()=>{
              const result=await knowledgeApi.videoOcr(settings,detail,assetId);
              if(epoch!==sessionEpoch.current) return;
              setDetail(result);setNotice("已提交云端画面 OCR。仅识别采样画面，完成后需核对。");refresh();
            });
          }} />
          {detail.status === "needs_review" && !detail.deleted_at && <p className="kb-ai-warning">请下载原件核对正文及定位。识别有误请先“补充 / 修订正文”；确认将由你把当前正文入库，不会再次调用模型。</p>}
          {detail.content ? <section className="kb-body"><h3>{detail.status === "ready" ? "已提取正文" : detail.status === "needs_review" ? detail.method === "volc_asr_draft" ? "AI 转写正文 · 待核对（暂不参与检索）" : "AI 识别正文 · 待核对（暂不参与检索）" : `保留的旧正文（版本 ${detail.content_revision || "待核对"}，暂不参与检索）`}</h3><div className="kb-source-text">{detail.content}</div></section> : <div className="kb-missing"><h3>还没有可搜索的正文</h3><p>仅保存链接不会参与知识问答。可补充文章正文、SRT / VTT 字幕，或上传图片、PDF、视频 / 音频。</p><p>{caps?.asr_enabled ? "后台已启用本地语音识别。" : caps?.cloud_asr_enabled ? "音视频可在原件下方主动选择云端转写。" : "后台尚未启用语音识别；上传的视频会保留，请先补充字幕或正文。"} {caps?.ocr_enabled ? "图片 / 扫描 PDF 可在原始文件下方主动选择云端 OCR。" : "后台尚未启用图片 OCR。"}</p></div>}
          {caps?.media_merge_available && !detail.deleted_at && <KnowledgeMediaMerge key={`merge-${settings.backendUrl}-${settings.backendAuthToken}-${detail.id}-${detail.revision}`} settings={settings} item={detail} busy={busy} onMerge={(speech, frames) => {
            const epoch = sessionEpoch.current; void operate(async () => { const result = await knowledgeApi.mergeMedia(settings, detail, speech, frames); if (epoch === sessionEpoch.current) { setDetail(result); refresh(); } });
          }} onConfirm={() => { const epoch = sessionEpoch.current; void operate(async () => { const result = await knowledgeApi.confirmMedia(settings, detail); if (epoch === sessionEpoch.current) { setDetail(result); refresh(); } }); }} />}
          {!!detail.history.length && <KnowledgeHistory key={`${detail.id}-${detail.revision}`} settings={settings} item={detail} />}
          {detail.status === "needs_review" && ["doubao_ocr_draft", "volc_asr_draft", "video_ocr_draft"].includes(detail.method) && !detail.deleted_at && <button className="primary-button kb-confirm-ocr" disabled={busy} onClick={confirmOcr}>已核对，入库</button>}
          {caps?.teams_available && <KnowledgeShare key={`share-${settings.backendUrl}-${settings.backendAuthToken}-${detail.id}-${detail.revision}`} settings={settings} item={detail} busy={busy} onShare={async (team) => {
            const epoch = sessionEpoch.current;
            await operate(async () => {
              await knowledgeApi.share(settings, team, detail);
              if (epoch === sessionEpoch.current) setNotice("当前正文版本已共享；原文件与后续修订不共享。可在团队知识库中撤回。");
            });
          }} />}
          <KnowledgeSemantic key={`semantic-${settings.backendUrl}-${settings.backendAuthToken}-${detail.id}-${detail.revision}`} settings={settings} item={detail} caps={caps} busy={busy} onChange={async (remove) => {
            const epoch = sessionEpoch.current;
            let result = null;
            await operate(async () => {
              const value = remove ? await knowledgeApi.removeSemantic(settings, detail) : await knowledgeApi.buildSemantic(settings, detail);
              if (epoch === sessionEpoch.current) result = value;
            });
            return result;
          }} />
          <KnowledgeOrganization key={`organization-${settings.backendUrl}-${settings.backendAuthToken}-${detail.id}-${detail.revision}`} settings={settings} item={detail} caps={caps} busy={busy} onGenerate={async () => {
            const epoch = sessionEpoch.current;
            let result = null;
            await operate(async () => {
              const value = await knowledgeApi.organize(settings, detail);
              if (epoch === sessionEpoch.current) result = value;
            });
            return result;
          }} onUseTags={(tags) => {
            const merged = Array.from(new Set([...detail.tags, ...tags]));
            if (merged.length > 20) { setError("合并后标签超过 20 个，请先手动删减标签。"); return; }
            openEdit(); setDraft((current) => ({ ...current, tags: merged }));
          }} />
        </>}
      </article>
    </div>}
    </div>
  </section>;
}
