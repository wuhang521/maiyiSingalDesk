import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { ChannelDialog } from "./components/ChannelDialog";
import { defaultSettings } from "./data/demo";
import { articleApi } from "./lib/articleApi";
import { authApi } from "./lib/authApi";
import { knowledgeApi } from "./lib/knowledgeApi";
import type { FeedItem, SmartChannel } from "./types";

vi.mock("./lib/authApi", () => ({ authApi: { me: vi.fn(), login: vi.fn(), logout: vi.fn() } }));
type BridgeEvent = Parameters<Parameters<NonNullable<Window["signalDesk"]>["onMessage"]>[0]>[0];
const testBackendUrl = "http://127.0.0.1:19998";
const channel = (id: string, extra: Partial<SmartChannel> = {}): SmartChannel => ({
  id, name: "健康管理商业情报", description: "synthetic description", prompt: "synthetic rules", topic: "health-topic",
  server: "https://relay.example.test", authToken: "", schedule: "09:00", maxItems: 100,
  includeTerms: [], excludeTerms: [], color: "green", state: "active", createdAt: 1, updatedAt: 2, ...extra,
});
const article = (id: string, channelId: string): FeedItem => ({ id, channelId, title: id, body: "synthetic original", source: "example.test", timestamp: Date.now(), tags: [], priority: 3, read: true, saved: true, feedback: "useful", stateUpdatedAt: 123 });
function bridge() {
  let listener: (event: BridgeEvent) => void = () => undefined;
  const value = {
    getAppInfo: vi.fn().mockResolvedValue({ version: "0.6.4", platform: "win32", launchAtLogin: false }),
    onMessage: vi.fn((next: typeof listener) => { listener = next; return () => undefined; }),
    subscribe: vi.fn().mockResolvedValue({ subscriptionId: "health" }), unsubscribe: vi.fn().mockResolvedValue(true),
    showNotification: vi.fn().mockResolvedValue(true), pollMessages: vi.fn().mockResolvedValue([]),
    publishMessage: vi.fn().mockResolvedValue({ id: "synthetic-publish" }),
    hideTaskWidget: vi.fn().mockResolvedValue(true),
  };
  Object.defineProperty(window, "signalDesk", { configurable: true, value });
  return { value, emit: (event: BridgeEvent) => act(() => listener(event)) };
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.spyOn(articleApi, "channelDeliveryCatalog").mockRejectedValue(Object.assign(new Error("旧版后台"), { status: 404 }));
  Object.defineProperty(window, "signalDesk", { configurable: true, value: undefined });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("starts and reloads duplicate old/new caches with one subscription and consumes alias/canonical replay once", async () => {
  localStorage.setItem("signaldesk.channels.v1", JSON.stringify([channel("demo-health", { authToken: "synthetic-local-token" }), channel("health"), channel("local-only", { name: "本机独有", state: "draft", topic: "" })]));
  localStorage.setItem("signaldesk.feed.v1", JSON.stringify([article("demo-message-retained", "demo-health"), article("daily:demo-health:old", "demo-health")]));
  const b = bridge();
  const view = render(<App />);
  await waitFor(() => expect(b.value.subscribe).toHaveBeenCalledTimes(1));
  expect(b.value.subscribe).toHaveBeenCalledWith({ server: "https://relay.example.test", topic: "health-topic", token: "synthetic-local-token", subscriptionId: "health" });
  const payload = { id: "incoming-1", event: "message", topic: "health-topic", time: Date.now() / 1000, title: "合成推送", message: "synthetic" };
  b.emit({ type: "message", subscriptionId: "demo-health", payload });
  b.emit({ type: "message", subscriptionId: "health", payload });
  b.emit({ type: "message", subscriptionId: "demo-health", payload: { ...payload, id: "retired-topic-event", topic: "retired-topic" } });
  expect(b.value.showNotification).toHaveBeenCalledTimes(1);
  const saved = JSON.parse(localStorage.getItem("signaldesk.feed.v1")!);
  expect(saved.filter((item: FeedItem) => item.id === "incoming-1")).toHaveLength(1);
  expect(saved.some((item: FeedItem) => item.id === "retired-topic-event")).toBe(false);
  expect(saved.find((item: FeedItem) => item.id === "daily:demo-health:old")).toMatchObject({ channelId: "health", read: true, saved: true, feedback: "useful", stateUpdatedAt: 123 });
  expect(saved.some((item: FeedItem) => item.id === "demo-message-retained")).toBe(true);
  view.unmount();
  render(<App />);
  await waitFor(() => expect(b.value.subscribe).toHaveBeenCalledTimes(2));
  b.emit({ type: "message", subscriptionId: "demo-health", payload });
  b.emit({ type: "message", subscriptionId: "health", payload });
  expect(b.value.showNotification).toHaveBeenCalledTimes(1);
  expect(JSON.parse(localStorage.getItem("signaldesk.channels.v1")!).map((row: SmartChannel) => row.id)).toEqual(["health", "local-only"]);
});

it("merges server aliases into an already selected local channel, keeps local-only articles, and refreshes without resubscribing", async () => {
  const user = userEvent.setup();
  const old = channel("old-import", { name: "旧健康入口", topic: "", state: "draft" });
  const custom = channel("local-only", { name: "本机频道", topic: "", state: "draft" });
  localStorage.setItem("signaldesk.channels.v1", JSON.stringify([old, custom]));
  localStorage.setItem("signaldesk.feed.v1", JSON.stringify([article("服务器保留文章", "old-import"), article("本机独有文章", "local-only")]));
  const session = { backendUrl: testBackendUrl, accessToken: "synthetic-only", expiresAt: "2027-12-01T00:00:00Z", user: { id: 10, username: "synthetic", displayName: "合成用户", staffId: 10, role: "member" as const } };
  vi.mocked(authApi.me).mockResolvedValue(session.user);
  vi.spyOn(knowledgeApi, "capabilities").mockResolvedValue({ notifications_available: false, asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive" });
  vi.spyOn(articleApi, "upsertChannel").mockResolvedValue({ id: "health" });
  vi.spyOn(articleApi, "syncArticles").mockResolvedValue({ accepted: 0 });
  const list = vi.spyOn(articleApi, "listArticles").mockResolvedValue({ items: [{ ...article("服务器保留文章", "health"), read: false, saved: false, feedback: null, stateUpdatedAt: 100, deletedAt: null, purged: false }], total: 1 });
  let resolve!: (channels: SmartChannel[]) => void;
  const remote = channel("health", { aliases: ["demo-health", "old-import"], managed: true });
  const channels = vi.spyOn(articleApi, "listChannels").mockImplementationOnce(() => new Promise(done => { resolve = done; })).mockResolvedValue([remote]);
  const b = bridge();
  Object.assign(window.signalDesk!, { loadAuthSession: vi.fn().mockResolvedValue(session), clearAuthSession: vi.fn().mockResolvedValue(true) });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "旧健康入口 示例" }));
  await waitFor(() => expect(channels).toHaveBeenCalledTimes(1));
  await act(async () => resolve([remote]));
  await waitFor(() => expect(b.value.subscribe).toHaveBeenCalledTimes(1));
  expect(screen.getAllByText("服务器保留文章").length).toBeGreaterThan(0);
  const stored = JSON.parse(localStorage.getItem("signaldesk.channels.v1")!);
  expect(stored.map((row: SmartChannel) => row.id)).toEqual(["health", "local-only"]);
  expect(JSON.parse(localStorage.getItem("signaldesk.feed.v1")!).some((item: FeedItem) => item.id === "本机独有文章")).toBe(true);
  expect(JSON.parse(localStorage.getItem("signaldesk.feed.v1")!).find((item: FeedItem) => item.id === "服务器保留文章")).toMatchObject({ channelId: "health", read: true, saved: true, feedback: "useful", stateUpdatedAt: 123 });
  await user.click(screen.getByRole("button", { name: "同步" }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  expect(b.value.subscribe).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "智能频道" }));
  expect(screen.getByText("每天北京时间 09:00（服务器推送）")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "暂停" })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: "删除" })[0]).toBeDisabled();
});

