import { ArrowLeft, ArrowRight, Check, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { deriveChannelRules } from "../lib/channelRules";
import type { AppSettings, ChannelColor, SmartChannel } from "../types";

interface ChannelDialogProps {
  open: boolean;
  channel: SmartChannel | null;
  settings: AppSettings;
  onClose: () => void;
  onSave: (channel: SmartChannel) => void;
}

const colorOptions: Array<{ value: ChannelColor; label: string }> = [
  { value: "green", label: "松绿" },
  { value: "blue", label: "雾蓝" },
  { value: "amber", label: "琥珀" },
  { value: "rose", label: "暖红" },
  { value: "slate", label: "石墨" },
];

export function ChannelDialog({
  open,
  channel,
  settings,
  onClose,
  onSave,
}: ChannelDialogProps) {
  const [step, setStep] = useState<1 | 2>(1);
  const [prompt, setPrompt] = useState("");
  const [form, setForm] = useState(() => emptyForm(settings.defaultServer));

  useEffect(() => {
    if (!open) return;
    if (channel) {
      setPrompt(channel.prompt);
      setForm({
        name: channel.name,
        description: channel.description,
        topic: channel.topic,
        server: channel.server,
        authToken: channel.authToken,
        schedule: channel.schedule,
        maxItems: String(channel.maxItems),
        includeTerms: channel.includeTerms.join("，"),
        excludeTerms: channel.excludeTerms.join("，"),
        color: channel.color,
        active: channel.state === "active",
      });
      setStep(2);
    } else {
      setPrompt("");
      setForm(emptyForm(settings.defaultServer));
      setStep(1);
    }
  }, [open, channel, settings.defaultServer]);

  const canContinue = prompt.trim().length >= 8;
  const canSave = form.name.trim() && form.schedule && Number(form.maxItems) > 0;
  const title = channel ? "编辑智能频道" : step === 1 ? "创建智能频道" : "确认频道规则";

  const preview = useMemo(() => deriveChannelRules(prompt), [prompt]);

  if (!open) return null;

  function generateRules() {
    const rules = deriveChannelRules(prompt);
    setForm((current) => ({
      ...current,
      name: rules.name,
      description: rules.description,
      includeTerms: rules.includeTerms.join("，"),
      excludeTerms: rules.excludeTerms.join("，"),
    }));
    setStep(2);
  }

  function save() {
    const timestamp = Date.now();
    onSave({
      id: channel?.id || crypto.randomUUID(),
      name: form.name.trim(),
      description: form.description.trim(),
      prompt: prompt.trim(),
      topic: form.topic.trim(),
      server: form.server.trim() || settings.defaultServer,
      authToken: form.authToken.trim(),
      schedule: form.schedule,
      maxItems: Math.min(100, Math.max(1, Number(form.maxItems))),
      includeTerms: splitTerms(form.includeTerms),
      excludeTerms: splitTerms(form.excludeTerms),
      color: form.color,
      state: form.active && form.topic.trim() ? "active" : form.topic.trim() ? "paused" : "draft",
      createdAt: channel?.createdAt || timestamp,
      updatedAt: timestamp,
      isDemo: false,
    });
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="channel-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="channel-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div>
            <span className="dialog-eyebrow">{channel ? "CHANNEL SETTINGS" : `STEP ${step} OF 2`}</span>
            <h2 id="channel-dialog-title">{title}</h2>
          </div>
          <button className="icon-button subtle" type="button" aria-label="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        {step === 1 ? (
          <div className="dialog-body prompt-step">
            <div className="prompt-intro">
              <span className="prompt-icon"><Sparkles size={18} /></span>
              <div>
                <strong>描述你希望长期收到什么</strong>
                <p>可以写关注范围、目标读者、排除内容、推送频率和数量。</p>
              </div>
            </div>
            <label className="field-group">
              <span>关注需求</span>
              <textarea
                aria-label="关注需求"
                autoFocus
                rows={8}
                value={prompt}
                placeholder="例如：每天关注全球健康管理服务公司的融资、产品、合作和政策变化，给管理层看。排除个人养生、食谱和普通疾病科普。"
                onChange={(event) => setPrompt(event.target.value)}
              />
              <small>{prompt.trim().length} 字</small>
            </label>
            {canContinue && (
              <div className="rule-preview">
                <span>规则预览</span>
                <strong>{preview.name}</strong>
                <p>{preview.description}</p>
              </div>
            )}
          </div>
        ) : (
          <div className="dialog-body rule-step">
            <div className="form-grid two-column">
              <label className="field-group">
                <span>频道名称</span>
                <input
                  aria-label="频道名称"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </label>
              <label className="field-group">
                <span>每天推送时间</span>
                <input
                  aria-label="每天推送时间"
                  type="time"
                  value={form.schedule}
                  onChange={(event) => setForm({ ...form, schedule: event.target.value })}
                />
              </label>
            </div>
            <label className="field-group">
              <span>频道说明</span>
              <input
                aria-label="频道说明"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />
            </label>
            <div className="form-grid two-column">
              <label className="field-group">
                <span>必须关注</span>
                <input
                  aria-label="必须关注"
                  value={form.includeTerms}
                  placeholder="多个词用逗号分隔"
                  onChange={(event) => setForm({ ...form, includeTerms: event.target.value })}
                />
              </label>
              <label className="field-group">
                <span>排除内容</span>
                <input
                  aria-label="排除内容"
                  value={form.excludeTerms}
                  placeholder="多个词用逗号分隔"
                  onChange={(event) => setForm({ ...form, excludeTerms: event.target.value })}
                />
              </label>
            </div>
            <div className="form-grid three-column">
              <label className="field-group">
                <span>每日最多</span>
                <div className="input-suffix">
                  <input
                    aria-label="每日最多"
                    type="number"
                    min="1"
                    max="100"
                    value={form.maxItems}
                    onChange={(event) => setForm({ ...form, maxItems: event.target.value })}
                  />
                  <span>条</span>
                </div>
              </label>
              <label className="field-group">
                <span>识别颜色</span>
                <select
                  aria-label="识别颜色"
                  value={form.color}
                  onChange={(event) =>
                    setForm({ ...form, color: event.target.value as ChannelColor })
                  }
                >
                  {colorOptions.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="field-group toggle-field">
                <span>保存后启用</span>
                <button
                  type="button"
                  role="switch"
                  aria-label="保存后启用"
                  aria-checked={form.active}
                  className={`switch ${form.active ? "on" : ""}`}
                  onClick={() => setForm({ ...form, active: !form.active })}
                >
                  <span />
                </button>
              </label>
            </div>

            <div className="connection-box">
              <div>
                <strong>ntfy 通知通道</strong>
                <span>Topic 可稍后配置，未配置时频道保存为草稿。</span>
              </div>
              <div className="form-grid two-column">
                <label className="field-group">
                  <span>服务器</span>
                  <input
                    aria-label="服务器"
                    value={form.server}
                    placeholder="https://ntfy.sh"
                    onChange={(event) => setForm({ ...form, server: event.target.value })}
                  />
                </label>
                <label className="field-group">
                  <span>Topic</span>
                  <input
                    aria-label="Topic"
                    value={form.topic}
                    placeholder="例如 company-health-radar"
                    onChange={(event) => setForm({ ...form, topic: event.target.value })}
                  />
                </label>
              </div>
              <label className="field-group">
                <span>访问令牌（可选）</span>
                <input
                  aria-label="访问令牌"
                  type="password"
                  value={form.authToken}
                  placeholder="tk_..."
                  onChange={(event) => setForm({ ...form, authToken: event.target.value })}
                />
              </label>
            </div>
          </div>
        )}

        <footer className="dialog-footer">
          {step === 2 && !channel ? (
            <button className="secondary-button" type="button" onClick={() => setStep(1)}>
              <ArrowLeft size={15} />
              返回修改
            </button>
          ) : (
            <span />
          )}
          {step === 1 ? (
            <button className="primary-button" type="button" disabled={!canContinue} onClick={generateRules}>
              生成频道规则
              <ArrowRight size={15} />
            </button>
          ) : (
            <button className="primary-button" type="button" disabled={!canSave} onClick={save}>
              <Check size={15} />
              保存频道
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}

function emptyForm(server: string) {
  return {
    name: "",
    description: "",
    topic: "",
    server,
    authToken: "",
    schedule: "09:00",
    maxItems: "20",
    includeTerms: "",
    excludeTerms: "",
    color: "green" as ChannelColor,
    active: true,
  };
}

function splitTerms(value: string): string[] {
  return Array.from(
    new Set(value.split(/[，,、]/).map((term) => term.trim()).filter(Boolean)),
  );
}
