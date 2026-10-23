import type { AppSettings, FeedItem, SmartChannel, TrashEntry } from "../types";

const KEYS = {
  channels: "signaldesk.channels.v1",
  feed: "signaldesk.feed.v1",
  settings: "signaldesk.settings.v1",
  trash: "signaldesk.trash.v1",
  deletedFeedIds: "signaldesk.deletedFeedIds.v1",
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
  loadChannels(fallback: SmartChannel[]): SmartChannel[] {
    return readJson(KEYS.channels, fallback);
  },
  saveChannels(channels: SmartChannel[]): void {
    writeJson(KEYS.channels, channels);
  },
  loadFeed(fallback: FeedItem[]): FeedItem[] {
    return readJson(KEYS.feed, fallback);
  },
  saveFeed(feed: FeedItem[]): void {
    writeJson(KEYS.feed, feed.slice(0, 1000));
  },
  loadTrash(): TrashEntry[] {
    const entries = readJson<TrashEntry[]>(KEYS.trash, []);
    const existingIds = new Set(entries.map((entry) => entry.id));
    const migrated = readJson<string[]>(KEYS.deletedFeedIds, [])
      .filter((id) => !existingIds.has(id))
      .map((id) => ({ id, deletedAt: Date.now() }));
    return [...entries, ...migrated];
  },
  saveTrash(entries: TrashEntry[]): void {
    writeJson(KEYS.trash, entries);
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
