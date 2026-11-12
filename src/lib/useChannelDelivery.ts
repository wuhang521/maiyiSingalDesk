import { useCallback, useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { articleApi, type ChannelDeliveryCatalog, type ChannelDeliveryConfig, type ChannelDeliveryDraft } from "./articleApi";

interface CatalogState {
  scope: string;
  catalog?: ChannelDeliveryCatalog;
  unsupported?: boolean;
  loading?: boolean;
  error?: string;
}

export function useChannelDelivery(settings: AppSettings, signedIn: boolean) {
  const scope = signedIn ? JSON.stringify([settings.backendUrl, settings.backendAuthToken]) : "";
  const [state, setState] = useState<CatalogState>({ scope: "" });
  const [pending, setPending] = useState<{ scope: string; id: string }>();
  const [failure, setFailure] = useState<{ scope: string; id: string; message: string }>();
  const [managementScope, setManagementScope] = useState<string>();
  const activeScope = useRef(scope);
  const currentSettings = useRef(settings);
  const request = useRef<AbortController>();
  const operation = useRef<string>();
  activeScope.current = scope;
  currentSettings.current = settings;

  const reload = useCallback(async () => {
    if (!scope || activeScope.current !== scope || operation.current === scope) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState((current) => ({ ...(current.scope === scope ? current : { scope }), loading: true, error: undefined }));
    try {
      const catalog = await articleApi.channelDeliveryCatalog(currentSettings.current, controller.signal);
      if (!controller.signal.aborted && activeScope.current === scope) setState({ scope, catalog });
    } catch (error) {
      if (controller.signal.aborted || activeScope.current !== scope) return;
      if ((error as { status?: number }).status === 404) {
        setState({ scope, unsupported: true });
      } else {
        setState((current) => ({ ...current, loading: false, error: message(error) }));
      }
    }
  }, [scope]);

  useEffect(() => {
    void reload();
    const refresh = () => { void reload(); };
    window.addEventListener("focus", refresh);
    return () => {
      request.current?.abort();
      window.removeEventListener("focus", refresh);
    };
  }, [reload]);

  function replaceRow(row: ChannelDeliveryConfig) {
    if (activeScope.current !== scope) return;
    setState((current) => {
      if (current.scope !== scope || !current.catalog) return current;
      const { channels } = current.catalog;
      const updated = channels.some((channel) => channel.id === row.id)
        ? channels.map((channel) => channel.id === row.id ? row : channel)
        : [...channels, row];
      return { ...current, loading: false, catalog: { ...current.catalog, channels: updated } };
    });
  }

  function beginMutation() {
    request.current?.abort();
    setState((current) => current.scope === scope && current.loading ? { ...current, loading: false } : current);
    operation.current = scope;
  }

  async function setSubscription(row: ChannelDeliveryConfig) {
    if (operation.current === scope) return;
    beginMutation();
    setFailure(undefined);
    setPending({ scope, id: row.id });
    let shouldReload = false;
    try {
      const result = await articleApi.setChannelSubscription(currentSettings.current, row.id, !row.subscribed);
      replaceRow({ ...row, subscribed: result.subscribed });
    } catch (error) {
      if (activeScope.current === scope) {
        setFailure({ scope, id: row.id, message: message(error) });
        const status = (error as { status?: number }).status;
        shouldReload = status === 403 || status === 404 || status === 409;
      }
    } finally {
      if (operation.current === scope) operation.current = undefined;
      if (activeScope.current === scope) {
        setPending(undefined);
        if (shouldReload) void reload();
      }
    }
  }

  async function save(draft: ChannelDeliveryDraft) {
    if (operation.current === scope) throw new Error("正在保存订阅，请稍后重试。");
    beginMutation();
    try {
      replaceRow(await articleApi.saveChannelDelivery(currentSettings.current, draft));
    } finally {
      if (operation.current === scope) operation.current = undefined;
    }
    if (state.scope === scope && state.catalog?.management_supported) void reload();
  }

  async function manage(action: () => Promise<void>) {
    if (!scope || operation.current === scope) throw new Error("正在保存频道设置，请稍后重试。");
    beginMutation();
    setManagementScope(scope);
    try {
      await action();
    } finally {
      if (operation.current === scope) operation.current = undefined;
      setManagementScope((current) => current === scope ? undefined : current);
    }
  }

  function setEnabled(row: ChannelDeliveryConfig) {
    return manage(async () => {
      replaceRow(await articleApi.setChannelEnabled(currentSettings.current, row.id, !row.enabled));
    });
  }

  async function saveAudience(row: ChannelDeliveryConfig, previousIds: number[] | null, allowedIds: number[] | null) {
    await manage(async () => {
      const { subscription_allowed } = await articleApi.saveChannelAudience(currentSettings.current, row.id, previousIds, allowedIds);
      replaceRow({ ...row, subscription_allowed, subscribed: subscription_allowed && row.subscribed });
    });
    void reload();
  }

  function archive(id: string) {
    return manage(async () => {
      await articleApi.archiveChannelDelivery(currentSettings.current, id);
      if (activeScope.current !== scope) return;
      setState((current) => current.scope === scope && current.catalog ? {
        ...current,
        catalog: {
          ...current.catalog,
          channels: current.catalog.channels.filter((row) => row.id !== id),
          archived_ids: [...(current.catalog.archived_ids || []), id],
        },
      } : current);
    });
  }

  async function reorder(previousIds: string[], channelIds: string[]) {
    try {
      await manage(async () => {
        const saved = await articleApi.saveChannelOrder(currentSettings.current, previousIds, channelIds);
        if (activeScope.current !== scope) return;
        const positions = new Map(saved.channel_ids.map((id, index) => [id, index]));
        setState((current) => current.scope === scope && current.catalog ? {
          ...current,
          catalog: {
            ...current.catalog,
            channels: [...current.catalog.channels].sort((a, b) => positions.get(a.id)! - positions.get(b.id)!),
          },
        } : current);
      });
    } catch (error) {
      if ((error as { status?: number }).status === 409) void reload();
      throw error;
    }
  }

  const scopedState = state.scope === scope ? state : { scope, loading: Boolean(scope) };
  return {
    ...scopedState,
    pendingChannelId: pending?.scope === scope ? pending.id : undefined,
    subscriptionError: failure?.scope === scope ? failure : undefined,
    managementPending: managementScope === scope,
    reload,
    save,
    setSubscription,
    setEnabled,
    saveAudience,
    archive,
    reorder,
  };
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "服务暂不可用，请稍后重试。";
}
