import { useEffect } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi } from "../lib/knowledgeApi";

// Mount only for the authenticated profile. Neither message bodies nor tokens are persisted.
export function KnowledgeNotificationListener({ settings, userId }: { settings: AppSettings; userId: number }) {
  useEffect(() => {
    if (!window.signalDesk || !settings.notifications || !settings.backendAuthToken) return;
    const controller = new AbortController(); let available: boolean | undefined; let running = false;
    const storageKey = `knowledge-notifications:${settings.backendUrl}:${userId}`;
    let seen = new Set<string>();
    try { const old = JSON.parse(localStorage.getItem(storageKey) || "[]"); if (Array.isArray(old)) seen = new Set(old.filter(v => typeof v === "string").slice(-1000)); } catch { /* Private storage unavailable: session-only deduplication. */ }
    async function refresh() {
      if (running || controller.signal.aborted || available === false) return;
      running = true;
      try {
        if (available === undefined) available = (await knowledgeApi.capabilities(settings)).notifications_available === true;
        if (!available || controller.signal.aborted) return;
        const prefs = await knowledgeApi.preferences(settings, controller.signal);
        if (!prefs.desktop_notifications) return;
        const result = await knowledgeApi.notifications(settings, controller.signal);
        for (const n of result.items.slice().reverse()) {
          if (controller.signal.aborted) return;
          if (seen.has(n.id)) continue;
          seen.add(n.id); seen = new Set(Array.from(seen).slice(-1000));
          try { localStorage.setItem(storageKey, JSON.stringify(Array.from(seen))); } catch { /* session-only */ }
          await window.signalDesk!.showNotification({ title: "我的知识库", body: n.message, messageId: "knowledge:inbox", silent: !settings.notificationSound });
          // OS delivery is not a read acknowledgement and does not retry Hermes delivery.
        }
      } catch { /* Inbox remains available; avoid repeated OS error notifications. */ }
      finally { running = false; }
    }
    void refresh(); const timer = window.setInterval(() => void refresh(), 30000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [settings.backendUrl, settings.backendAuthToken, settings.notifications, settings.notificationSound, userId]);
  return null;
}