it("allows only local credential edits for a managed channel and preserves its server fields", async () => {
  const user = userEvent.setup(), save = vi.fn();
  const managed = channel("health", { aliases: ["demo-health"], managed: true, authToken: "old-synthetic" });
  render(<ChannelDialog open channel={managed} settings={defaultSettings} onClose={() => undefined} onSave={save} />);
  expect(screen.getByRole("note")).toHaveTextContent("每天北京时间 09:00");
  for (const name of ["频道名称", "每天推送时间", "频道说明", "必须关注", "排除内容", "每日最多", "识别颜色", "保存后启用", "服务器", "Topic"]) expect(screen.getByLabelText(name)).toBeDisabled();
  fireEvent.change(screen.getByLabelText("访问令牌"), { target: { value: "new-synthetic" } });
  await user.click(screen.getByRole("button", { name: "保存频道" }));
  expect(save).toHaveBeenCalledWith({ ...managed, authToken: "new-synthetic", authTokenServer: managed.server, authTokenTopic: managed.topic });
});

async function statusWorkspace(notifications = true) {
  const managed = channel("health", { aliases: ["demo-health"], managed: true });
  const custom = channel("custom", { name: "其他频道", topic: "custom-topic" });
  const kept = article("保留原有文章", "health"), trashed = article("保留回收站", "health");
  const deletedAt = Date.now();
  localStorage.setItem("signaldesk.settings.v1", JSON.stringify({ notifications }));
  localStorage.setItem("signaldesk.channels.v1", JSON.stringify([managed, custom]));
  localStorage.setItem("signaldesk.feed.v1", JSON.stringify([kept]));
  localStorage.setItem("signaldesk.trash.v1", JSON.stringify([{ id: trashed.id, item: trashed, deletedAt }]));
  const session = { backendUrl: testBackendUrl, accessToken: "synthetic-only", expiresAt: "2027-12-01T00:00:00Z", user: { id: 10, username: "synthetic", displayName: "合成用户", staffId: 10, role: "member" as const } };
  vi.mocked(authApi.me).mockResolvedValue(session.user);
  vi.spyOn(knowledgeApi, "capabilities").mockResolvedValue({ notifications_available: false, asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive" });
  vi.spyOn(articleApi, "upsertChannel").mockResolvedValue({ id: "health" });
  const sync = vi.spyOn(articleApi, "syncArticles").mockResolvedValue({ accepted: 0 });
  const batch = vi.spyOn(articleApi, "batchArticles").mockImplementation(async (_settings, items) => ({ accepted: items.length, idMap: Object.fromEntries(items.map(item => [item.id, item.id])) }));
  const list = vi.spyOn(articleApi, "listArticles").mockResolvedValue({ items: [{ ...kept, stateUpdatedAt: 123, deletedAt: null, purged: false }, { ...trashed, stateUpdatedAt: 123, deletedAt, purged: false }], total: 2 });
  vi.spyOn(articleApi, "listChannels").mockResolvedValue([managed, custom]);
  const b = bridge();
  Object.assign(window.signalDesk!, { loadAuthSession: vi.fn().mockResolvedValue(session), clearAuthSession: vi.fn().mockResolvedValue(true) });
  render(<App />);
  await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(b.value.subscribe).toHaveBeenCalledTimes(2));
  return { ...b, sync, batch, list, kept, trashed, deletedAt };
}

it("shows a health delay status once without creating or syncing articles; other channel messages keep their behavior", async () => {
  const b = await statusWorkspace();
  const beforeFeed = localStorage.getItem("signaldesk.feed.v1"), beforeTrash = localStorage.getItem("signaldesk.trash.v1");
  const synced = b.sync.mock.calls.length;
  const payload = { id: "synthetic-status", event: "message", topic: "health-topic", time: Date.now() / 1000, title: "internal-fingerprint-abcdef", message: "今天的健康情报仍在准备，完成后会继续推送。", tags: ["health_status"] };
  b.emit({ type: "message", subscriptionId: "demo-health", payload });
  b.emit({ type: "message", subscriptionId: "health", payload });
  expect(b.value.showNotification).toHaveBeenCalledTimes(1);
  expect(b.value.showNotification).toHaveBeenCalledWith({ title: "健康情报推送状态", body: payload.message, messageId: "health:status", silent: true });
  expect(screen.getByText("健康情报推送状态")).toBeInTheDocument();
  expect(screen.queryByText(payload.title)).not.toBeInTheDocument();
  expect(b.batch).not.toHaveBeenCalled();
  expect(b.sync).toHaveBeenCalledTimes(synced);
  expect(localStorage.getItem("signaldesk.feed.v1")).toBe(beforeFeed);
  expect(localStorage.getItem("signaldesk.trash.v1")).toBe(beforeTrash);
  b.emit({ type: "message", subscriptionId: "health", payload: { ...payload, id: "ordinary-health", title: "普通健康文章", tags: [] } });
  b.emit({ type: "message", subscriptionId: "custom", payload: { ...payload, id: "other-channel", topic: "custom-topic", title: "其他频道原行为" } });
  expect(b.batch).toHaveBeenCalledTimes(2);
  await waitFor(() => expect(b.value.showNotification).toHaveBeenCalledTimes(3));
  const feed: FeedItem[] = JSON.parse(localStorage.getItem("signaldesk.feed.v1")!);
  expect(feed.some(item => item.id === "synthetic-status")).toBe(false);
  expect(feed.find(item => item.id === "ordinary-health")?.channelId).toBe("health");
  expect(feed.find(item => item.id === "other-channel")?.channelId).toBe("custom");
});

it("respects the notifications switch for health delay status without touching existing articles", async () => {
  const b = await statusWorkspace(false);
  const beforeFeed = localStorage.getItem("signaldesk.feed.v1"), beforeTrash = localStorage.getItem("signaldesk.trash.v1");
  b.emit({ type: "message", subscriptionId: "health", payload: { id: "status-muted", event: "message", topic: "health-topic", time: Date.now() / 1000, title: "internal-status", message: "服务器正在准备", tags: ["health_status"] } });
  expect(b.value.showNotification).not.toHaveBeenCalled();
  expect(screen.queryByText("健康情报推送状态")).not.toBeInTheDocument();
  expect(b.batch).not.toHaveBeenCalled();
  expect(localStorage.getItem("signaldesk.feed.v1")).toBe(beforeFeed);
  expect(localStorage.getItem("signaldesk.trash.v1")).toBe(beforeTrash);
});

it("authenticates a managed relay only after login, checks reception with GET and never publishes or leaks tokens to ntfy", async () => {
  const user = userEvent.setup();
  const managed = channel("health", { managed: true, readViaApi: true, aliases: ["demo-health"], authToken: "synthetic-private-ntfy" });
  localStorage.setItem("signaldesk.channels.v1", JSON.stringify([managed, { ...managed, id: "demo-health" }]));
  localStorage.setItem("signaldesk.settings.v1", JSON.stringify({ historyWindow: "all" }));
  const session = { backendUrl: "http://127.0.0.1:19198/app", accessToken: "synthetic-personal", expiresAt: "2027-12-01T00:00:00Z", user: { id: 11, username: "synthetic", displayName: "合成用户", staffId: 11, role: "member" as const } };
  let resolve!: (user: typeof session.user) => void;
  vi.mocked(authApi.me).mockImplementation(() => new Promise(done => { resolve = done; }));
  vi.spyOn(knowledgeApi, "capabilities").mockResolvedValue({ notifications_available: false, asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive" });
  vi.spyOn(articleApi, "upsertChannel").mockResolvedValue({ id: "health" });
  vi.spyOn(articleApi, "syncArticles").mockResolvedValue({ accepted: 0 });
  vi.spyOn(articleApi, "batchArticles").mockImplementation(async (_settings, items) => ({ accepted: items.length, idMap: Object.fromEntries(items.map(item => [item.id, item.id])) }));
  vi.spyOn(articleApi, "listArticles").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(articleApi, "listChannels").mockResolvedValue([managed]);
  const b = bridge();
  Object.assign(window.signalDesk!, { loadAuthSession: vi.fn().mockResolvedValue(session), clearAuthSession: vi.fn().mockResolvedValue(true) });
  render(<App />);
  await waitFor(() => expect(authApi.me).toHaveBeenCalled());
  expect(b.value.subscribe).not.toHaveBeenCalled();
  await act(async () => resolve(session.user));
  const connection = { server: session.backendUrl + "/api/v1/health-relay", topic: "health", token: session.accessToken };
  await waitFor(() => expect(b.value.subscribe).toHaveBeenCalledTimes(1));
  expect(b.value.subscribe).toHaveBeenCalledWith({ ...connection, subscriptionId: "health" });
  const payload = { id: "relay-message", event: "message", topic: "health", time: Date.now() / 1000, title: "转发接收测试", message: "synthetic" };
  b.emit({ type: "message", subscriptionId: "demo-health", payload });
  b.emit({ type: "message", subscriptionId: "health", payload });
  b.emit({ type: "message", subscriptionId: "health", payload: { ...payload, id: "old-direct-topic", topic: managed.topic } });
  await waitFor(() => expect(b.value.showNotification).toHaveBeenCalledTimes(1));
  await user.click(screen.getByRole("button", { name: "智能频道" }));
  await user.click(screen.getByRole("button", { name: "检查接收" }));
  expect(b.value.pollMessages).toHaveBeenCalledWith({ ...connection, since: "10m" });
  expect(await screen.findByText("接收链路正常")).toBeInTheDocument();
  b.value.pollMessages.mockRejectedValueOnce(new Error("登录已失效，请重新登录"));
  await user.click(screen.getByRole("button", { name: "检查接收" }));
  expect(await screen.findByText("接收检查失败")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "同步" }));
  await waitFor(() => expect(b.value.pollMessages).toHaveBeenCalledWith({ ...connection, since: "24h" }));
  expect(b.value.publishMessage).not.toHaveBeenCalled();
  expect(b.value.pollMessages.mock.calls.every(([config]) => config.server === connection.server && config.token === session.accessToken)).toBe(true);
  await user.click(screen.getByRole("button", { name: "查看受管配置" }));
  expect(screen.queryByLabelText("访问令牌")).not.toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("历史文章从服务器同步");
  await user.click(screen.getByRole("button", { name: "完成" }));
  const local: SmartChannel[] = JSON.parse(localStorage.getItem("signaldesk.channels.v1")!);
  expect(local[0]).toMatchObject({ server: managed.server, topic: managed.topic, authToken: "synthetic-private-ntfy" });
});

it("waits for real stream status instead of treating subscribe acknowledgement as a connection", async () => {
  const user = userEvent.setup();
  localStorage.setItem("signaldesk.channels.v1", JSON.stringify([channel("health")]));
  const b = bridge();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "智能频道" }));
  await waitFor(() => expect(b.value.subscribe).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("运行中")).not.toBeInTheDocument();
  b.emit({ type: "status", subscriptionId: "health", status: "reconnecting", error: "HTTP401" });
  expect(screen.getByText("重连中")).toBeInTheDocument();
  b.emit({ type: "status", subscriptionId: "health", status: "connected" });
  expect(screen.getByText("运行中")).toBeInTheDocument();
  b.emit({ type: "status", subscriptionId: "health", status: "reconnecting", error: "正常EOF" });
  expect(screen.getByText("重连中")).toBeInTheDocument();
  b.emit({ type: "status", subscriptionId: "health", status: "connected" });
  expect(screen.getByText("运行中")).toBeInTheDocument();
});

