import { useEffect, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeDetail, type KnowledgeTeam } from "../lib/knowledgeApi";

export function KnowledgeShare({ settings, item, busy, onShare }: {
  settings: AppSettings; item: KnowledgeDetail; busy: boolean; onShare: (team: KnowledgeTeam) => Promise<void>;
}) {
  const [teams, setTeams] = useState<KnowledgeTeam[]>([]); const [teamId, setTeamId] = useState("");
  const [consent, setConsent] = useState(false); const [error, setError] = useState("");
  useEffect(() => {
    if (busy) return;
    const controller = new AbortController();
    knowledgeApi.teams(settings, controller.signal).then(({ items }) => { if (!controller.signal.aborted) { setTeams(items); setTeamId((id) => items.some((row) => row.id === id) ? id : ""); } })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "团队读取失败"); });
    return () => controller.abort();
  }, [settings.backendUrl, settings.backendAuthToken, busy]);
  if (item.deleted_at) return null;
  const team = teams.find((row) => row.id === teamId);
  return <section className="kb-ocr" aria-label="主动共享当前正文">
    <h3>共享到团队 · 默认不共享</h3><p id="kb-share-disclosure">仅共享本份当前已核对正文、标题、标签和来源；不共享原文件、私人 AI 笔记或后续修订。团队负责人可以管理成员和撤回共享，但不能读取未共享的个人资料。撤回不能清除已经复制的文字。</p>
    {error && <p role="alert">{error}</p>}
    {!teams.length ? <p>尚未加入团队，请到“团队知识库”创建或加入团队。</p> : <form onSubmit={(event) => { event.preventDefault(); if (busy || !team || !consent || item.status !== "ready") return; setConsent(false); void onShare(team); }}>
      <label className="kb-ocr-file">共享目标<select value={teamId} disabled={busy} onChange={(event) => { setTeamId(event.target.value); setConsent(false); }}><option value="">请选择团队</option>{teams.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <label className="kb-ocr-consent"><input type="checkbox" checked={consent} disabled={busy || !team || item.status !== "ready"} aria-describedby="kb-share-disclosure" onChange={(event) => setConsent(event.target.checked)} />同意向所选团队共享当前正文版本</label>
      <button className="primary-button" disabled={busy || !team || !consent || item.status !== "ready"}>共享当前正文</button>
    </form>}
  </section>;
}
