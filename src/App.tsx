import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import mayiLogo from "./assets/mayi-logo.webp";
import { ChannelDialog } from "./components/ChannelDialog";
import { ChannelsView } from "./components/ChannelsView";
import { ContentGenerationView } from "./components/ContentGenerationView";
import { MessageWorkspace } from "./components/MessageWorkspace";
import { LoginView } from "./components/LoginView";
import { SettingsView } from "./components/SettingsView";
import { Sidebar } from "./components/Sidebar";
import { ToastStack, type ToastMessage } from "./components/Toast";
import { Topbar } from "./components/Topbar";
import { TaskWorkspace } from "./components/TaskWorkspace";
import { defaultSettings, demoChannels, demoFeed } from "./data/demo";
import {
  channelConnection,
  channelHistoryWindow,
  mapNtfyMessageItems,
  mergeFeed,
  normalizeFeedItems,
  usesHealthRelay,
} from "./lib/ntfy";
import { articleApi, type ArticleStateUpdate } from "./lib/articleApi";
import { authApi } from "./lib/authApi";
import { storage } from "./lib/storage";
import { canonicalChannelId, canonicalizeChannels, canonicalizeFeed, canonicalizeTrash, mergeChannelSnapshot } from "./lib/channelIdentity";
import { purgeExpiredTrash } from "./lib/trash";
import type {
  AppSettings,
  AuthSession,
  FeedItem,
  FeedbackValue,
  SmartChannel,
  SubscriptionStatus,
  TrashEntry,
  UpdateCheckState,
  ViewKey,
} from "./types";

type FeedFilter = "all" | "unread" | "saved";
const KnowledgeWorkspace = lazy(() => import("./components/KnowledgeWorkspace").then((module) => ({ default: module.KnowledgeWorkspace })));
const KnowledgeNotificationListener = lazy(() => import("./components/KnowledgeNotificationListener").then((module) => ({ default: module.KnowledgeNotificationListener })));

interface ActiveSubscription {
  signature: string;
}

type ArticleListResult = Awaited<ReturnType<typeof articleApi.listArticles>>;

function remapArticleIds(items: FeedItem[], idMap?: Record<string, string>): FeedItem[] {
  if (!idMap) return items;
  const byId = new Map<string, FeedItem>();
  for (const item of items) {
    const next = { ...item, id: idMap[item.id] || item.id };
    const previous = byId.get(next.id);
    byId.set(next.id, previous ? mergeFeed([previous], [next])[0] : next);
  }
  return Array.from(byId.values());
}

