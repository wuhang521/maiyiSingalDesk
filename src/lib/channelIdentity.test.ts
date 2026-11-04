import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FeedItem, SmartChannel, TrashEntry } from "../types";
import { defaultSettings } from "../data/demo";
import { articleApi } from "./articleApi";
import { canonicalChannelId, canonicalizeChannels, canonicalizeFeed, canonicalizeTrash, mergeChannelSnapshot } from "./channelIdentity";
import { channelConnection, channelHistoryWindow, mapNtfyMessageItems, usesHealthRelay } from "./ntfy";
import { storage } from "./storage";

const channel = (id: string, extra: Partial<SmartChannel> = {}): SmartChannel => ({
  id, name: "健康管理商业情报", description: "synthetic", prompt: "synthetic rules", topic: "health-topic",
  server: "https://relay.example.test", authToken: "", schedule: "09:00", maxItems: 100,
  includeTerms: [], excludeTerms: [], color: "green", state: "active", createdAt: 1, updatedAt: 2, ...extra,
});
const article = (id: string, channelId = "demo-health"): FeedItem => ({
  id, channelId, title: id, body: "synthetic original", source: "example.test", timestamp: Date.now(), tags: [],
  priority: 3, read: true, saved: true, feedback: "useful", stateUpdatedAt: 123,
});

