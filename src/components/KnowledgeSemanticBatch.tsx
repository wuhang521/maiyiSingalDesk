import { useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeItem } from "../lib/knowledgeApi";
export function KnowledgeSemanticBatch({ settings, items, disabled, onBusy }: { settings: AppSettings; items: KnowledgeItem[]; disabled: boolean; onBusy: (v: boolean) => void }) {
  const [selected, setSelected] = useState<string[]>([]); const [consent, setConsent] = useState(false); const [busy, setBusy] = useState(false); const [results, setResults] = useState<string[]>([]);
  const stop = useRef(false); const live = useRef(true); const running = useRef(false);
  const page = items.filter(i => i.status === "ready" && !i.deleted_at);
  const identity = page.map(i => `${i.id}:${i.revision}`).join("|");
  useEffect(() => { live.current = true; return () => { live.current = false; stop.current = true; }; }, []);
  useEffect(() => { if (!running.current) { setSelected([]); setConsent(false); } }, [identity]);
  async function submit() {
    if (running.current || disabled || !consent || !selected.length) return;
    const snapshots = page.filter(i => selected.includes(i.id));
    running.current = true; stop.current = false; setBusy(true); onBusy(true); setConsent(false); setResults([]);
    try {
      for (const snapshot of snapshots) {
        if (stop.current || !live.current) break;
        try {
          const current = await knowledgeApi.detail(settings, snapshot.id);
          if (!live.current || stop.current) break;
          if (current.revision !== snapshot.revision || current.status !== "ready") throw new Error("正文版本或状态已变，本份未发送");
          const result = await knowledgeApi.buildSemantic(settings, current);
          if (live.current) setResults(v => [...v, `${snapshot.title}：${result.state === "ready" ? "当前索引已建立" : "结果待确认"}`]);
        } catch (e) { if (live.current) setResults(v => [...v, `${snapshot.title}：${e instanceof Error ? e.message : "结果未确认"}；本批停止，不自动重试`]); break; }
      }
    } finally { running.current = false; if (live.current) { setBusy(false); onBusy(false); setSelected([]); } }
  }
  return <details className="kb-auxiliary"><summary>本页资料批量建索引（最多 20 份）</summary><p>只发送明确勾选的当前已入库正文；每份分别预留提交次数及已配置预算，可能计费。已有当前索引跳过，未知结果停止，不自动重试、不导出整库。</p>
    <fieldset disabled={busy || disabled}>{page.map(i => <label className="kb-consent" key={i.id}><input type="checkbox" checked={selected.includes(i.id)} disabled={!selected.includes(i.id) && selected.length >= 20} onChange={e => { setSelected(v => e.target.checked ? [...v, i.id] : v.filter(id => id !== i.id)); setConsent(false); }} />{i.title} · 版本 {i.revision}</label>)}
      {!page.length && <p>本页没有已入库资料；请先核对正文，或切换列表页。</p>}
      <label className="kb-consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />同意发送以上 {selected.length} 份当前正文并承担可能费用</label>
      <button className="primary-button" disabled={!consent || !selected.length} onClick={() => void submit()}>建立所选资料索引</button>
    </fieldset>
    {busy && <button className="secondary-button" onClick={() => { stop.current = true; }}>当前请求结束后停止</button>}
    <ul aria-live="polite">{results.map((r, i) => <li key={i}>{r}</li>)}</ul>
  </details>;
}