export default function App() {
  const [channels, setChannels] = useState<SmartChannel[]>(() =>
    storage.loadChannels(demoChannels, storage.loadSettings(defaultSettings).defaultServer),
  );
  const [trash, setTrash] = useState<TrashEntry[]>(() => {
    const storedItems = normalizeFeedItems(storage.loadFeed(demoFeed));
    const itemById = new Map(storedItems.map((item) => [item.id, item]));
    return purgeExpiredTrash(
      storage.loadTrash().map((entry) =>
        entry.purged ? entry : { ...entry, item: entry.item || itemById.get(entry.id) },
      ),
    );
  });
  const [feed, setFeed] = useState<FeedItem[]>(() => {
    const deleted = new Set(trash.map((entry) => entry.id));
    return normalizeFeedItems(storage.loadFeed(demoFeed)).filter((item) => !deleted.has(item.id));
  });
  const [settings, setSettings] = useState<AppSettings>(() =>
    storage.loadSettings(defaultSettings),
  );
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [loginBackendUrl, setLoginBackendUrl] = useState(defaultSettings.backendUrl);
  const [authStatus, setAuthStatus] = useState<"loading" | "signed-in" | "signed-out">(
    () => window.signalDesk?.loadAuthSession ? "loading" : "signed-in",
  );
  const [activeView, setActiveView] = useState<ViewKey>("today");
  const [selectedItemId, setSelectedItemId] = useState<string | null>(feed[0]?.id || null);
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");
  const [statuses, setStatuses] = useState<Record<string, SubscriptionStatus>>({});
  const [refreshing, setRefreshing] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingChannel, setEditingChannel] = useState<SmartChannel | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [appVersion, setAppVersion] = useState("0.2.0");
  const [platform, setPlatform] = useState("web");
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckState>({ status: "idle" });
  const [installingUpdate, setInstallingUpdate] = useState(false);
  const updateEventRef = useRef(0);
  const installRequestRef = useRef(false);
  const [subscriptionDefaults, setSubscriptionDefaults] = useState(() => ({
    defaultServer: settings.defaultServer,
    globalAuthToken: settings.globalAuthToken,
    backendUrl: settings.backendUrl,
    backendAuthToken: settings.backendAuthToken,
  }));
  const subscriptionsRef = useRef<Map<string, ActiveSubscription>>(new Map());
  const receivedMessagesRef = useRef(new Set<string>());
  const pendingMessagesRef = useRef(new Set<string>());
  const feedRef = useRef(feed);
  const trashRef = useRef(trash);
  const channelsRef = useRef(channels);
  const settingsRef = useRef(settings);
  const backendStateErrorRef = useRef(false);
  feedRef.current = feed;
  trashRef.current = trash;
  channelsRef.current = channels;
  settingsRef.current = settings;
  const trashIds = useMemo(() => trash.map((entry) => entry.id), [trash]);

  useEffect(() => storage.saveChannels(channels), [channels]);
  useEffect(() => storage.saveFeed(feed), [feed]);
  useEffect(() => storage.saveTrash(trash), [trash]);
  useEffect(() => storage.saveSettings(settings), [settings]);
  useEffect(() => { receivedMessagesRef.current = new Set(storage.loadNtfyReceipts()); }, []);
  useEffect(() => {
    const bridge = window.signalDesk;
    if (!bridge?.loadAuthSession) return;
    const authBridge = bridge;
    let cancelled = false;

    async function restoreLogin() {
      try {
        const stored = await authBridge.loadAuthSession();
        if (!stored) {
          if (!cancelled) setAuthStatus("signed-out");
          return;
        }
        const user = await authApi.me(stored);
        if (cancelled) return;
        const session = { ...stored, user };
        setAuthSession(session);
        setSettings((current) => ({
          ...current,
          backendUrl: session.backendUrl,
          backendAuthToken: session.accessToken,
          operatorName: session.user.displayName,
        }));
        setAuthStatus("signed-in");
      } catch {
        await authBridge.clearAuthSession().catch(() => undefined);
        if (!cancelled) setAuthStatus("signed-out");
      }
    }

    void restoreLogin();
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    const timer = window.setInterval(() => {
      setTrash((current) => purgeExpiredTrash(current));
    }, 60 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSubscriptionDefaults((current) => ({
        ...current,
        defaultServer: settings.defaultServer,
        globalAuthToken: settings.globalAuthToken,
      }));
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [settings.defaultServer, settings.globalAuthToken]);
  useEffect(() => {
    setSubscriptionDefaults((current) => ({ ...current, backendUrl: settings.backendUrl, backendAuthToken: settings.backendAuthToken }));
  }, [settings.backendUrl, settings.backendAuthToken]);

  const notify = useCallback((toast: Omit<ToastMessage, "id">) => {
    const id = crypto.randomUUID();
    setToasts((current) => [...current, { ...toast, id }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 4200);
  }, []);

  const mayChangeView = useCallback((next: ViewKey) => {
    if (next === activeView) return true;
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return !event.defaultPrevented || window.confirm("当前页面有未保存的内容。确定离开并放弃这些修改吗？");
  }, [activeView]);

  useEffect(() => {
    const bridge = window.signalDesk;
    if (!bridge?.getUpdateState && !bridge?.onUpdateState) return;
    let disposed = false;
    const observedEvents = updateEventRef.current;
    const unsubscribe = bridge.onUpdateState?.((state) => {
      if (disposed) return;
      updateEventRef.current += 1;
      setUpdateCheck(state);
    });
    bridge.getUpdateState?.()
      .then((state) => {
        if (!disposed && updateEventRef.current === observedEvents) setUpdateCheck(state);
      })
      .catch((error) => {
        if (!disposed && updateEventRef.current === observedEvents) {
          setUpdateCheck({ status: "error", message: errorMessage(error) });
        }
      });
    return () => {
      disposed = true;
      unsubscribe?.();
    };
  }, []);

  const backendSignature = `${settings.backendUrl.trim()}|${settings.backendAuthToken.trim()}|${settings.operatorName.trim()}`;

  const applyBackendSnapshot = useCallback((
    articleResult: ArticleListResult,
    remoteChannels: SmartChannel[],
  ) => {
    const previousChannels = channelsRef.current;
    const mergedChannels = mergeChannelSnapshot(previousChannels, remoteChannels);
    const aliases = [...previousChannels, ...mergedChannels, ...remoteChannels];
    const remoteIds = new Set(remoteChannels.map((channel) => canonicalChannelId(channel.id, aliases)));
    const localOnlyIds = new Set(mergedChannels.filter((channel) => !remoteIds.has(channel.id)).map((channel) => channel.id));
    const serverActive = canonicalizeFeed(articleResult.items
      .filter((item) => item.deletedAt === null)
      .map(({ deletedAt: _deletedAt, purged: _purged, ...item }) => item), aliases);
    const serverTrash = canonicalizeTrash(articleResult.items
      .filter((item) => item.deletedAt !== null)
      .map(({ deletedAt, purged: _purged, ...item }) => ({
        id: item.id,
        item,
        deletedAt: deletedAt!,
      })), aliases);
    const serverTrashIds = serverTrash.map((entry) => entry.id);

    setFeed((current) => {
      const normalized = canonicalizeFeed(current, aliases);
      const byId = new Map(normalized.map((item) => [item.id, item]));
      const incoming = serverActive.map((item) => {
        const previous = byId.get(item.id);
        return previous && (previous.stateUpdatedAt || 0) > (item.stateUpdatedAt || 0)
          ? { ...item, read: previous.read, saved: previous.saved, feedback: previous.feedback, stateUpdatedAt: previous.stateUpdatedAt }
          : item;
      });
      return mergeFeed(
        normalized.filter((item) => item.id.startsWith("demo-message-") || localOnlyIds.has(item.channelId)),
        incoming,
        serverTrashIds,
      );
    });
    setTrash((current) => [
      ...serverTrash,
      ...canonicalizeTrash(current, aliases).filter(
        (entry) =>
          (entry.purged || entry.id.startsWith("demo-message-") || (entry.item && localOnlyIds.has(entry.item.channelId))) &&
          !serverTrashIds.includes(entry.id),
      ),
    ]);
    channelsRef.current = mergedChannels;
    setChannels(mergedChannels);
    setSelectedChannelId((id) => id ? canonicalChannelId(id, aliases) : null);
    setEditingChannel((channel) => channel ? mergedChannels.find((item) => item.id === canonicalChannelId(channel.id, aliases)) || null : null);
  }, []);

  useEffect(() => {
    if (!articleApi.configured(settings)) return;
    let cancelled = false;

    async function synchronizeBackend() {
      try {
        const localChannels = channelsRef.current.filter(
          (channel) => !channel.isDemo || Boolean(channel.topic.trim()),
        );
        await Promise.all(
          localChannels.map((channel) => articleApi.upsertChannel(settings, channel)),
        );
        await articleApi.syncArticles(settings, feedRef.current, trashRef.current, channelsRef.current);
        const [articleResult, remoteChannels] = await Promise.all([
          articleApi.listArticles(settings, "all"),
          articleApi.listChannels(settings),
        ]);
        if (cancelled) return;
        applyBackendSnapshot(articleResult, remoteChannels);
        backendStateErrorRef.current = false;
      } catch (error) {
        if (cancelled) return;
        notify({
          title: "服务器文章同步失败",
          message: `${errorMessage(error)}；当前继续使用本机缓存。`,
          tone: "error",
        });
      }
    }

    void synchronizeBackend();
    return () => {
      cancelled = true;
    };
  }, [applyBackendSnapshot, backendSignature, notify]);

  const hydrateTrashItems = useCallback((items: FeedItem[]) => {
    const itemById = new Map(items.map((item) => [item.id, item]));
    setTrash((current) => {
      let changed = false;
      const next = current.map((entry) => {
        if (entry.purged || entry.item || !itemById.has(entry.id)) return entry;
        changed = true;
        return { ...entry, item: itemById.get(entry.id) };
      });
      return changed ? next : current;
    });
  }, []);

  async function checkForUpdates() {
    const bridge = window.signalDesk;
    if (!bridge?.checkForUpdates) {
      setUpdateCheck({ status: "error", message: "请在 Electron 桌面端检查更新。" });
      return;
    }

    const observedEvents = updateEventRef.current;
    setUpdateCheck((current) => ({ ...current, status: "checking", message: undefined }));
    try {
      const result = await bridge.checkForUpdates();
      if (updateEventRef.current !== observedEvents) return;
      setUpdateCheck(
        "status" in result ? result : result.updateAvailable
          ? {
              status: "available",
              latestVersion: result.latestVersion,
              downloadUrl: result.downloadUrl,
              installerAvailable: result.installerAvailable,
            }
          : { status: "current", latestVersion: result.latestVersion },
      );
    } catch (error) {
      if (updateEventRef.current === observedEvents) {
        setUpdateCheck((current) => ({ ...current, status: "error", message: errorMessage(error) }));
      }
    }
  }

  async function downloadUpdate() {
    const bridge = window.signalDesk;
    if (bridge?.downloadUpdate) {
      const observedEvents = updateEventRef.current;
      setUpdateCheck((current) => ({ ...current, status: "downloading", progress: undefined, message: undefined }));
      try {
        const state = await bridge.downloadUpdate();
        if (updateEventRef.current === observedEvents) setUpdateCheck(state);
      } catch (error) {
        if (updateEventRef.current === observedEvents) {
          setUpdateCheck((current) => ({ ...current, status: "error", message: errorMessage(error) }));
        }
      }
      return;
    }
    if (!updateCheck.downloadUrl) return;
    bridge?.openExternal(updateCheck.downloadUrl).catch((error) => {
      notify({ title: "无法打开更新地址", message: errorMessage(error), tone: "error" });
    });
  }

  async function installUpdate() {
    if (updateCheck.status !== "ready" || installRequestRef.current) return;
    const bridge = window.signalDesk;
    if (!bridge?.installUpdate) {
      notify({ title: "暂时无法安装更新", message: "请在支持自动更新的桌面端重试。", tone: "error" });
      return;
    }
    installRequestRef.current = true;
    setInstallingUpdate(true);
    try {
      const result = await bridge.installUpdate();
      if (result.started) setUpdateCheck((current) => ({ ...current, status: "installing" }));
      else if (result.reason) notify({ title: "更新尚未安装", message: result.reason, tone: "info" });
      // A cancelled native confirmation leaves the downloaded update ready.
    } catch (error) {
      notify({ title: "暂时无法安装更新", message: errorMessage(error), tone: "error" });
    } finally {
      installRequestRef.current = false;
      setInstallingUpdate(false);
    }
  }

  const syncChannels = useCallback(async () => {
    const bridge = window.signalDesk;
    const connectedChannels = canonicalizeChannels(channels).filter(
      (channel) => channel.state === "active" && Boolean(channelConnection(channel, settings)?.topic),
    );
    if (!bridge) {
      notify({
        title: "网页预览模式",
        message: "请启动 Electron 桌面端以连接 ntfy。",
        tone: "info",
      });
      return;
    }
    const backendConfigured = articleApi.contentConfigured(settings);
    if (connectedChannels.length === 0 && !backendConfigured) {
      notify({
        title: "没有可同步的频道",
        message: "请先为频道配置 topic 并启用。",
        tone: "info",
      });
      return;
    }

    setRefreshing(true);
    setStatuses((current) => {
      const next = { ...current };
      connectedChannels.forEach((channel) => {
        next[channel.id] = { status: "syncing" };
      });
      return next;
    });

    try {
      const results = await Promise.allSettled(
        connectedChannels.map(async (channel) => {
          const connection = channelConnection(channel, settings);
          if (!connection) throw new Error("请先登录本人账号以接收健康情报。");
          const messages = await bridge.pollMessages({
            ...connection,
            since: channelHistoryWindow(channel, settings.historyWindow),
          });
          let items = messages.flatMap((message) => mapNtfyMessageItems(channel, message));
          let idMap: Record<string, string> | undefined;
          const resolved = channel.id === "health" && channel.managed && backendConfigured;
          if (resolved && items.length > 0) {
            const saved = await articleApi.batchArticles(settings, items, channels, true);
            idMap = saved.idMap;
            items = remapArticleIds(items, idMap);
          }
          return { channel, connection, items, idMap, resolved };
        }),
      );

      if (settingsRef.current.backendUrl !== settings.backendUrl || settingsRef.current.backendAuthToken !== settings.backendAuthToken) return;

      let received = 0;
      const receivedItems: FeedItem[] = [];
      results.forEach((result, index) => {
        const channel = connectedChannels[index];
        if (result.status === "fulfilled") {
          const { idMap, resolved, connection } = result.value;
          if (resolved && JSON.stringify(channelConnection(channelsRef.current.find(item => item.id === channel.id) || channel, settingsRef.current)) !== JSON.stringify(connection)) return;
          received += result.value.items.length;
          if (!resolved) receivedItems.push(...result.value.items);
          if (idMap) setTrash(current => current.map(entry => ({ ...entry, id: idMap[entry.id] || entry.id, item: entry.item ? { ...entry.item, id: idMap[entry.item.id] || entry.item.id } : undefined })));
          hydrateTrashItems(result.value.items);
          setFeed((current) => mergeFeed(remapArticleIds(current, idMap), result.value.items, trashRef.current.map(entry => idMap?.[entry.id] || entry.id)));
          setChannels((current) =>
            current.map((item) =>
              item.id === result.value.channel.id
                ? { ...item, lastSyncAt: Date.now() }
                : item,
            ),
          );
          setStatuses((current) => ({
            ...current,
            [result.value.channel.id]: { status: "connected" },
          }));
          return;
        }
        setStatuses((current) => ({
          ...current,
          [channel.id]: { status: "error", message: errorMessage(result.reason) },
        }));
      });

      let backendError: unknown;
      if (backendConfigured && receivedItems.length > 0) {
        try {
          await articleApi.batchArticles(settings, receivedItems, channels);
        } catch (error) {
          backendError = error;
        }
      }

      if (backendConfigured && !backendError) {
        try {
          const [articleResult, remoteChannels] = await Promise.all([
            articleApi.listArticles(settings, "all"),
            articleApi.listChannels(settings),
          ]);
          if (settingsRef.current.backendUrl !== settings.backendUrl || settingsRef.current.backendAuthToken !== settings.backendAuthToken) return;
          applyBackendSnapshot(articleResult, remoteChannels);
          backendStateErrorRef.current = false;
        } catch (error) {
          backendError = error;
        }
      }
      if (backendError) backendStateErrorRef.current = true;

      const failed = results.filter((result) => result.status === "rejected");
      if (failed.length > 0) {
        notify({
          title: `${failed.length} 个频道同步失败`,
          message: errorMessage((failed[0] as PromiseRejectedResult).reason),
          tone: "error",
        });
      } else if (backendError) {
        notify({
          title: "服务器内容刷新失败",
          message: errorMessage(backendError),
          tone: "error",
        });
      } else {
        notify({
          title: "同步完成",
          message: received > 0
            ? `读取到 ${received} 条缓存消息，并已刷新服务器内容。`
            : backendConfigured
              ? "已从服务器重新读取最新内容。"
              : "当前没有新的缓存消息。",
          tone: "success",
        });
      }
    } finally {
      setRefreshing(false);
    }
  }, [applyBackendSnapshot, channels, settings, notify, trashIds, hydrateTrashItems]);

  useEffect(() => {
    window.signalDesk
      ?.getAppInfo()
      .then((info) => {
        setAppVersion(info.version);
        setPlatform(info.platform);
        if (info.backendUrl) setLoginBackendUrl(info.backendUrl);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const bridge = window.signalDesk;
    if (!bridge?.onNavigate) return;
    return bridge.onNavigate((view) => {
      if (!mayChangeView(view)) return;
      setActiveView(view);
      setSelectedChannelId(null);
    });
  }, [mayChangeView]);

  useEffect(() => {
    const bridge = window.signalDesk;
    if (!bridge) return;

    return bridge.onMessage((event) => {
      if (event.type === "status" && event.subscriptionId) {
        const subscriptionId = canonicalChannelId(event.subscriptionId, channels);
        setStatuses((current) => ({
          ...current,
          [subscriptionId]: {
            status: event.status === "connected" || event.status === "reconnecting" ? event.status : "error",
            message: event.error,
          },
        }));
        return;
      }
      if (event.type === "notification-click" && event.messageId) {
        if (event.messageId === "health:status") {
          if (!mayChangeView("inbox")) return;
          setSelectedChannelId("health"); setSelectedItemId(null); setActiveView("inbox"); return;
        }
        if (event.messageId === "knowledge:inbox") {
          if (!mayChangeView("knowledge")) return;
          setActiveView("knowledge"); setSelectedChannelId(null); return;
        }
        if (!mayChangeView("inbox")) return;
        setSelectedItemId(event.messageId);
        setActiveView("inbox");
        return;
      }
      if (event.type !== "message" || !event.subscriptionId || !event.payload) return;
      const subscriptionId = canonicalChannelId(event.subscriptionId, channels);
      const channel = channels.find((item) => item.id === subscriptionId);
      if (!channel) return;
      const connection = channelConnection(channel, settings);
      if (!connection?.topic || event.payload.topic !== connection.topic) return;
      // ntfy may replay a message after reconnect, or a retired alias stream may
      // finish after the canonical subscription starts. Consume each event once.
      const receipt = JSON.stringify([subscriptionId, connection.server.replace(/\/+$/, ""), connection.topic, event.payload.id]);
      const messageIdentity = subscriptionId === "health" ? JSON.stringify(["health-message", event.payload.id]) : receipt;
      if (receivedMessagesRef.current.has(receipt) || receivedMessagesRef.current.has(messageIdentity)) return;
      const remember = () => {
        receivedMessagesRef.current.add(receipt);
        receivedMessagesRef.current.add(messageIdentity);
        while (receivedMessagesRef.current.size > 1000) receivedMessagesRef.current.delete(receivedMessagesRef.current.values().next().value!);
        storage.saveNtfyReceipts(Array.from(receivedMessagesRef.current));
      };
      if (channel.id === "health" && event.payload.tags?.includes("health_status")) {
        remember();
        if (settings.notifications) {
          const title = "健康情报推送状态";
          const body = plainNotification(event.payload.message || "服务器正在准备今天的健康情报，完成后会继续推送。");
          notify({ title, message: body, tone: "info" });
          bridge.showNotification({ title, body, messageId: "health:status", silent: !settings.notificationSound });
        }
        return;
      }
      const incomingItems = mapNtfyMessageItems(channel, event.payload);
      const commit = (received: FeedItem[], idMap?: Record<string, string>) => {
        if (receivedMessagesRef.current.has(receipt) || receivedMessagesRef.current.has(messageIdentity)) return;
        remember();
        const excluded = trashRef.current.map(entry => idMap?.[entry.id] || entry.id);
        if (idMap) setTrash(current => current.map(entry => ({ ...entry, id: idMap[entry.id] || entry.id, item: entry.item ? { ...entry.item, id: idMap[entry.item.id] || entry.item.id } : undefined })));
        hydrateTrashItems(received);
        const items = received.filter(item => !excluded.includes(item.id));
        if (items.length === 0) return;
        setFeed(current => mergeFeed(remapArticleIds(current, idMap), items, excluded));
        if (settings.notifications) {
          const notificationItem = items[0];
          bridge.showNotification({
            title: items.length === 1 ? notificationItem.title : `${channel.name} · ${items.length} 条新情报`,
            body: items.length === 1 ? plainNotification(notificationItem.body) : items.map(item => item.title).join("、"),
            messageId: notificationItem.id,
            silent: !settings.notificationSound,
          });
        }
      };
      if (channel.id === "health" && channel.managed && articleApi.contentConfigured(settings)) {
        const signature = JSON.stringify(connection);
        const pending = receipt + signature;
        if (pendingMessagesRef.current.has(pending)) return;
        pendingMessagesRef.current.add(pending);
        const current = () => {
          const latest = channelsRef.current.find(item => item.id === channel.id);
          return latest?.state === "active" && settingsRef.current.backendUrl === settings.backendUrl && settingsRef.current.backendAuthToken === settings.backendAuthToken && JSON.stringify(channelConnection(latest, settingsRef.current)) === signature;
        };
        void articleApi.batchArticles(settings, incomingItems, channels, true).then(result => {
          if (!current()) return;
          commit(remapArticleIds(incomingItems, result.idMap), result.idMap);
        }).catch(error => {
          if (!current()) return;
          notify({ title: "文章暂未同步", message: `${errorMessage(error)}；消息保留在本机，请稍后点击同步。`, tone: "error" });
          commit(incomingItems);
        }).finally(() => pendingMessagesRef.current.delete(pending));
        return;
      }
      if (articleApi.contentConfigured(settings)) articleApi.batchArticles(settings, incomingItems, channels).catch(() => undefined);
      commit(incomingItems);
    });
  }, [
    channels,
    trashIds,
    settings.backendUrl,
    settings.backendAuthToken,
    settings.defaultServer,
    settings.notifications,
    settings.notificationSound,
    notify,
    hydrateTrashItems,
    mayChangeView,
  ]);

  useEffect(() => {
    const bridge = window.signalDesk;
    if (!bridge) return;

    const desired = new Map(
      canonicalizeChannels(channels)
        .filter((channel) => channel.state === "active")
        .flatMap((channel) => {
          const connection = channelConnection(channel, subscriptionDefaults);
          if (!connection?.topic) return [];
          return [[
            channel.id,
            {
              config: { ...connection, subscriptionId: channel.id },
              signature: JSON.stringify(connection),
            },
          ] as const];
        }),
    );

    const disconnects = new Map<string, Promise<unknown>>();
    for (const [subscriptionId, current] of subscriptionsRef.current) {
      const next = desired.get(subscriptionId);
      if (next && next.signature === current.signature) continue;

      subscriptionsRef.current.delete(subscriptionId);
      disconnects.set(
        subscriptionId,
        bridge.unsubscribe(subscriptionId).catch(() => undefined),
      );
      setStatuses((statuses) => ({
        ...statuses,
        [subscriptionId]: { status: "idle" },
      }));
    }

    for (const [subscriptionId, next] of desired) {
      if (subscriptionsRef.current.get(subscriptionId)?.signature === next.signature) continue;

      const record: ActiveSubscription = { signature: next.signature };
      subscriptionsRef.current.set(subscriptionId, record);
      setStatuses((current) => ({
        ...current,
        [subscriptionId]: { status: "syncing" },
      }));

      const disconnected = disconnects.get(subscriptionId) || Promise.resolve();
      disconnected
        .then(() => {
          if (subscriptionsRef.current.get(subscriptionId) !== record) return undefined;
          return bridge.subscribe(next.config);
        })
        .catch((error) => {
          if (subscriptionsRef.current.get(subscriptionId) !== record) return;
          setStatuses((current) => ({
            ...current,
            [subscriptionId]: { status: "error", message: errorMessage(error) },
          }));
        });
    }
  }, [channels, subscriptionDefaults]);

  useEffect(() => {
    const bridge = window.signalDesk;
    return () => {
      if (!bridge) return;
      for (const subscriptionId of subscriptionsRef.current.keys()) {
        bridge.unsubscribe(subscriptionId).catch(() => undefined);
      }
      subscriptionsRef.current.clear();
    };
  }, []);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.querySelector<HTMLInputElement>('.search-field input')?.focus();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        if (activeView === "content" || activeView === "knowledge" || activeView === "trash") return;
        setEditingChannel(null);
        setDialogOpen(true);
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [activeView]);

  const visibleItems = useMemo(() => {
    if (activeView === "trash") return [];
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    const query = searchQuery.trim().toLowerCase();
    return feed.filter((item) => {
      if (activeView === "today" && item.timestamp < todayStart.getTime()) return false;
      if (activeView === "saved" && !item.saved) return false;
      if (selectedChannelId && item.channelId !== selectedChannelId) return false;
      if (feedFilter === "unread" && item.read) return false;
      if (feedFilter === "saved" && !item.saved) return false;
      if (query && !`${item.title} ${item.body} ${item.source}`.toLowerCase().includes(query)) {
        return false;
      }
      return true;
    });
  }, [feed, activeView, selectedChannelId, feedFilter, searchQuery]);

  const visibleTrashEntries = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return trash
      .filter((entry): entry is TrashEntry & { item: FeedItem } => !entry.purged && Boolean(entry.item))
      .filter(({ item }) =>
        !query || `${item.title} ${item.body} ${item.source}`.toLowerCase().includes(query),
      )
      .sort((a, b) => b.deletedAt - a.deletedAt);
  }, [trash, searchQuery]);

  const visibleTrashItems = useMemo(
    () => visibleTrashEntries.map((entry) => entry.item),
    [visibleTrashEntries],
  );

  const deletedAtById = useMemo(
    () => Object.fromEntries(visibleTrashEntries.map((entry) => [entry.id, entry.deletedAt])),
    [visibleTrashEntries],
  );

  useEffect(() => {
    if (activeView === "trash") {
      if (visibleTrashItems.length > 0 && !visibleTrashItems.some((item) => item.id === selectedItemId)) {
        setSelectedItemId(visibleTrashItems[0].id);
      }
      return;
    }
    if (visibleItems.length > 0 && !visibleItems.some((item) => item.id === selectedItemId)) {
      setSelectedItemId(visibleItems[0].id);
    }
  }, [activeView, visibleItems, visibleTrashItems, selectedItemId]);

  function persistArticleState(itemId: string, update: ArticleStateUpdate) {
    if (!articleApi.configured(settings) || itemId.startsWith("demo-message-")) return;
    articleApi
      .updateState(settings, itemId, update)
      .then(() => {
        backendStateErrorRef.current = false;
      })
      .catch((error) => {
        if (backendStateErrorRef.current) return;
        backendStateErrorRef.current = true;
        notify({
          title: "文章状态暂未同步",
          message: `${errorMessage(error)}；操作已保存在本机。`,
          tone: "error",
        });
      });
  }

  function mutateFeed(
    itemId: string,
    update: (item: FeedItem) => FeedItem,
    remoteUpdate?: Omit<ArticleStateUpdate, "stateUpdatedAt">,
  ) {
    const stateUpdatedAt = Date.now();
    setFeed((current) =>
      current.map((item) =>
        item.id === itemId ? { ...update(item), stateUpdatedAt } : item,
      ),
    );
    if (remoteUpdate) {
      persistArticleState(itemId, { ...remoteUpdate, stateUpdatedAt });
    }
  }

  function deleteFeedItem(itemId: string) {
    const item = feed.find((entry) => entry.id === itemId);
    if (!item || !window.confirm(`将“${item.title}”移入回收站吗？`)) return;

    const currentIndex = visibleItems.findIndex((entry) => entry.id === itemId);
    const nextItem = visibleItems[currentIndex + 1] || visibleItems[currentIndex - 1];
    const stateUpdatedAt = Date.now();
    const deletedItem = { ...item, stateUpdatedAt };
    setTrash((current) => [
      { id: item.id, item: deletedItem, deletedAt: stateUpdatedAt },
      ...current.filter((entry) => entry.id !== item.id),
    ]);
    setFeed((current) => current.filter((entry) => entry.id !== itemId));
    persistArticleState(itemId, { action: "delete", stateUpdatedAt });
    setSelectedItemId(nextItem?.id || null);
    notify({ title: "已移入回收站", message: "文章将在 7 天后自动清除。", tone: "success" });
  }

  function restoreTrashItem(itemId: string) {
    const entry = trash.find((item) => item.id === itemId);
    if (!entry?.item) return;
    const stateUpdatedAt = Date.now();
    const restoredItem = { ...entry.item, stateUpdatedAt };
    setTrash((current) => current.filter((item) => item.id !== itemId));
    setFeed((current) => mergeFeed(current, [restoredItem], []));
    persistArticleState(itemId, { action: "restore", stateUpdatedAt });
    notify({ title: "文章已恢复", message: entry.item.title, tone: "success" });
  }

  function deleteTrashItemPermanently(itemId: string) {
    const entry = trash.find((item) => item.id === itemId);
    if (!entry || !window.confirm(`永久删除“${entry.item?.title || "这篇文章"}”吗？此操作无法撤销。`)) return;
    const stateUpdatedAt = Date.now();
    setTrash((current) => current.map((item) =>
      item.id === itemId ? { id: item.id, deletedAt: item.deletedAt, purged: true } : item,
    ));
    persistArticleState(itemId, { action: "permanent", stateUpdatedAt });
    notify({ title: "文章已永久删除", tone: "success" });
  }

  function emptyTrash() {
    if (visibleTrashItems.length === 0 || !window.confirm("清空回收站吗？此操作无法撤销。")) return;
    setTrash((current) => current.map((entry) =>
      entry.item ? { id: entry.id, deletedAt: entry.deletedAt, purged: true } : entry,
    ));
    visibleTrashItems.forEach((item, index) => {
      persistArticleState(item.id, {
        action: "permanent",
        stateUpdatedAt: Date.now() + index,
      });
    });
    setSelectedItemId(null);
    notify({ title: "回收站已清空", tone: "success" });
  }

  function saveChannel(channel: SmartChannel) {
    channel = canonicalizeChannels([channel], channels)[0];
    const exists = channels.some((item) => item.id === channel.id);
    setChannels((current) =>
      exists ? current.map((item) => (item.id === channel.id ? channel : item)) : [channel, ...current],
    );
    setDialogOpen(false);
    setEditingChannel(null);
    notify({
      title: exists ? "频道已更新" : "频道已创建",
      message: channel.managed ? "本机访问令牌已保存；每日北京时间推送由服务器管理。" : channel.topic ? "ntfy 通道将在后台连接。" : "配置 topic 后即可接收实时消息。",
      tone: "success",
    });
    if (!articleApi.contentConfigured(settings)) return;
    if (channel.managed) return;
    articleApi.upsertChannel(settings, channel).catch((error) => {
      notify({ title: "服务器同步失败", message: errorMessage(error), tone: "error" });
    });
  }

  function toggleChannel(channelId: string) {
    const channel = channels.find((item) => item.id === channelId);
    if (!channel || channel.managed) return;
    const updated: SmartChannel = {
      ...channel,
      state: channel.state === "active" ? "paused" : channel.topic ? "active" : "draft",
      updatedAt: Date.now(),
    };
    setChannels((current) =>
      current.map((item) => (item.id === channelId ? updated : item)),
    );
    if (articleApi.contentConfigured(settings)) {
      articleApi.upsertChannel(settings, updated).catch((error) => {
        notify({ title: "服务器同步失败", message: errorMessage(error), tone: "error" });
      });
    }
  }

  async function testChannel(channel: SmartChannel) {
    const bridge = window.signalDesk;
    if (!bridge) return;
    const connection = channelConnection(channel, settings);
    if (!connection?.topic) {
      if (usesHealthRelay(channel)) notify({ title: "请先登录", message: "登录本人账号后可检查健康情报接收。", tone: "info" });
      return;
    }
    try {
      if (usesHealthRelay(channel)) {
        await bridge.pollMessages({ ...connection, since: "10m" });
        notify({ title: "接收链路正常", message: "健康情报由服务器准备并推送，历史文章从服务器同步。", tone: "success" });
        return;
      }
      await bridge.publishMessage({
        ...connection,
        title: `迈医协同 · ${channel.name}`,
        message: "频道连接成功。这是一条来自迈医协同桌面端的测试消息。",
        priority: 3,
        tags: ["satellite"],
      });
      notify({ title: "测试消息已发送", message: channel.topic, tone: "success" });
    } catch (error) {
      notify({ title: usesHealthRelay(channel) ? "接收检查失败" : "发送失败", message: errorMessage(error), tone: "error" });
    }
  }

  function updateSettings(next: AppSettings) {
    if (next.launchAtLogin !== settings.launchAtLogin) {
      window.signalDesk?.setLaunchAtLogin(next.launchAtLogin).catch(() => undefined);
    }
    setSettings(next);
  }

  async function login(username: string, password: string) {
    const session = await authApi.login(loginBackendUrl, username, password);
    const bridge = window.signalDesk;
    if (!bridge?.saveAuthSession) throw new Error("请在迈医协同桌面客户端中登录");
    await bridge.saveAuthSession(session);
    setAuthSession(session);
    setSettings((current) => ({
      ...current,
      backendUrl: session.backendUrl,
      backendAuthToken: session.accessToken,
      operatorName: session.user.displayName,
    }));
    setAuthStatus("signed-in");
  }

  async function logout() {
    if (authSession) await authApi.logout(authSession).catch(() => undefined);
    await window.signalDesk?.clearAuthSession().catch(() => undefined);
    await window.signalDesk?.hideTaskWidget().catch(() => undefined);
    setAuthSession(null);
    setSettings((current) => ({
      ...current,
      backendUrl: defaultSettings.backendUrl,
      backendAuthToken: "",
      operatorName: "",
    }));
    setAuthStatus("signed-out");
  }

  async function toggleTaskWidget() {
    const bridge = window.signalDesk;
    if (!bridge) {
      notify({ title: "桌面挂件仅支持客户端", tone: "info" });
      return;
    }
    try {
      const result = await bridge.toggleTaskWidget();
      if (result.launchAtLogin && !settings.launchAtLogin) {
        setSettings((current) => ({ ...current, launchAtLogin: true }));
      }
      notify({
        title: result.visible ? "桌面挂件已打开" : "桌面挂件已关闭",
        message: result.visible ? "已同时启用登录系统后启动。" : undefined,
        tone: "success",
      });
    } catch (error) {
      notify({ title: "桌面挂件打开失败", message: errorMessage(error), tone: "error" });
    }
  }

  function resetLocalData() {
    if (!window.confirm("确定清除所有本地频道、消息和设置吗？")) return;
    storage.clearAll();
    setChannels(canonicalizeChannels(demoChannels));
    setFeed(canonicalizeFeed(demoFeed));
    setTrash([]);
    setSettings({
      ...defaultSettings,
      backendAuthToken: authSession?.accessToken || "",
      operatorName: authSession?.user.displayName || "",
    });
    setActiveView("today");
    setSelectedChannelId(null);
    notify({ title: "本地数据已重置", tone: "success" });
  }

  const unreadCount = feed.filter((item) => !item.read).length;
  const savedCount = feed.filter((item) => item.saved).length;
  const trashCount = trash.filter((entry) => entry.item).length;

  if (authStatus === "loading") {
    return (
      <main className="login-shell login-loading" aria-label="正在恢复登录状态">
        <img src={mayiLogo} alt="" />
        <span className="login-loading-pulse" />
      </main>
    );
  }

  if (authStatus === "signed-out") return <LoginView onLogin={login} />;

  return (
    <div className={`app-shell platform-${platform}`}>
      {authSession && <Suspense fallback={null}><KnowledgeNotificationListener key={`${settings.backendUrl}-${authSession.user.id}`} settings={settings} userId={authSession.user.id} /></Suspense>}
      <div className="window-drag-region" aria-hidden="true">
        <img src={mayiLogo} alt="" />
        <span>迈医协同</span>
      </div>
      <Sidebar
        activeView={activeView}
        channels={channels}
        unreadCount={unreadCount}
        savedCount={savedCount}
        trashCount={trashCount}
        statuses={statuses}
        onViewChange={(view) => {
          if (!mayChangeView(view)) return;
          setActiveView(view);
          setSelectedChannelId(null);
          if (view === "saved") setFeedFilter("all");
        }}
        onChannelSelect={(channelId) => {
          if (!mayChangeView("inbox")) return;
          setSelectedChannelId(canonicalChannelId(channelId, channels));
          setActiveView("inbox");
          setFeedFilter("all");
        }}
        onToggleTaskWidget={toggleTaskWidget}
      />

      <main className="main-surface" style={updateCheck.status === "ready" ? { gridTemplateRows: "72px auto minmax(0, 1fr)" } : undefined}>
        <Topbar
          activeView={activeView}
          searchQuery={searchQuery}
          refreshing={refreshing}
          onSearchChange={setSearchQuery}
          onRefresh={syncChannels}
          onCreateChannel={() => {
            setEditingChannel(null);
            setDialogOpen(true);
          }}
        />

        {updateCheck.status === "ready" && (
          <div className="settings-row" role="status" aria-live="polite" style={{ background: "var(--green-soft)" }}>
            <div>
              <strong>新版本{updateCheck.latestVersion ? ` v${updateCheck.latestVersion}` : ""}已下载</strong>
              <span>可在方便时重启安装，保留已有账号、数据和配置。</span>
            </div>
            <button className="primary-button" type="button" disabled={installingUpdate} onClick={installUpdate}>
              {installingUpdate ? "等待重启确认…" : "新版已下载，重启安装"}
            </button>
          </div>
        )}

        <div className="page-body">
          {activeView === "channels" ? (
            <ChannelsView
              channels={channels}
              statuses={statuses}
              onCreate={() => {
                setEditingChannel(null);
                setDialogOpen(true);
              }}
              onEdit={(channel) => {
                setEditingChannel(channel);
                setDialogOpen(true);
              }}
              onToggle={toggleChannel}
              onDelete={(channelId) => {
                const channel = channels.find((item) => item.id === channelId);
                if (!channel || channel.managed || !window.confirm(`删除频道“${channel.name}”及其本地消息吗？`)) return;
                setChannels((current) => current.filter((item) => item.id !== channelId));
                setFeed((current) => current.filter((item) => item.channelId !== channelId));
                if (articleApi.contentConfigured(settings)) {
                  articleApi.deleteChannel(settings, channelId).catch((error) => {
                    notify({ title: "服务器删除频道失败", message: errorMessage(error), tone: "error" });
                  });
                }
              }}
              onTest={testChannel}
            />
          ) : activeView === "settings" ? (
            <SettingsView
              settings={settings}
              authUser={authSession?.user}
              appVersion={appVersion}
              platform={platform}
              updateCheck={updateCheck}
              automaticUpdates={Boolean(window.signalDesk?.getUpdateState && window.signalDesk?.downloadUpdate && window.signalDesk?.installUpdate)}
              installingUpdate={installingUpdate}
              onChange={updateSettings}
              onCheckForUpdates={checkForUpdates}
              onDownloadUpdate={downloadUpdate}
              onInstallUpdate={installUpdate}
              onReset={resetLocalData}
              onLogout={logout}
            />
          ) : activeView === "tasks" ? (
            <TaskWorkspace
              settings={settings}
              searchQuery={searchQuery}
              currentStaffId={authSession?.user.staffId}
              canManageMembers={authSession?.user.role === "admin"}
            />
          ) : activeView === "knowledge" ? (
            <Suspense fallback={<p role="status">正在打开知识库…</p>}><KnowledgeWorkspace settings={settings} canManage={authSession?.user.role === "admin"} /></Suspense>
          ) : activeView === "content" ? (
            <ContentGenerationView settings={settings} />
          ) : activeView === "trash" ? (
            <MessageWorkspace
              mode="trash"
              items={visibleTrashItems}
              channels={channels}
              selectedItemId={selectedItemId}
              filter="all"
              compactMode={settings.compactMode}
              deletedAtById={deletedAtById}
              onFilterChange={() => undefined}
              onSelect={setSelectedItemId}
              onToggleRead={() => undefined}
              onToggleSaved={() => undefined}
              onFeedback={() => undefined}
              onDelete={() => undefined}
              onRestore={restoreTrashItem}
              onDeletePermanently={deleteTrashItemPermanently}
              onEmptyTrash={emptyTrash}
              onOpenLink={(url) => {
                if (window.signalDesk) window.signalDesk.openExternal(url);
                else window.open(url, "_blank", "noopener,noreferrer");
              }}
            />
          ) : (
            <MessageWorkspace
              items={visibleItems}
              channels={channels}
              selectedItemId={selectedItemId}
              filter={feedFilter}
              compactMode={settings.compactMode}
              onFilterChange={setFeedFilter}
              onSelect={(itemId) => {
                setSelectedItemId(itemId);
                if (!feed.find((item) => item.id === itemId)?.read) {
                  mutateFeed(itemId, (item) => ({ ...item, read: true }), { read: true });
                }
              }}
              onToggleRead={(itemId) =>
                mutateFeed(
                  itemId,
                  (item) => ({ ...item, read: !item.read }),
                  { read: !feed.find((item) => item.id === itemId)?.read },
                )
              }
              onToggleSaved={(itemId) =>
                mutateFeed(
                  itemId,
                  (item) => ({ ...item, saved: !item.saved }),
                  { saved: !feed.find((item) => item.id === itemId)?.saved },
                )
              }
              onFeedback={(itemId, feedback: FeedbackValue) =>
                mutateFeed(itemId, (item) => ({ ...item, feedback }), { feedback })
              }
              onDelete={deleteFeedItem}
              onOpenLink={(url) => {
                if (window.signalDesk) window.signalDesk.openExternal(url);
                else window.open(url, "_blank", "noopener,noreferrer");
              }}
            />
          )}
        </div>
      </main>

      <ChannelDialog
        open={dialogOpen}
        channel={editingChannel}
        settings={settings}
        onClose={() => {
          setDialogOpen(false);
          setEditingChannel(null);
        }}
        onSave={saveChannel}
      />
      <ToastStack
        toasts={toasts}
        onDismiss={(id) => setToasts((current) => current.filter((toast) => toast.id !== id))}
      />
    </div>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "未知错误");
}

function plainNotification(markdown: string): string {
  return markdown.replace(/[#>*_`\[\]()]/g, " ").replace(/\s+/g, " ").trim().slice(0, 180);
}
