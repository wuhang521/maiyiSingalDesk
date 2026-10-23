import { useEffect, useId, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Bold, Italic, List, Plus, Trash2, Upload } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AppSettings } from "../../types";
import { blankContent, contentApi, type ContentData, type ContentField, type ContentMember } from "../../lib/contentApi";

interface FieldsProps {
  fields: ContentField[]; value: ContentData; onChange: (value: ContentData) => void;
  members: ContentMember[]; sources?: ContentData[]; claims?: ContentData[];
  columns?: ContentData[];
  settings: AppSettings; disabled?: boolean; path?: string;
}

export function ContentFields(props: FieldsProps) {
  return <div className="creation-fields">{props.fields.filter((f) => !f.show_if || props.value[f.show_if[0]] === f.show_if[1]).map((field) =>
    <ContentInput key={field.key} {...props} field={field} />,
  )}</div>;
}

function ContentInput({ field: f, ...props }: FieldsProps & { field: ContentField }) {
  const id = useId();
  const value = props.value[f.key];
  const path = `${props.path || ""}${f.label}`;
  const update = (next: unknown) => props.onChange({ ...props.value, [f.key]: next });
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const label = <label htmlFor={id}>{f.label}{f.required && <span className="creation-required" aria-label="必填"> *</span>}</label>;
  if (f.kind === "object") return <fieldset className="creation-object" disabled={props.disabled}>
    <legend>{f.label}</legend><ContentFields {...props} fields={f.fields || []} value={value || {}} path={`${path} / `} onChange={update} />
  </fieldset>;
  if (f.kind === "records") {
    const rows: ContentData[] = value || [];
    return <fieldset className="creation-records" disabled={props.disabled}>
      <legend>{f.label}{f.required && " *"}<span>{rows.length} 条</span></legend>
      {f.hint && <p className="creation-hint">{f.hint}</p>}
      {rows.map((row, index) => <section className="creation-record" key={row.id || index}>
        <div className="creation-record-bar"><strong>{index + 1}. {row.name || row.title || row.platform || row.claim_text?.slice(0, 32) || f.label}</strong>
          <div>
            <button type="button" className="creation-icon" aria-label={`上移${f.label} ${index + 1}`} disabled={index === 0} onClick={() => {
              const next = [...rows]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; update(next);
            }}><ArrowUp size={14} /></button>
            <button type="button" className="creation-icon" aria-label={`下移${f.label} ${index + 1}`} disabled={index === rows.length - 1} onClick={() => {
              const next = [...rows]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; update(next);
            }}><ArrowDown size={14} /></button>
            <button type="button" className="creation-icon" aria-label={`删除${f.label} ${index + 1}`} onClick={() => update(rows.filter((_, i) => i !== index))}><Trash2 size={14} /></button>
          </div>
        </div>
        <ContentFields {...props} fields={f.fields || []} value={row} path={`${path} ${index + 1} / `}
          onChange={(next) => update(rows.map((old, i) => i === index ? next : old))} />
      </section>)}
      <button type="button" className="creation-text-button" onClick={() => update([...rows, { ...blankContent(f.fields || []), id: crypto.randomUUID() }])}>
        <Plus size={14} />添加{f.label}
      </button>
    </fieldset>;
  }
  if (["multi", "sources", "claims"].includes(f.kind)) {
    const options = f.kind === "multi" ? (f.options || []).map((v) => ({ value: v, label: v })) :
      (f.kind === "sources" ? props.sources || [] : props.claims || []).map((v) => ({ value: v.id as string, label: v.title || v.claim_text }));
    return <fieldset className="creation-choices" disabled={props.disabled}><legend>{f.label}{f.required && " *"}</legend>
      <div>{options.map((option) => <label key={option.value}>
        <input type="checkbox" checked={(value || []).includes(option.value)} onChange={(e) => update(e.target.checked ? [...(value || []), option.value] : value.filter((v: string) => v !== option.value))} />{option.label}
      </label>)}</div>
      {!options.length && <p className="creation-hint">{f.kind === "sources" ? "尚无资料，请在本期设置或账号素材库补充。" : "暂无可引用记录。"}</p>}
    </fieldset>;
  }
  if (f.kind === "boolean") return <label className="creation-checkbox" htmlFor={id}>
    <input id={id} type="checkbox" checked={Boolean(value)} disabled={props.disabled} onChange={(e) => update(e.target.checked)} />{f.label}{f.required && " *"}
  </label>;
  if (f.kind === "readonly") return <div className="creation-field creation-readonly">{label}<div id={id}>{typeof value === "string" ? value || "尚未记录" : JSON.stringify(value || "尚未记录")}</div></div>;
  return <div className={`creation-field ${f.kind === "richtext" ? "creation-body-field" : ""}`}>
    {label}
    {f.hint && <p id={`${id}-hint`} className="creation-hint">{f.hint}</p>}
    {f.kind === "select" || f.kind === "reviewer" || f.kind === "column" ? <select id={id} value={value || ""} disabled={props.disabled} onChange={(e) => update(f.kind === "reviewer" ? Number(e.target.value) : e.target.value)}>
      <option value="">{f.kind === "reviewer" ? "选择实际成员" : "请选择"}</option>
      {f.kind === "column" ? props.columns?.map((column) => <option key={column.id} value={column.id}>{column.name}</option>) : f.kind === "reviewer" ? props.members.map((member) => <option key={member.id} value={member.id}>{member.name}</option>) :
        f.options?.map((option) => <option key={option}>{option}</option>)}
    </select> : f.kind === "richtext" ? <RichText value={value || ""} onChange={update} label={path} disabled={props.disabled} /> :
      f.kind === "text" || f.kind === "number" ? <input id={id} type={f.kind === "number" ? "number" : "text"} min={f.kind === "number" ? 0 : undefined}
        value={value ?? ""} disabled={props.disabled} onChange={(e) => update(f.kind === "number" ? Number(e.target.value) : e.target.value)} /> :
        <textarea id={id} rows={f.kind === "material" ? 7 : 3} value={f.kind === "lines" ? (value || []).join("\n") : value || ""}
          disabled={props.disabled} onChange={(e) => update(f.kind === "lines" ? e.target.value.split("\n") : e.target.value)} />}
    {f.kind === "lines" && <small className="creation-hint">每行一条，空行保存时忽略。</small>}
    {f.kind === "material" && <label className="creation-upload">
      <Upload size={14} />{importing ? "正在提取文字…" : "导入文件文字"}
      <input type="file" aria-label={`导入${path}`} accept=".txt,.md,.csv,.docx,.pptx,.pdf" disabled={props.disabled || importing} onChange={async (e) => {
        const file = e.target.files?.[0]; if (!file) return;
        setImporting(true); setError("");
        try { const result = await contentApi.importMaterial(props.settings, file); update(result.content); }
        catch (err) { setError(err instanceof Error ? err.message : "导入失败"); }
        finally { setImporting(false); e.target.value = ""; }
      }} />
    </label>}
    {error && <p role="alert" className="creation-error-inline">{error}</p>}
  </div>;
}

