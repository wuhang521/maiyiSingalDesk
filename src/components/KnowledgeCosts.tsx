import { useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeCosts as Costs, type KnowledgeCostRow } from "../lib/knowledgeApi";
const labels: Record<string, string> = { doubao_ocr: "原件 OCR", volc_asr: "语音转写", video_ocr: "采样画面 OCR", cloud_organize: "AI 整理", semantic_index: "语义索引", semantic_query: "语义查询", cloud_qa: "AI 综合问答" };
export function KnowledgeCosts({ settings, canManage, disabled }: { settings: AppSettings; canManage: boolean; disabled: boolean }) {
  const [costs, setCosts] = useState<Costs | null>(null); const [rows, setRows] = useState<KnowledgeCostRow[]>([]);
  const [admin, setAdmin] = useState(false); const [offset, setOffset] = useState(0); const [tick, setTick] = useState(0);
  const [bill, setBill] = useState(""); const [consent, setConsent] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const live = useRef(true); const running = useRef(false);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => { if (disabled || busy) return; const c = new AbortController(); setError(""); Promise.all([knowledgeApi.costs(settings, c.signal), knowledgeApi.costLedger(settings, offset, admin && canManage, c.signal)]).then(([v, r]) => { if (!c.signal.aborted) { setCosts(v); setRows(r.items); } }).catch(e => { if (!c.signal.aborted) setError(e.message); }); return () => c.abort(); }, [settings.backendUrl, settings.backendAuthToken, offset, admin, tick, disabled, busy, canManage]);
  async function reconcile() {
    if (!canManage || !consent || disabled || running.current) return;
    running.current = true; setBusy(true); setError(""); setNotice(""); setConsent(false);
    try {
      const parsed = JSON.parse(bill);
      if (!Array.isArray(parsed) || !parsed.length || parsed.length > 100 || parsed.some(r => !r || typeof r.usage_id !== "string" || typeof r.amount_cny !== "string" || typeof r.invoice_reference !== "string" || Object.keys(r).some(k => !["usage_id", "amount_cny", "invoice_reference"].includes(k)))) throw new Error("请输入 1–100 行费用 JSON 数组，金额用字符串，不包含凭据或资料正文");
      const result = await knowledgeApi.reconcileCosts(settings, parsed);
      if (live.current) { setNotice(`已保存 ${result.reconciled} 行人工核对的账单记录。`); setBill(""); setTick(v => v + 1); }
    } catch (e) { if (live.current) setError(e instanceof Error ? e.message : "对账结果未确认，请先刷新账本，不自动重试"); }
    finally { running.current = false; if (live.current) setBusy(false); }
  }
  return <details className="kb-auxiliary"><summary>人民币预算与账本（预留不是账单）</summary>
    {error && <p className="kb-error" role="alert">{error} · 旧数值不作为可用余额。</p>}
    {costs && !error && <><p>{costs.enabled ? `已启用人民币预留限制：今日 ${costs.daily_exposure_cny} / ${costs.daily_limit_cny} 元，本月 ${costs.monthly_exposure_cny} / ${costs.monthly_limit_cny} 元。` : "人民币预留限制尚未配置；提交次数额度仍生效。没有猜测服务商单价。"}</p><p>本月未定价提交 {costs.monthly_unpriced} 次。启用预算后，当前账期未知费用需先对账，不能按零计。UTC 日 / 月统计。</p>
      {!!Object.keys(costs.request_ceiling_cny).length && <ul>{Object.entries(costs.request_ceiling_cny).map(([op, value]) => <li key={op}>{labels[op] || op}：每次最高预留 {value} 元</li>)}</ul>}</>}
    <p>预留取管理员配置的保守上限，含整份原件 / 多画面调用；服务商实际金额可能不同，以核实账单为准。失败、超时不会自动释放预算；删除资料不会删账。实际费用只来自管理员逐行核对的账单，不自动由 token 数猜价。</p>
    {canManage && <label className="kb-consent"><input type="checkbox" disabled={busy || disabled} checked={admin} onChange={e => { setAdmin(e.target.checked); setOffset(0); setRows([]); }} />查看管理员费用元数据账本（不含个人正文）</label>}
    <ul>{rows.map(r => <li key={r.usage_id}>{r.owner_id !== undefined && `账号 ${r.owner_id} · `}{labels[r.operation] || r.operation} · 提交编号 {r.usage_id}<br />提交时间（UTC）：{r.created_at}<br />预留：{r.reserved_cny === null ? "未定价" : `${r.reserved_cny} 元`}；{r.actual_cny === null ? "账单未核实" : `人工核对实际账单：${r.actual_cny} 元`}
      {!!r.provider_requests?.length && <details><summary>服务商关联记录（不是费用）</summary><ul>{r.provider_requests.map((p, index) => <li key={index}>{p.provider} · HTTP {p.http_status}<br />请求 / 日志编号：{p.request_id || "未提供"}<br />响应编号：{p.response_id || "未提供"}<br />计量：{Object.entries(p.usage).map(([k, v]) => `${k}=${v}`).join("，") || "未提供数值"}</li>)}</ul></details>}
    </li>)}</ul>
    <button className="secondary-button" disabled={busy || disabled || !offset} onClick={() => setOffset(v => Math.max(0, v - 50))}>上一批</button> <button className="secondary-button" disabled={busy || disabled || rows.length < 50} onClick={() => setOffset(v => v + 50)}>下一批</button> <button className="secondary-button" disabled={busy || disabled} onClick={() => setTick(v => v + 1)}>刷新账本</button>
    {canManage && <details><summary>导入已核实账单金额</summary><p>仅在能明确对应本地提交与账单行时填写，无法匹配时不要猜测。金额最多 6 位小数，已对账记录不能覆盖，同一账单行不能用于多个提交。</p><form onSubmit={e => { e.preventDefault(); void reconcile(); }}><fieldset disabled={busy || disabled}><label>账单行 JSON（最多 100 行）<textarea value={bill} maxLength={60000} onChange={e => { setBill(e.target.value); setConsent(false); }} placeholder={'[{"usage_id":"本地提交编号","amount_cny":"核实的金额","invoice_reference":"服务商:账单号:行号"}]'} /></label><label className="kb-consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />已逐行核对服务商账单与提交记录，金额不是估算</label><button className="primary-button" disabled={!consent || !bill.trim()}>保存人工对账记录</button></fieldset></form></details>}
    {notice && <p role="status">{notice}</p>}
  </details>;
}