describe("health channel identity compatibility", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it.each([false, true])("normalizes before the first POST/subscription in either cache order (%s)", reverse => {
    const old = channel("demo-health", { state: "draft", authToken: "synthetic-local-token", lastSyncAt: 20 });
    const current = channel("health", { managed: true, lastSyncAt: 10 });
    const rows = canonicalizeChannels(reverse ? [current, old] : [old, current]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: "health", topic: "health-topic", state: "active", managed: true, authToken: "synthetic-local-token", lastSyncAt: 20 });
    expect(rows[0].aliases).toContain("demo-health");
    expect(channelConnection(rows[0], defaultSettings)?.token).toBe("synthetic-local-token");
  });

  it("applies server-declared aliases while preserving unrelated same-name local channels", () => {
    const local = [channel("old-import", { authToken: "synthetic-local-token" }), channel("private-local", { topic: "", name: "健康管理商业情报" })];
    const remote = [channel("health", { aliases: ["demo-health", "old-import"], managed: true, description: "server authoritative" })];
    const rows = mergeChannelSnapshot(local, remote);
    expect(rows.map(row => row.id)).toEqual(["health", "private-local"]);
    expect(rows[0]).toMatchObject({ description: "server authoritative", managed: true, authToken: "synthetic-local-token" });
    expect(canonicalChannelId("old-import", rows)).toBe("health");
  });

  it("retains a local token at rest but does not send it to a changed server or topic", () => {
    const local = [channel("demo-health", { authToken: "synthetic-private-token" })];
    const remote = [channel("health", { managed: true, server: "https://new.example.test", topic: "new-topic" })];
    const merged = mergeChannelSnapshot(local, remote)[0];
    expect(merged.authToken).toBe("synthetic-private-token");
    expect(channelConnection(merged, { defaultServer: "", globalAuthToken: "" })?.token).toBeUndefined();
    expect(channelConnection({ ...merged, server: local[0].server, topic: local[0].topic }, defaultSettings)?.token).toBe("synthetic-private-token");
  });

  it("binds a legacy default-server credential before a remote snapshot changes its address", () => {
    localStorage.setItem("signaldesk.channels.v1", JSON.stringify([channel("demo-health", { server: "", authToken: "synthetic-default-token" })]));
    const local = storage.loadChannels([], "https://old-default.example.test");
    expect(channelConnection(local[0], { defaultServer: "https://old-default.example.test", globalAuthToken: "" })?.token).toBe("synthetic-default-token");
    const remote = mergeChannelSnapshot(local, [channel("health", { server: "https://new-default.example.test", managed: true })]);
    expect(remote[0].authToken).toBe("synthetic-default-token");
    expect(channelConnection(remote[0], { defaultServer: "https://new-default.example.test", globalAuthToken: "" })?.token).toBeUndefined();
  });

  it("migrates and reloads all cached article identities without changing user states or tombstones", () => {
    const items = [article("daily:demo-health:day:1"), article("demo-message-history")];
    const trash: TrashEntry[] = [{ id: "trashed", deletedAt: Date.now(), item: article("trashed") }, { id: "purged", deletedAt: 10, purged: true }];
    localStorage.setItem("signaldesk.channels.v1", JSON.stringify([channel("demo-health"), channel("health")]));
    localStorage.setItem("signaldesk.feed.v1", JSON.stringify(items));
    localStorage.setItem("signaldesk.trash.v1", JSON.stringify(trash));
    const channels = storage.loadChannels([]), feed = storage.loadFeed([]), recoveredTrash = storage.loadTrash();
    expect(channels.map(row => row.id)).toEqual(["health"]);
    expect(feed).toEqual(items.map(item => ({ ...item, channelId: "health" })));
    expect(recoveredTrash).toEqual([{ ...trash[0], item: { ...trash[0].item!, channelId: "health" } }, trash[1]]);
    storage.saveChannels(channels); storage.saveFeed(feed); storage.saveTrash(recoveredTrash);
    expect(storage.loadChannels([])).toEqual(channels);
    expect(storage.loadFeed([])).toEqual(feed);
    expect(storage.loadTrash()).toEqual(recoveredTrash);
    expect(canonicalizeTrash([{ id: "purged", deletedAt: 10, purged: true }])).toEqual([{ id: "purged", deletedAt: 10, purged: true }]);
  });

  it("normalizes API writes and excludes local credentials, server metadata and demo articles", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: "health", accepted: 1 }) } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const settings = { ...defaultSettings, backendAuthToken: "synthetic-api-token", operatorName: "synthetic-operator" };
    const managed = channel("health", { aliases: ["demo-health", "old-import"], managed: true, authToken: "local-only", authTokenServer: "private", authTokenTopic: "private" });
    await articleApi.upsertChannel(settings, { ...managed, id: "demo-health" });
    await articleApi.batchArticles(settings, [article("real", "old-import"), article("demo-message-ignore")], [managed]);
    await articleApi.syncArticles(settings, [article("real", "old-import"), article("demo-message-ignore")], [{ id: "trash", deletedAt: 1234, item: article("trash") }], [managed]);
    const bodies = fetchMock.mock.calls.map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies[0].id).toBe("health");
    for (const key of ["authToken", "authTokenServer", "authTokenTopic", "managed", "aliases"]) expect(bodies[0]).not.toHaveProperty(key);
    expect(bodies[1].items).toHaveLength(1);
    expect(bodies[1].items[0]).toMatchObject({ id: "real", channelId: "health" });
    expect(bodies[2].items.map((item: FeedItem) => item.channelId)).toEqual(["health", "health"]);
    expect(bodies[2].items[0]).toMatchObject({ read: true, saved: true, feedback: "useful", stateUpdatedAt: 123 });
    expect(bodies[2].items[1].deletedAt).toBe(1234);
  });

  it("maps a late alias ntfy event to the canonical channel without renaming its message id", () => {
    const rows = mapNtfyMessageItems(channel("demo-health"), { id: "ntfy-synthetic", topic: "health-topic", event: "message", time: 100, message: "synthetic" });
    expect(rows[0]).toMatchObject({ id: "ntfy-synthetic", channelId: "health" });
    expect(canonicalizeFeed([article("other", "other")])[0].channelId).toBe("other");
  });

  it("excludes health delay status from historical poll mapping while keeping ordinary messages", () => {
    const status = { id: "historical-health-status", topic: "health-topic", event: "message", time: 100, message: "正在准备", tags: ["health_status"] };
    expect(mapNtfyMessageItems(channel("health"), status)).toEqual([]);
    expect(mapNtfyMessageItems(channel("demo-health"), status)).toEqual([]);
    expect(mapNtfyMessageItems(channel("ordinary"), status)).toHaveLength(1);
    expect(mapNtfyMessageItems(channel("health"), { ...status, tags: [] })).toHaveLength(1);
  });

  it("uses only the configured authenticated API for an explicitly managed health relay", () => {
    const managed = channel("health", { managed: true, readViaApi: true, server: "https://untrusted-row.example.test", authToken: "private-ntfy-token" });
    const settings = { ...defaultSettings, backendUrl: "https://configured-api.example.test/app///", backendAuthToken: "personal-only" };
    expect(usesHealthRelay(managed)).toBe(true);
    expect(channelConnection(managed, settings)).toEqual({ server: "https://configured-api.example.test/app/api/v1/health-relay", topic: "health", token: "personal-only" });
    expect(channelConnection({ ...managed, id: "demo-health" }, settings)).toEqual(channelConnection(managed, settings));
    expect(channelConnection(managed, { ...settings, backendAuthToken: "" })).toBeNull();
    expect(channelConnection(managed, { ...settings, backendUrl: "" })).toBeNull();
    expect(channelConnection(managed, { ...settings, backendUrl: "https://person@other.example.test" })).toBeNull();
    expect(channelConnection({ ...managed, id: "ordinary" }, settings)?.token).toBe("private-ntfy-token");
    expect(channelConnection({ ...managed, managed: false }, settings)?.server).toBe(managed.server);
    expect(channelConnection({ ...managed, readViaApi: false }, settings)?.server).toBe(managed.server);
  });

  it("limits only relay cache reads while leaving ordinary channel windows unchanged", () => {
    const managed = channel("health", { managed: true, readViaApi: true });
    for (const since of ["3d", "7d", "all"]) {
      expect(channelHistoryWindow(managed, since)).toBe("24h");
      expect(channelHistoryWindow(channel("ordinary"), since)).toBe(since);
    }
    for (const since of ["10m", "12h", "24h"]) expect(channelHistoryWindow(managed, since)).toBe(since);
  });

  it("opts into canonical article IDs without changing the default batch response and rejects missing resolution", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ accepted: 1 }) } as Response)
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ accepted: 1, idMap: { wire: "daily:health:actual" } }) } as Response)
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ accepted: 1 }) } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const settings = { ...defaultSettings, backendAuthToken: "synthetic-personal" };
    expect(await articleApi.batchArticles(settings, [article("wire")])).toEqual({ accepted: 1 });
    expect(await articleApi.batchArticles(settings, [article("wire")], [], true)).toEqual({ accepted: 1, idMap: { wire: "daily:health:actual" } });
    await expect(articleApi.batchArticles(settings, [article("wire")], [], true)).rejects.toThrow("服务器未返回文章保存编号");
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/articles\/batch$/);
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/articles\/batch\?resolve_ids=true$/);
  });
});