function escaped(value: string) { return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function editableHtml(markdown: string) {
  return markdown.split("\n").map((line) => line === "---" ? "<hr>" : `<div>${escaped(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>") || "<br>"}</div>`).join("");
}
function markdownFromNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
  const text = Array.from(node.childNodes).map(markdownFromNode).join("");
  const tag = node.nodeName.toLowerCase();
  if (tag === "strong" || tag === "b") return `**${text}**`;
  if (tag === "em" || tag === "i") return `*${text}*`;
  if (tag === "br") return "\n";
  if (tag === "hr") return "\n---\n";
  if (tag === "li") return `- ${text.trim()}\n`;
  if (tag === "div" || tag === "p") return `${text.replace(/\n$/, "")}\n`;
  return text;
}

function RichText({ value, onChange, label, disabled }: { value: string; onChange: (v: string) => void; label: string; disabled?: boolean }) {
  const editor = useRef<HTMLDivElement>(null);
  const lastEmitted = useRef<string | null>(null);
  const [mode, setMode] = useState("edit");
  useEffect(() => {
    if (editor.current && value !== lastEmitted.current) editor.current.innerHTML = editableHtml(value);
  }, [value, mode]);
  function emit() {
    if (!editor.current) return;
    const result = Array.from(editor.current.childNodes).map(markdownFromNode).join("").trimEnd();
    lastEmitted.current = result; onChange(result);
  }
  return <div className="creation-richtext">
    <div className="creation-richtext-tools">
      <button type="button" aria-pressed={mode === "edit"} onClick={() => { lastEmitted.current = null; setMode("edit"); }}>编辑</button>
      <button type="button" aria-pressed={mode === "markdown"} onClick={() => setMode("markdown")}>Markdown</button>
      <span />
      {[{ icon: Bold, label: "加粗", command: "bold" }, { icon: Italic, label: "斜体", command: "italic" }, { icon: List, label: "列表", command: "insertUnorderedList" }].map((item) =>
        <button key={item.command} type="button" aria-label={item.label} disabled={disabled || mode !== "edit"} onMouseDown={(e) => e.preventDefault()} onClick={() => { editor.current?.focus(); document.execCommand(item.command); emit(); }}><item.icon size={15} /></button>)}
    </div>
    {mode === "edit" ? <div ref={editor} className="creation-editable" contentEditable={!disabled} suppressContentEditableWarning role="textbox" aria-label={label} aria-multiline="true" onInput={emit}
      onPaste={(e) => { e.preventDefault(); document.execCommand("insertText", false, e.clipboardData.getData("text/plain")); emit(); }} /> :
      <textarea aria-label={`${label} Markdown`} value={value} rows={16} disabled={disabled} onChange={(e) => onChange(e.target.value)} />}
  </div>;
}

export function ContentPreview({ fields, value, refs = {}, members = [] }: { fields: ContentField[]; value: ContentData; refs?: Record<string, string>; members?: ContentMember[] }) {
  return <div className="creation-document">{fields.map((f) => {
    const v = value[f.key];
    if (f.show_if && value[f.show_if[0]] !== f.show_if[1]) return null;
    if (f.kind === "boolean" || f.kind === "reviewer") return <section key={f.key}><h3>{f.label}</h3><p>{f.kind === "boolean" ? v ? "是" : "否" : members.find((m) => m.id === v)?.name || "未指定"}</p></section>;
    if (!v || Array.isArray(v) && !v.length) return null;
    return <section key={f.key}><h3>{f.label}</h3>
      {f.kind === "records" ? (v as ContentData[]).map((row, i) => <div className="creation-preview-record" key={row.id || i}><ContentPreview fields={f.fields || []} value={row} refs={refs} members={members} /></div>) :
        f.kind === "object" ? <ContentPreview fields={f.fields || []} value={v} refs={refs} members={members} /> :
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: ({ alt }) => <span>图片说明：{alt || "图片尚未制作"}</span>, a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>{Array.isArray(v) ? v.map((item) => ["sources", "claims"].includes(f.kind) ? refs[item] || "已归档引用" : item).join("\n\n") : String(v)}</ReactMarkdown>}
    </section>;
  })}</div>;
}