it("waits for managed health ID resolution, coalesces in-flight replays and saves read/collection by the actual original ID", async () => {
  const user = userEvent.setup(), b = await statusWorkspace();
  let resolve!: (value: { accepted: number; idMap: Record<string, string> }) => void;
  b.batch.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const state = vi.spyOn(articleApi, "updateState").mockImplementation(async (_settings, id, update) => ({ ...article(id, "health"), ...update, deletedAt: null, purged: false }));
  const payload = { id: "wire-id", event: "message", topic: "health-topic", time: Date.now() / 1000, title: "服务器实际编号文章", message: "合成内容" };
  b.emit({ type: "message", subscriptionId: "health", payload });
  b.emit({ type: "message", subscriptionId: "demo-health", payload });
  expect(b.batch).toHaveBeenCalledTimes(1);
  expect(b.batch.mock.calls[0][3]).toBe(true);
  expect(b.value.showNotification).not.toHaveBeenCalled();
  expect(localStorage.getItem("signaldesk.feed.v1")).not.toContain("wire-id");
  await act(async () => resolve({ accepted: 1, idMap: { "wire-id": "daily:health:actual" } }));
  await waitFor(() => expect(b.value.showNotification).toHaveBeenCalledTimes(1));
  expect(b.value.showNotification.mock.calls[0][0].messageId).toBe("daily:health:actual");
  expect(JSON.parse(localStorage.getItem("signaldesk.feed.v1")!).filter((item: FeedItem) => item.title === payload.title).map((item: FeedItem) => item.id)).toEqual(["daily:health:actual"]);
  await user.click(screen.getByRole("button", { name: /^全部信息/ }));
  await user.click(screen.getByText(payload.title));
  await user.click(screen.getAllByRole("button", { name: "收藏" }).at(-1)!);
  expect(state.mock.calls.every(([, id]) => id === "daily:health:actual")).toBe(true);
  expect(state.mock.calls.some(([, , update]) => update.read === true)).toBe(true);
  expect(state.mock.calls.some(([, , update]) => update.saved === true)).toBe(true);
});

