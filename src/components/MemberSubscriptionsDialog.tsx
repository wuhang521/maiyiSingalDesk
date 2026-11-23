import { useEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import { articleApi, type MemberChannelSubscriptions } from "../lib/articleApi";
import type { AppSettings } from "../types";
import type { ManagedMember } from "../lib/memberApi";

interface Props {
  member: ManagedMember;
  settings: AppSettings;
  onClose: () => void;
  onSaved: () => void;
}

export function MemberSubscriptionsDialog({ member, settings, onClose, onSaved }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [state, setState] = useState<MemberChannelSubscriptions>();
  const [selected, setSelected] = useState<string[]>([]);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const generation = useRef(0);

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    generation.current++;
    savingRef.current = false;
    setSaving(false);
    setState(undefined);
    setError("");
    articleApi.memberSubscriptions(settings, member.id, controller.signal).then((result) => {
      if (!controller.signal.aborted) {
        setState(result);
        setSelected(result.subscribed_channel_ids);
      }
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setError(message(caught));
    });
    return () => { generation.current++; controller.abort(); };
  }, [member.id, retry, settings.backendUrl, settings.backendAuthToken]);

  async function save() {
    if (!state || savingRef.current) return;
    const sequence = generation.current;
    savingRef.current = true;
    setSaving(true);
    setError("");
    try {
      await articleApi.saveMemberSubscriptions(settings, state, selected);
      if (sequence === generation.current) onSaved();
    } catch (caught) {
      if (sequence === generation.current) setError(message(caught));
    } finally {
      if (sequence === generation.current) {
        savingRef.current = false;
        setSaving(false);
      }
    }
  }

  return <dialog ref={dialog} className="channel-dialog member-subscriptions-dialog" aria-labelledby="member-subscriptions-title"
    onCancel={(event) => { event.preventDefault(); if (!saving) onClose(); }}>
    <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <header className="dialog-header">
        <h2 id="member-subscriptions-title">为{member.name}设置频道订阅</h2>
        <button type="button" className="icon-button subtle" aria-label="关闭频道订阅" disabled={saving} onClick={onClose}><X size={18} /></button>
      </header>
      <div className="dialog-body">
        <p className="member-subscriptions-intro">勾选要接收的频道，保存后直接生效，成员无需再次订阅。</p>
        {state ? <>
          {!state.wechat_bound && <p className="member-feedback" role="status">请先在用户管理的“消息接收渠道”绑定微信后开通订阅。</p>}
          <fieldset disabled={saving} className="member-subscriptions-list">
            <legend>该成员的频道订阅</legend>
            {state.channels.map((channel) => <label className="member-subscriptions-option" key={channel.id}>
              <input type="checkbox" checked={selected.includes(channel.id)} disabled={!channel.subscribed && !channel.can_subscribe}
                onChange={(event) => {
                  const checked = event.target.checked;
                  setSelected((current) => checked ? [...current, channel.id] : current.filter((id) => id !== channel.id));
                }} />
              <span><strong>{channel.name}</strong><small>{channel.unavailable_reason || `每天北京时间 ${channel.schedule} 推送新情报`}</small></span>
            </label>)}
            {state.channels.length === 0 && <p>暂无可订阅频道，请先在智能频道中创建。</p>}
          </fieldset>
          <p className="member-hint">已选 {selected.length} 个频道。取消勾选后，该成员将停止接收对应频道的新推送。</p>
        </> : !error && <p role="status">正在读取该成员的频道订阅…</p>}
        {error && <div className="member-feedback error" role="alert"><span>{error}</span>
          <button type="button" className="text-button" disabled={saving} onClick={() => setRetry((value) => value + 1)}>重新加载</button>
        </div>}
      </div>
      <footer className="dialog-footer">
        <button type="button" className="secondary-button" disabled={saving} onClick={onClose}>取消</button>
        <button type="submit" className="primary-button" disabled={!state || saving}><Check size={15} />{saving ? "正在保存订阅…" : "保存频道订阅"}</button>
      </footer>
    </form>
  </dialog>;
}

function message(error: unknown) {
  if ((error as { status?: number })?.status === 404 && (error as Error).message === "Not Found") {
    return "后台尚未支持管理员代订阅，请先更新后台。";
  }
  return error instanceof Error ? error.message : "无法读取或保存频道订阅，请重试。";
}
