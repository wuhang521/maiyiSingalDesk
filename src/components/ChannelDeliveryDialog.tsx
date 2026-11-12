import { Check, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ChannelDeliveryConfig, ChannelDeliveryDraft } from "../lib/articleApi";

interface ChannelDeliveryDialogProps {
  channel: ChannelDeliveryConfig | null;
  onClose: () => void;
  onSave: (draft: ChannelDeliveryDraft) => Promise<void>;
}

const newChannel: ChannelDeliveryDraft = {
  name: "", description: "", enabled: true, schedule: "09:00", max_items: 20,
  source_type: "website", source_url: null, selector: null, include_terms: [], exclude_terms: [],
};

export function ChannelDeliveryDialog({ channel, onClose, onSave }: ChannelDeliveryDialogProps) {
  const [draft, setDraft] = useState<ChannelDeliveryDraft>(() => channel ? {
    id: channel.id, name: channel.name, description: channel.description,
    enabled: channel.enabled, schedule: channel.schedule, max_items: channel.max_items,
    source_type: channel.source_type, source_url: channel.source_url, selector: channel.selector,
    include_terms: channel.include_terms ?? [], exclude_terms: channel.exclude_terms ?? [],
  } : newChannel);
  const [includes, setIncludes] = useState(draft.include_terms.join("，"));
  const [excludes, setExcludes] = useState(draft.exclude_terms.join("，"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const website = draft.source_type === "website";

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose]);

  function update<K extends keyof ChannelDeliveryDraft>(key: K, value: ChannelDeliveryDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function submit() {
    const name = draft.name.trim();
    const sourceUrl = draft.source_url?.trim() || "";
    const selector = draft.selector?.trim() || "";
    if (busy) return;
    if (!name || (website && (!/^https?:\/\//i.test(sourceUrl) || !selector))) {
      setError(website ? "请填写频道名称、公开的 HTTP(S) 网址和内容选择器。" : "请填写频道名称。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSave({
        ...draft,
        name,
        description: draft.description.trim(),
        source_url: website ? sourceUrl : null,
        selector: website ? selector : null,
        include_terms: website ? [] : splitTerms(includes),
        exclude_terms: website ? [] : splitTerms(excludes),
      });
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "保存失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={() => { if (!busy) onClose(); }}>
      <form className="channel-dialog channel-delivery-dialog" role="dialog" aria-modal="true"
        aria-labelledby="channel-delivery-title" onMouseDown={(event) => event.stopPropagation()}
        onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <header className="dialog-header">
          <h2 id="channel-delivery-title">{channel ? "配置频道" : "新建智能频道"}</h2>
          <button className="icon-button subtle" type="button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="dialog-body">
          <fieldset className="channel-delivery-fields" disabled={busy}>
            <label className="field-group">
              <span>频道名称</span>
              <input autoFocus required maxLength={160} value={draft.name} onChange={(event) => update("name", event.target.value)} />
            </label>
            <label className="field-group">
              <span>频道说明</span>
              <input maxLength={2000} value={draft.description} onChange={(event) => update("description", event.target.value)} />
            </label>
            <label className="field-group">
              <span>内容来源</span>
              <select value={draft.source_type} onChange={(event) => update("source_type", event.target.value === "website" ? "website" : "articles")}>
                <option value="website">指定网页</option>
                <option value="articles">频道已有情报</option>
              </select>
            </label>
            {website ? <>
              <label className="field-group">
                <span>网页地址</span>
                <input type="url" required maxLength={2000} placeholder="https://example.com/news"
                  aria-describedby="channel-source-note" value={draft.source_url ?? ""} onChange={(event) => update("source_url", event.target.value)} />
              </label>
              <label className="field-group">
                <span>内容选择器（CSS）</span>
                <input required maxLength={300} placeholder="#news-list a" value={draft.selector ?? ""}
                  aria-describedby="channel-source-note" onChange={(event) => update("selector", event.target.value)} />
              </label>
              <p className="channel-field-note" id="channel-source-note">使用可直接访问的公开网页。选择器指定要订阅的区域，例如 <code>#news-list a</code> 提取新闻列表里的链接。</p>
            </> : <>
              <div className="form-grid two-column">
                <label className="field-group">
                  <span>关注关键词</span>
                  <input value={includes} placeholder="用逗号分隔" onChange={(event) => setIncludes(event.target.value)} />
                </label>
                <label className="field-group">
                  <span>排除关键词</span>
                  <input value={excludes} placeholder="用逗号分隔" onChange={(event) => setExcludes(event.target.value)} />
                </label>
              </div>
              <p className="channel-field-note">从上游已入库的频道文章筛选新情报。留空时推送该频道的全部新情报，最多达到下面的设置。</p>
            </>}
            <div className="form-grid two-column">
              <label className="field-group">
                <span>微信推送时间（北京时间）</span>
                <input type="time" required value={draft.schedule} onChange={(event) => update("schedule", event.target.value)} />
              </label>
              <label className="field-group">
                <span>每日最多（条）</span>
                <input type="number" required min={1} max={100} value={Number.isNaN(draft.max_items) ? "" : draft.max_items}
                  onChange={(event) => update("max_items", event.target.valueAsNumber)} />
              </label>
            </div>
            <label className="channel-delivery-check">
              <input type="checkbox" checked={draft.enabled} onChange={(event) => update("enabled", event.target.checked)} />
              <span>启用微信订阅</span>
            </label>
          </fieldset>
          {error && <p className="channel-action-error" role="alert">{error}</p>}
        </div>
        <footer className="dialog-footer">
          <button className="secondary-button" type="button" disabled={busy} onClick={onClose}>取消</button>
          <button className="primary-button" type="submit" disabled={busy}><Check size={15} />{busy ? "正在保存" : "保存频道"}</button>
        </footer>
      </form>
    </div>
  );
}

function splitTerms(value: string): string[] {
  return Array.from(new Set(value.split(/[，,、\n]+/).map((term) => term.trim()).filter(Boolean)));
}