it("keeps an unsaved normal message with a clear error then remaps its cached ID during manual sync", async () => {
  const user = userEvent.setup(), b = await statusWorkspace();
  b.batch.mockRejectedValueOnce(new Error("Synthetic offline"));
  const payload = { id: "wire-offline", event: "message", topic: "health-topic", time: Date.now() / 1000, title: "暂未同步的普通文章", message: "本机保留" };
  b.emit({ type: "message", subscriptionId: "health", payload });
  expect(await screen.findByText("文章暂未同步")).toBeInTheDocument();
  await waitFor(() => expect(b.value.showNotification).toHaveBeenCalledTimes(1));
  expect(JSON.parse(localStorage.getItem("signaldesk.feed.v1")!).some((item: FeedItem) => item.id === payload.id)).toBe(true);
  b.value.pollMessages.mockResolvedValue([payload]);
  b.batch.mockResolvedValue({ accepted: 1, idMap: { [payload.id]: "daily:health:recovered" } });
  b.list.mockResolvedValue({ items: [
    { ...b.kept, stateUpdatedAt: 123, deletedAt: null, purged: false }, { ...b.trashed, stateUpdatedAt: 123, deletedAt: b.deletedAt, purged: false },
    { ...article("daily:health:recovered", "health"), title: payload.title, body: payload.message, read: false, saved: false, stateUpdatedAt: 0, deletedAt: null, purged: false },
  ], total: 3 });
  await user.click(screen.getByRole("button", { name: "同步" }));
  expect(await screen.findByText("同步完成")).toBeInTheDocument();
  const feed: FeedItem[] = JSON.parse(localStorage.getItem("signaldesk.feed.v1")!);
  expect(feed.some(item => item.id === payload.id)).toBe(false);
  expect(feed.filter(item => item.title === payload.title).map(item => item.id)).toEqual(["daily:health:recovered"]);
  expect(feed.find(item => item.id === b.kept.id)).toMatchObject({ read: true, saved: true, feedback: "useful" });
  expect(b.value.showNotification).toHaveBeenCalledTimes(1);
});

