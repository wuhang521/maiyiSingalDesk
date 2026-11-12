import { useEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { memberApi, type ManagedMember, type WechatPairing } from "../lib/memberApi";
import type { AppSettings } from "../types";

interface Props {
  member: ManagedMember;
  settings: AppSettings;
  onClose: () => void;
  onBound: () => void;
}

const POLL_INTERVAL = 2000;

export function WechatPairingDialog({ member, settings, onClose, onBound }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [attempt, setAttempt] = useState(0);
  const [pairing, setPairing] = useState<WechatPairing>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    let timer: ReturnType<typeof setTimeout>;

    function poll(initial: WechatPairing) {
      timer = setTimeout(async () => {
        try {
          const result = await memberApi.readWechatPairing(settings, member.id, initial.id, signal);
          if (signal.aborted) return;
          setPairing({ ...result, scan_url: initial.scan_url });
          setError("");
          if (result.state === "waiting") poll(initial);
        } catch (caught) {
          if (signal.aborted) return;
          if (Date.now() >= Date.parse(initial.expires_at)) {
            setPairing({ ...initial, state: "expired" });
            setError("");
          } else {
            setError(message(caught));
            poll(initial);
          }
        }
      }, POLL_INTERVAL);
    }

    // React's development effect replay must not create a second one-time code.
    queueMicrotask(async () => {
      if (signal.aborted) return;
      try {
        const result = await memberApi.startWechatPairing(settings, member.id, signal);
        if (signal.aborted) return;
        setPairing(result);
        poll(result);
      } catch (caught) {
        if (!signal.aborted) setError(message(caught));
      }
    });
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [attempt, member.id, settings]);

  useEffect(() => {
    if (pairing?.state !== "ready" || saving) return;
    const timer = setTimeout(() => {
      setPairing({ ...pairing, state: "expired" });
      setError("");
    }, Math.max(0, Date.parse(pairing.expires_at) - Date.now()));
    return () => clearTimeout(timer);
  }, [pairing, saving]);

  function restart() {
    setPairing(undefined);
    setError("");
    setAttempt((current) => current + 1);
  }

  function close() {
    if (savingRef.current) return;
    if (pairing && pairing.state !== "confirmed" && pairing.state !== "expired") {
      void memberApi.cancelWechatPairing(settings, member.id, pairing.id).catch(() => undefined);
    }
    onClose();
  }

  async function confirm() {
    if (pairing?.state !== "ready" || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError("");
    try {
      await memberApi.confirmWechatPairing(settings, member.id, pairing.id);
      onBound();
    } catch (caught) {
      setError(message(caught));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  return <dialog ref={dialog} className="channel-dialog channel-delivery-dialog wechat-pairing-dialog"
    aria-labelledby="wechat-pairing-title" onCancel={(event) => { event.preventDefault(); close(); }}>
    <header className="dialog-header">
      <h2 id="wechat-pairing-title">扫码绑定微信</h2>
      <button className="icon-button subtle" type="button" aria-label="关闭" onClick={close} disabled={saving}><X size={18} /></button>
    </header>
    <div className="dialog-body">
      <p className="wechat-pairing-member">绑定成员 <strong>{member.name}</strong><span>{member.user?.username}</span></p>
      {pairing?.state === "waiting" && pairing.scan_url && <>
        <div className="wechat-pairing-code">
          <QRCodeSVG value={pairing.scan_url} size={208} marginSize={4} title="微信绑定二维码" role="img" />
        </div>
        <ol className="wechat-pairing-steps">
          <li>打开微信扫一扫，扫描上方二维码。</li>
          <li>复制页面上的绑定指令，发给你正在使用的 Hermes 机器人。</li>
          <li>微信验证后，在电脑上确认绑定。</li>
        </ol>
        <p className="wechat-pairing-status" role="status">等待微信验证。二维码 5 分钟内有效。</p>
      </>}
      {pairing?.state === "ready" && <div className="wechat-pairing-ready" role="status">
        <strong>微信已验证</strong>
        <code>{pairing.recipient_hint}</code>
        <p>{pairing.replacing ? "确认后将更换接收微信。请重新订阅需要接收的频道。" : "确认将此微信绑定给该成员。"}</p>
      </div>}
      {pairing?.state === "expired" && <p role="status">二维码已过期，请重新生成。</p>}
      {!pairing && !error && <p role="status">正在生成二维码…</p>}
      {error && <p className="member-feedback error" role="alert">{error}</p>}
    </div>
    <footer className="dialog-footer">
      <button className="secondary-button" type="button" onClick={close} disabled={saving}>取消</button>
      {(pairing || error) && <button className="secondary-button" type="button" onClick={restart} disabled={saving}>重新生成</button>}
      {pairing?.state === "ready" && <button className="primary-button" type="button" disabled={saving} onClick={() => void confirm()}>
        <Check size={16} />{saving ? "正在绑定…" : "确认绑定"}
      </button>}
    </footer>
  </dialog>;
}

function message(error: unknown) { return error instanceof Error ? error.message : "微信绑定暂不可用，请稍后重试"; }
