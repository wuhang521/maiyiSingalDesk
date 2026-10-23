/// <reference types="vite/client" />

import type { AppUpdateInfo, AuthSession, NtfyMessage, UpdateCheckState } from "./types";

interface NtfyConnectionConfig {
  server: string;
  topic: string;
  token?: string;
}

interface SignalDeskBridge {
  pollMessages: (
    config: NtfyConnectionConfig & { since: string },
  ) => Promise<NtfyMessage[]>;
  publishMessage: (
    config: NtfyConnectionConfig & {
      title: string;
      message: string;
      priority?: number;
      tags?: string[];
      click?: string;
    },
  ) => Promise<NtfyMessage>;
  subscribe: (
    config: NtfyConnectionConfig & { subscriptionId: string },
  ) => Promise<{ subscriptionId: string }>;
  unsubscribe: (subscriptionId: string) => Promise<boolean>;
  onMessage: (
    callback: (event: {
      type: "message" | "status" | "notification-click";
      subscriptionId?: string;
      payload?: NtfyMessage;
      status?: string;
      error?: string;
      messageId?: string;
    }) => void,
  ) => () => void;
  openExternal: (url: string) => Promise<void>;
  showNotification: (notification: {
    title: string;
    body: string;
    messageId: string;
    silent: boolean;
  }) => Promise<boolean>;
  setLaunchAtLogin: (enabled: boolean) => Promise<boolean>;
  getAppInfo: () => Promise<{
    version: string;
    platform: string;
    launchAtLogin: boolean;
    backendUrl?: string;
  }>;
  checkForUpdates: () => Promise<UpdateCheckState | AppUpdateInfo>;
  getUpdateState?: () => Promise<UpdateCheckState>;
  downloadUpdate?: () => Promise<UpdateCheckState>;
  onUpdateState?: (callback: (state: UpdateCheckState) => void) => () => void;
  installUpdate?: () => Promise<{ started: boolean; reason?: string }>;
  loadAuthSession: () => Promise<AuthSession | null>;
  saveAuthSession: (session: AuthSession) => Promise<boolean>;
  clearAuthSession: () => Promise<boolean>;
  getVideoConnection?: (session: { backendUrl: string; accessToken: string }) => Promise<{
    baseUrl: string;
    accessToken: string;
    identityHash: string;
  } | null>;
  downloadKnowledgeAsset?: (asset: { id: string; filename: string }) => Promise<boolean>;
  knowledgeWatchStatus?: () => Promise<{ running: boolean; folder: string; message: string; files: { name: string; size: number; updatedAt: number }[] }>;
  knowledgeWatchStart?: () => Promise<{ running: boolean; folder: string; message: string; files: { name: string; size: number; updatedAt: number }[] }>;
  knowledgeWatchStop?: () => Promise<{ running: boolean; folder: string; message: string; files: { name: string; size: number; updatedAt: number }[] }>;
  toggleTaskWidget: () => Promise<{ visible: boolean; launchAtLogin: boolean }>;
  hideTaskWidget: () => Promise<boolean>;
  setTaskWidgetLocked: (locked: boolean) => Promise<boolean>;
  getTaskWidgetState: () => Promise<{ locked: boolean; collapsed: boolean }>;
  setTaskWidgetCollapsed: (collapsed: boolean) => Promise<boolean>;
  openTaskWorkspace: () => Promise<boolean>;
  onNavigate: (callback: (view: "tasks") => void) => () => void;
}

declare global {
  interface Window {
    signalDesk?: SignalDeskBridge;
  }
}

export {};