it("drops an ID-resolution response that completes after logout changed the backend credential", async () => {
  const user = userEvent.setup(), b = await statusWorkspace();
  vi.mocked(authApi.logout).mockResolvedValue(undefined);
  let resolve!: (value: { accepted: number; idMap: Record<string, string> }) => void;
  b.batch.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  b.emit({ type: "message", subscriptionId: "health", payload: { id: "retired-login-wire", event: "message", topic: "health-topic", time: Date.now() / 1000, title: "不能迟到写入", message: "合成" } });
  await user.click(screen.getByRole("button", { name: "设置" }));
  await user.click(screen.getByRole("button", { name: "退出登录" }));
  expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeInTheDocument();
  await act(async () => resolve({ accepted: 1, idMap: { "retired-login-wire": "daily:health:retired-login" } }));
  expect(b.value.showNotification).not.toHaveBeenCalled();
  expect(localStorage.getItem("signaldesk.feed.v1")).not.toContain("retired-login");
});

it("resolves a historical wire ID to an existing trashed original without resurrecting its article or changing state", async () => {
  const user = userEvent.setup(), b = await statusWorkspace();
  const beforeTrash = localStorage.getItem("signaldesk.trash.v1");
  const payload = { id: "trashed-wire", event: "message", topic: "health-topic", time: Date.now() / 1000, title: b.trashed.title, message: b.trashed.body };
  b.value.pollMessages.mockResolvedValue([payload]);
  b.batch.mockResolvedValue({ accepted: 1, idMap: { [payload.id]: b.trashed.id } });
  await user.click(screen.getByRole("button", { name: "同步" }));
  expect(await screen.findByText("同步完成")).toBeInTheDocument();
  expect(localStorage.getItem("signaldesk.trash.v1")).toBe(beforeTrash);
  const feed: FeedItem[] = JSON.parse(localStorage.getItem("signaldesk.feed.v1")!);
  expect(feed.some(item => [payload.id, b.trashed.id].includes(item.id))).toBe(false);
  expect(b.value.showNotification).not.toHaveBeenCalled();
});
