import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArchiveRestore, Plus, RefreshCw, Search, ShieldCheck, Trash2, X } from "lucide-react";
import { memberApi, type ManagedMember, type MemberDraft } from "../lib/memberApi";
import { taskApi } from "../lib/taskApi";
import type { AppSettings, StaffPlatform } from "../types";
import "./member-management.css";

interface Props {
  settings: AppSettings;
  currentStaffId?: number;
  onMembersChanged: () => void;
}

const blankDraft: MemberDraft = { name: "", username: "", password: "", role: "member", enabled: true };
const platforms: Record<StaffPlatform, string> = { qq: "QQ", weixin: "微信", wecom: "企业微信" };
type Filter = "current" | "enabled" | "disabled" | "removed";

function isEnabled(member: ManagedMember) {
  return member.enabled && (!member.user || member.user.enabled);
}

export function MemberManagement({ settings, currentStaffId, onMembersChanged }: Props) {
  const [members, setMembers] = useState<ManagedMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [channelError, setChannelError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("current");
  const [editing, setEditing] = useState<ManagedMember | null | undefined>(undefined);
  const [draft, setDraft] = useState<MemberDraft>(blankDraft);
  const [platform, setPlatform] = useState<StaffPlatform>("qq");
  const [recipient, setRecipient] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);
  const createButton = useRef<HTMLButtonElement>(null);
  const channelErrorSummary = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef(false);
  const requestSequence = useRef(0);

  const load = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setError("");
    try {
      const result = await memberApi.list(settings);
      if (sequence === requestSequence.current) {
        setMembers(result);
        return result;
      }
    } catch (caught) {
      if (sequence === requestSequence.current) setError(message(caught));
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [settings.backendUrl, settings.backendAuthToken]);

  useEffect(() => {
    void load();
    return () => { requestSequence.current++; };
  }, [load]);
  useEffect(() => {
    if (editing !== undefined) nameInput.current?.focus();
  }, [editing?.id, editing === null]);
  useEffect(() => {
    if (restoreFocus.current && !busy && editing === undefined) {
      createButton.current?.focus();
      restoreFocus.current = false;
    }
  }, [busy, editing]);
  useEffect(() => {
    if (channelError) {
      channelErrorSummary.current?.focus();
      channelErrorSummary.current?.scrollIntoView?.({ block: "nearest" });
    }
  }, [channelError]);

  const visible = useMemo(() => members.filter((member) => {
    const matchesState = filter === "removed" ? Boolean(member.removed_at)
      : !member.removed_at && (filter === "current" || (filter === "enabled" ? isEnabled(member) : !isEnabled(member)));
    const query = search.trim().toLocaleLowerCase();
    return matchesState && `${member.name} ${member.user?.username || ""}`.toLocaleLowerCase().includes(query);
  }), [members, search, filter]);
  const currentCount = members.filter((member) => !member.removed_at).length;
  const self = editing?.id === currentStaffId;

  function openEditor(member: ManagedMember | null) {
    if (busy) return;
    setError(""); setChannelError(""); setNotice(""); setRecipient("");
    setEditing(member);
    setDraft(member ? {
      name: member.name, username: member.user?.username || "", password: "",
      role: member.user?.role || "member", enabled: isEnabled(member),
    } : { ...blankDraft });
  }

  function closeEditor() {
    restoreFocus.current = true;
    setEditing(undefined); setDraft({ ...blankDraft });
    setChannelError("");
  }

  async function perform(action: () => Promise<unknown>, success: string, close = true) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(""); setChannelError(""); setNotice("");
    try {
      await action();
      if (close) closeEditor();
      setNotice(success);
      const refreshed = await load();
      if (!close) {
        setEditing((current) => current ? refreshed?.find((member) => member.id === current.id) || current : current);
        setRecipient("");
      }
      onMembersChanged();
    } catch (caught) {
      if (close) setError(message(caught));
      else setChannelError(message(caught));
    }
    finally { busyRef.current = false; setBusy(false); }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (editing === undefined) return;
    if (editing && (!draft.enabled && isEnabled(editing)) &&
      !window.confirm(`停用“${editing.name}”吗？该用户将立即失去登录权限，历史记录会保留。`)) return;
    if (editing?.user && draft.role !== editing.user.role &&
      !window.confirm(`将“${editing.name}”设为${draft.role === "admin" ? "管理员" : "普通成员"}吗？该用户需要重新登录。`)) return;
    if (editing?.user && draft.password &&
      !window.confirm(`重置“${editing.name}”的密码吗？该用户已有的登录会话将失效。`)) return;
    const payload = { ...draft, name: draft.name.trim(), username: draft.username?.trim(), password: draft.password || undefined };
    void perform(() => editing ? memberApi.update(settings, editing.id, payload) : memberApi.create(settings, payload),
      editing ? "用户设置已保存。权限或密码变更后需要重新登录。" : "用户已添加，可以使用新账号登录。请单独告知对方初始密码。");
  }

  function remove(member: ManagedMember) {
    if (!window.confirm(`移除“${member.name}”（${member.user?.username || "未开通登录"}）吗？\n登录及消息渠道任务权限将被撤销。历史任务和审核记录会保留，可在“已移除”中恢复。`)) return;
    void perform(() => memberApi.remove(settings, member.id), `已移除“${member.name}”，历史记录已保留。`);
  }

  return <section className="member-management" aria-label="用户管理">
    <div className="member-heading">
      <div><h2><ShieldCheck size={20} aria-hidden="true" />用户管理</h2><p>管理人员加入、登录权限和消息接收渠道。当前 {currentCount} 名人员。</p></div>
      <div className="member-actions">
        <button className="secondary-button" type="button" disabled={loading || busy} onClick={() => void load()}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新用户</button>
        <button ref={createButton} className="primary-button" type="button" disabled={busy} onClick={() => openEditor(null)}><Plus size={16} />添加用户</button>
      </div>
    </div>
    {error && <div className="member-feedback error" role="alert">{error}</div>}
    {notice && <div className="member-feedback" role="status">{notice}</div>}
    <div className={`member-layout ${editing !== undefined ? "with-editor" : ""}`}>
      <div className="member-roster">
        <div className="member-filters">
          <label className="member-search"><Search size={16} aria-hidden="true" /><input aria-label="搜索用户" placeholder="搜索姓名或登录账号" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <label><span className="member-label">人员状态</span><select aria-label="人员状态" value={filter} disabled={busy} onChange={(event) => { setFilter(event.target.value as Filter); closeEditor(); }}>
            <option value="current">全部在册</option><option value="enabled">已启用</option><option value="disabled">已停用</option><option value="removed">已移除</option>
          </select></label>
        </div>
        <div className="member-table-scroll" aria-busy={loading}>
          <table className="member-table"><caption>人员与登录账号</caption><thead><tr><th scope="col">人员 / 登录账号</th><th scope="col">角色</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
            <tbody>{visible.map((member) => <tr key={member.id} className={editing?.id === member.id ? "selected" : ""}>
              <td><strong>{member.name}{member.id === currentStaffId && <span className="member-self">我</span>}</strong><small>{member.user?.username || "尚未开通登录"}</small></td>
              <td>{member.user?.role === "admin" ? "管理员" : member.user ? "成员" : "仅任务人员"}</td>
              <td><span className={`member-status ${member.removed_at ? "removed" : isEnabled(member) ? "enabled" : "disabled"}`}>{member.removed_at ? "已移除" : isEnabled(member) ? "已启用" : "已停用"}</span></td>
              <td>{member.removed_at ? <button type="button" className="member-text-button" disabled={busy} onClick={() => {
                if (window.confirm(`恢复“${member.name}”吗？恢复后保持停用，需要管理员另外启用。`)) void perform(() => memberApi.restore(settings, member.id), "用户已恢复为停用状态，请编辑并启用后再登录。");
              }}><ArchiveRestore size={14} />恢复用户</button> : <div className="member-row-actions">
                <button type="button" className="member-text-button" disabled={busy} aria-label={`管理 ${member.name}`} onClick={() => openEditor(member)}>管理</button>
                <button type="button" className="member-text-button danger" disabled={busy || member.id === currentStaffId} aria-label={`移除 ${member.name}`} title={member.id === currentStaffId ? "不能移除当前登录账号" : "撤销权限并保留历史"} onClick={() => remove(member)}>移除</button>
              </div>}</td>
            </tr>)}</tbody>
          </table>
          {loading && <p className="member-empty" role="status">正在读取用户…</p>}
          {!loading && visible.length === 0 && <div className="member-empty"><strong>{search ? "没有匹配的用户" : filter === "removed" ? "暂无已移除用户" : "当前没有符合条件的人员"}</strong><p>{search ? "尝试其他姓名或登录账号。" : filter === "current" ? "点击“添加用户”，开通第一个成员账号。" : "可以切换人员状态查看其他用户。"}</p></div>}
        </div>
        <p className="member-footnote">移除只撤销访问权限，历史任务、内容和审核记录仍保留。已有任务人员可通过“管理”开通登录。</p>
      </div>
      {editing !== undefined && <aside className="member-editor" aria-label={editing ? `管理 ${editing.name}` : "添加用户表单"}>
        <div className="member-editor-heading"><h3>{editing ? "管理用户" : "添加用户"}</h3><button className="icon-button" type="button" aria-label="关闭用户表单" disabled={busy} onClick={closeEditor}><X size={18} /></button></div>
        <form onSubmit={submit}>
          <fieldset disabled={busy}>
            <label><span>姓名或称呼</span><input ref={nameInput} required maxLength={80} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
            <label><span>登录账号</span><input required minLength={2} maxLength={80} autoComplete="off" readOnly={Boolean(editing?.user)} value={draft.username} onChange={(event) => setDraft({ ...draft, username: event.target.value })} /><small>{editing?.user ? "登录账号固定，用于识别成员身份。" : "2–80 个字符，不区分英文字母大小写。"}</small></label>
            {!self && <label><span>{editing?.user ? "重置密码（选填）" : "初始密码"}</span><input type="password" minLength={8} maxLength={128} required={!editing?.user} autoComplete="new-password" value={draft.password} placeholder={editing?.user ? "留空则保留原密码" : "至少 8 个字符"} onChange={(event) => setDraft({ ...draft, password: event.target.value })} /></label>}
            <label><span>用户角色</span><select value={draft.role} disabled={self} onChange={(event) => setDraft({ ...draft, role: event.target.value as MemberDraft["role"] })}><option value="member">普通成员</option><option value="admin">管理员</option></select><small>管理员可以添加用户、重置密码和调整权限。</small></label>
            {editing && <label className="member-toggle"><input type="checkbox" checked={draft.enabled} disabled={self} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>启用该用户</span></label>}
            {self && <p className="member-hint">当前登录账号的角色和启用状态受保护。</p>}
            <button className="primary-button member-save" type="submit" disabled={!draft.name.trim() || !draft.username?.trim()}>{busy ? "正在保存…" : editing ? "保存用户设置" : "确认添加"}</button>
          </fieldset>
        </form>
        {editing && <section className="member-channel-section" aria-label="消息接收渠道">
          <h4>消息接收渠道</h4><p>绑定后可通过对应消息账号处理任务。</p>
          {channelError && <div ref={channelErrorSummary} tabIndex={-1} className="member-feedback error" role="alert">{channelError}</div>}
          {editing.channels.length === 0 && <p className="member-hint">尚未绑定接收渠道。</p>}
          {editing.channels.map((channel) => <div className="member-channel" key={channel.id}><span>{platforms[channel.platform]}</span><code title={channel.recipient_id}>{channel.recipient_id}</code><button className="icon-button danger" type="button" disabled={busy} aria-label={`解绑 ${platforms[channel.platform]} ${channel.recipient_id}`} onClick={() => {
            if (window.confirm(`解除“${editing.name}”的${platforms[channel.platform]}绑定吗？`)) void perform(() => taskApi.deleteStaffChannel(settings, editing.id, channel.id), "渠道绑定已解除。", false);
          }}><Trash2 size={14} /></button></div>)}
          <form onSubmit={(event) => {
            event.preventDefault();
            if (recipient.trim()) void perform(() => taskApi.addStaffChannel(settings, editing.id, platform, recipient.trim()), "消息渠道已绑定。", false);
          }}><fieldset disabled={busy}>
            <label><span>接收渠道</span><select value={platform} onChange={(event) => setPlatform(event.target.value as StaffPlatform)}>{Object.entries(platforms).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            <label><span>渠道接收 ID</span><input required maxLength={160} value={recipient} onChange={(event) => setRecipient(event.target.value)} /></label>
            <button className="secondary-button" disabled={!recipient.trim()}>绑定渠道</button>
          </fieldset></form>
        </section>}
      </aside>}
    </div>
  </section>;
}

function message(error: unknown) { return error instanceof Error ? error.message : "操作未完成，请稍后重试"; }
