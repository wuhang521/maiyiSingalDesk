import type { AppSettings, FeedItem, SmartChannel, TrashEntry } from "../types";
import { canonicalizeChannels, canonicalizeFeed, canonicalizeTrash } from "./channelIdentity";

const KEYS = {
  channels: "signaldesk.channels.v1",
  feed: "signaldesk.feed.v1",
  settings: "signaldesk.settings.v1",
  trash: "signaldesk.trash.v1",
  deletedFeedIds: "signaldesk.deletedFeedIds.v1",
  ntfyReceipts: "signaldesk.ntfy-receipts.v1",
} as const;

function readJson<T>(key: string, fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson<T>(key: string, value: T): void {
  window.localStorage.setItem(key, JSON.stringify(value));
}

export const storage = {
  loadNtfyReceipts(): string[] {
    const value = readJson<unknown>(KEYS.ntfyReceipts, []);
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string").slice(-1000) : [];
  },
  saveNtfyReceipts(receipts: string[]): void {
    try { writeJson(KEYS.ntfyReceipts, receipts.slice(-1000)); } catch { /* Session deduplication remains available if local storage is full. */ }
  },
  loadChannels(fallback: SmartChannel[], defaultServer = ""): SmartChannel[] {
    const channels = readJson(KEYS.channels, fallback);
    return canonicalizeChannels(channels, channels, defaultServer);
  },
  saveChannels(channels: SmartChannel[]): void {
    writeJson(KEYS.channels, canonicalizeChannels(channels));
  },
  loadFeed(fallback: FeedItem[]): FeedItem[] {
    return canonicalizeFeed(readJson(KEYS.feed, fallback), readJson<SmartChannel[]>(KEYS.channels, []));
  },
  saveFeed(feed: FeedItem[]): void {
    writeJson(KEYS.feed, canonicalizeFeed(feed.slice(0, 1000), readJson<SmartChannel[]>(KEYS.channels, [])));
  },
  loadTrash(): TrashEntry[] {
    const entries = readJson<TrashEntry[]>(KEYS.trash, []);
    const existingIds = new Set(entries.map((entry) => entry.id));
    const migrated = readJson<string[]>(KEYS.deletedFeedIds, [])
      .filter((id) => !existingIds.has(id))
      .map((id) => ({ id, deletedAt: Date.now() }));
    return canonicalizeTrash([...entries, ...migrated], readJson<SmartChannel[]>(KEYS.channels, []));
  },
  saveTrash(entries: TrashEntry[]): void {
    writeJson(KEYS.trash, canonicalizeTrash(entries, readJson<SmartChannel[]>(KEYS.channels, [])));
    window.localStorage.removeItem(KEYS.deletedFeedIds);
  },
  loadSettings(fallback: AppSettings): AppSettings {
    return {
      ...fallback,
      ...readJson<Partial<AppSettings>>(KEYS.settings, {}),
      backendUrl: fallback.backendUrl,
      backendAuthToken: "",
    };
  },
  saveSettings(settings: AppSettings): void {
    writeJson(KEYS.settings, { ...settings, backendAuthToken: "" });
  },
  clearAll(): void {
    Object.values(KEYS).forEach((key) => window.localStorage.removeItem(key));
  },
};
