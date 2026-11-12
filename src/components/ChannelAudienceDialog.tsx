import { useEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import { articleApi, type ChannelAudience, type ChannelDeliveryConfig } from "../lib/articleApi";
import type { AppSettings } from "../types";

interface Props {
  channel: ChannelDeliveryConfig;
  settings: AppSettings;
  onClose: () => void;
  onSave: (previousIds: number[] | null, allowedIds: number[] | null) => Promise<void>;
}

export function ChannelAudienceDialog({ channel, settings, onClose, onSave }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [audience, setAudience] = useState<ChannelAudience>();
  const [retry, setRetry] = useState(0);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [restricted, setRestricted] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [search, setSearch] = useState("");

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setAudience(undefined);
    setLoadError("");
    setSaveError("");
    articleApi.channelAudience(settings, channel.id, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      const allowed = new Set(result.allowed_user_ids ?? []);
      setAudience(result);
      setRestricted(result.allowed_user_ids !== null);
      setSelected(result.members.filter(({ id }) => allowed.has(id)).map(({ id }) => id));
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setLoadError(message(error));
    });
    return () => controller.abort();
  }, [channel.id, retry, settings]);

  const query = search.trim().toLocaleLowerCase();
  const members = audience?.members.filter(({ name, username }) => `${name} ${username}`.toLocaleLowerCase().includes(query)) ?? [];

  async function save() {
    if (!audience || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      await onSave(audience.allowed_user_ids, restricted ? selected : null);
      onClose();
    } catch (error) {
      setSaveError(message(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <dialog ref={dialog} className="channel-dialog channel-delivery-dialog channel-audience-dialog"
      aria-labelledby="channel-audience-title" onCancel={(event) => {
        event.preventDefault();
        if (!saving) onClose();
      }}>
      <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <header className="dialog-header">
          <h2 id="channel-audience-title">订阅权限</h2>
          <button className="icon-button subtle" type="button" aria-label="关闭" disabled={saving} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="dialog-body">
          <p className="channel-audience-name">{channel.name}</p>
          {loadError ? <div className="channel-audience-error">
            <span className="channel-action-error" role="alert">{loadError}</span>
            <button className="text-button" type="button" onClick={() => setRetry(retry + 1)}>重新加载</button>
          </div> : audience ? <>
            <fieldset className="channel-audience-options" disabled={saving}>
              <legend>谁可以订阅</legend>
              <label className="channel-delivery-check">
                <input type="radio" name="channel-audience" checked={!restricted} onChange={() => setRestricted(false)} />
                所有有频道访问权限的成员
              </label>
              <label className="channel-delivery-check">
                <input type="radio" name="channel-audience" checked={restricted} onChange={() => setRestricted(true)} />
                仅指定成员
              </label>
              {restricted && <>
                <label className="field-group">
                  <span>搜索成员</span>
                  <input type="search" placeholder="输入姓名或账号" value={search} onChange={(event) => setSearch(event.target.value)} />
                </label>
                <p className="channel-audience-note" role="status">
                  {selected.length ? `已选 ${selected.length} 人` : "名单为空时，任何成员都无法订阅。"}
                </p>
                <div className="channel-audience-list">
                  {members.map(({ id, name, username }) => (
                    <label className="channel-audience-member" key={id}>
                      <input type="checkbox" checked={selected.includes(id)} onChange={(event) => {
                        const checked = event.target.checked;
                        setSelected((current) => checked ? [...current, id] : current.filter((value) => value !== id));
                      }} />
                      <span><strong>{name}</strong><small>{username}</small></span>
                    </label>
                  ))}
                  {members.length === 0 && <p className="channel-audience-note">{query ? "没有匹配的成员。" : "暂无可选成员。"}</p>}
                </div>
              </>}
            </fieldset>
            <p className="channel-audience-note">成员需自行订阅后才接收推送。移出名单会取消该成员已有的订阅。</p>
          </> : <p role="status">正在读取订阅权限…</p>}
          {saveError && <div className="channel-audience-error">
            <span className="channel-action-error" role="alert">{saveError}</span>
            <button className="text-button" type="button" onClick={() => setRetry(retry + 1)}>重新加载</button>
          </div>}
        </div>
        <footer className="dialog-footer">
          <button className="secondary-button" type="button" disabled={saving} onClick={onClose}>取消</button>
          <button className="primary-button" type="submit" disabled={!audience || saving}><Check size={15} />{saving ? "正在保存" : "保存订阅权限"}</button>
        </footer>
      </form>
    </dialog>
  );
}

function message(error: unknown) {
  return error instanceof Error ? error.message : "无法读取或保存订阅权限，请重试。";
}
