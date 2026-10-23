import { useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeSharedItem, type KnowledgeTeam, type KnowledgeTeamMember } from "../lib/knowledgeApi";

export function KnowledgeTeams({ settings, disabled, onBusy }: { settings: AppSettings; disabled: boolean; onBusy: (value: boolean) => void }) {
  const [teams, setTeams] = useState<KnowledgeTeam[]>([]);
  const [teamId, setTeamId] = useState("");
  const [members, setMembers] = useState<KnowledgeTeamMember[]>([]);
  const [items, setItems] = useState<KnowledgeSharedItem[]>([]);
  const [total, setTotal] = useState(0); const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(""); const [detail, setDetail] = useState<KnowledgeSharedItem | null>(null);
  const [name, setName] = useState(""); const [username, setUsername] = useState(""); const [role, setRole] = useState("member");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ item_id: string; revision: number; locator: string; text: string }[] | null>(null);
  const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false); const live = useRef(true); const request = useRef(0); const running = useRef(false);
  const team = teams.find((row) => row.id === teamId);
  useEffect(() => { live.current = true; return () => { live.current = false; ++request.current; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    knowledgeApi.teams(settings, controller.signal).then(({ items }) => {
      if (controller.signal.aborted) return;
      setTeams(items); setTeamId((id) => items.some((row) => row.id === id) ? id : items[0]?.id || "");
    }).catch((error: unknown) => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "团队列表读取失败"); });
    return () => controller.abort();
  }, [settings.backendUrl, settings.backendAuthToken, tick]);
  useEffect(() => {
    setItems([]); setMembers([]); setDetail(null); setAnswer(null); setSelected("");
    if (!teamId) return;
    const controller = new AbortController();
    Promise.all([knowledgeApi.teamItems(settings, teamId, offset, controller.signal), knowledgeApi.teamMembers(settings, teamId, controller.signal)]).then(([data, roster]) => {
      if (controller.signal.aborted) return;
      setItems(data.items); setTotal(data.total); setMembers(roster.items); setSelected(data.items[0]?.id || "");
    }).catch((error: unknown) => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "团队资料读取失败"); });
    return () => controller.abort();
  }, [settings.backendUrl, settings.backendAuthToken, teamId, offset, tick]);
  useEffect(() => {
    setDetail(null); if (!teamId || !selected) return;
    const controller = new AbortController();
    knowledgeApi.teamDetail(settings, teamId, selected, controller.signal).then((value) => { if (!controller.signal.aborted) setDetail(value); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "共享资料已不可访问"); });
    return () => controller.abort();
  }, [settings.backendUrl, settings.backendAuthToken, teamId, selected, tick]);
  async function operate(action: () => Promise<void>) {
    if (disabled || running.current) return;
    const epoch = ++request.current;
    running.current = true; setBusy(true); onBusy(true); setError(""); setNotice("");
    try { await action(); } catch (error) { if (live.current && epoch === request.current) setError(error instanceof Error ? error.message : "团队操作失败"); }
    finally { running.current = false; if (live.current) { setBusy(false); onBusy(false); } }
  }
  return <div className="kb-team-workspace">
    <div className="kb-team-controls">
      <h2>团队知识库 · 只读共享</h2><p>只展示成员主动共享的当前已核对正文。管理员不能通过这里读取未共享的个人资料；原件和私人 AI 笔记不共享。撤回不能清除他人此前复制的文字。</p>
      <label>选择团队<select value={teamId} disabled={disabled || busy} onChange={(event) => { setTeamId(event.target.value); setOffset(0); setError(""); }}>
        <option value="">{teams.length ? "选择团队" : "尚未加入团队"}</option>{teams.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <details><summary>创建一个团队</summary><form onSubmit={(event) => { event.preventDefault(); void operate(async () => {
        const value = await knowledgeApi.createTeam(settings, name.trim()); if (!live.current) return;
        setName(""); setTeamId(value.id); setTick((n) => n + 1); setNotice("团队已创建，没有自动共享任何个人资料。");
      }); }}><label>团队名称<input value={name} maxLength={160} required disabled={disabled || busy} onChange={(event) => setName(event.target.value)} /></label><button className="secondary-button" disabled={disabled || busy || !name.trim()}>创建团队</button></form></details>
      {team?.can_manage && <details><summary>管理团队成员 · {members.length} 位</summary>
        <form onSubmit={(event) => { event.preventDefault(); void operate(async () => {
          await knowledgeApi.setTeamMember(settings, team, username.trim(), role); if (!live.current) return;
          setUsername(""); setTick((n) => n + 1); setNotice("团队成员设置已保存。个人资料没有自动共享。");
        }); }}><label>个人登录账号名<input value={username} maxLength={80} required disabled={disabled || busy} onChange={(event) => setUsername(event.target.value)} /></label>
          <label>团队角色<select value={role} disabled={disabled || busy} onChange={(event) => setRole(event.target.value)}><option value="member">只读成员</option><option value="manager">团队负责人（可管理成员）</option></select></label>
          <button className="secondary-button" disabled={disabled || busy || !username.trim()}>添加 / 修改成员</button></form>
        <ul>{members.map((member) => <li key={member.user_id}>{member.name}（{member.username}）· {member.is_owner ? "创建者" : member.role === "manager" ? "负责人" : "成员"}{!member.enabled && " · 已停用"}
          {!member.is_owner && <button className="kb-remove" disabled={disabled || busy} onClick={() => {
            if (window.confirm(`移除 ${member.name} 的团队访问权限，同时撤回其在本团队的共享？不能清除此前复制的文字。`)) void operate(async () => { await knowledgeApi.removeTeamMember(settings, team, member.user_id); if (live.current) setTick((n) => n + 1); });
          }}>移除成员</button>}</li>)}</ul>
        {team.is_owner && <button className="kb-remove" disabled={disabled || busy} onClick={() => {
          if (window.confirm("关闭这个团队并撤销全部共享访问？个人原文、文件和历史仍保留。")) void operate(async () => { await knowledgeApi.closeTeam(settings, team); if (live.current) { setTeamId(""); setTick((n) => n + 1); } });
        }}>关闭团队（保留个人资料）</button>}
      </details>}
      {error && <p className="kb-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      {team && <form onSubmit={(event) => { event.preventDefault(); void operate(async () => {
        const result = await knowledgeApi.teamSearch(settings, team.id, question.trim()); if (live.current) setAnswer(result.items);
      }); }}><label>搜索团队已共享原文<input value={question} minLength={2} maxLength={500} required disabled={disabled || busy} onChange={(event) => setQuestion(event.target.value)} /></label><button className="secondary-button" disabled={disabled || busy}>查找团队原文</button></form>}
      {answer && <div className="kb-source-text">{answer.length ? answer.map((row, index) => <p key={index}><button className="secondary-button" disabled={disabled || busy} onClick={() => setSelected(row.item_id)}>{row.locator} · 版本 {row.revision}</button><br />{row.text}</p>) : <p>没有找到当前共享版本的相关正文。</p>}</div>}
    </div>
    <div className="kb-columns">
      <div className="kb-library"><div className="kb-list">
        {!items.length ? <p className="kb-empty">尚无当前有效的共享资料。请在“全部资料”详情中主动共享；修订正文后需要重新共享。</p> : items.map((item) => <button key={item.id} className={`kb-list-item ${selected === item.id ? "selected" : ""}`} disabled={disabled || busy} onClick={() => setSelected(item.id)}><strong>{item.title}</strong><p>{item.summary}</p><small>共享正文版本 {item.revision}</small></button>)}
      </div>{total > 50 && <div className="kb-pagination"><button className="secondary-button" disabled={!offset || disabled || busy} onClick={() => setOffset((n) => Math.max(0, n - 50))}>上一页</button><span>{offset + 1}–{Math.min(offset + 50, total)} / {total}</span><button className="secondary-button" disabled={offset + 50 >= total || disabled || busy} onClick={() => setOffset((n) => n + 50)}>下一页</button></div>}</div>
      <article className="kb-detail" aria-label="团队共享正文">{detail ? <><h2>{detail.title}</h2><p>版本 {detail.revision} · 只读共享正文，不含原件。</p>{detail.source_url && <a href={detail.source_url} target="_blank" rel="noreferrer">查看共享来源</a>}<div className="kb-source-text">{detail.content}</div>
        {team && detail.can_revoke && <button className="kb-remove" disabled={disabled || busy} onClick={() => {
          if (window.confirm("撤回这份资料在当前团队的共享？原文保留；需要资料所有者或团队负责人权限。")) void operate(async () => { await knowledgeApi.revokeShare(settings, team, detail.id); if (live.current) setTick((n) => n + 1); });
        }}>撤回本团队共享</button>}</> : <p className="kb-empty">选择一份共享资料阅读。</p>}</article>
    </div>
  </div>;
}
