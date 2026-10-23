export type ViewKey = "today" | "inbox" | "channels" | "tasks" | "knowledge" | "content" | "saved" | "trash" | "settings";
export type ChannelColor = "green" | "blue" | "amber" | "rose" | "slate";
export type ChannelState = "active" | "paused" | "draft" | "error";
export type FeedbackValue = "useful" | "not-useful" | null;

export interface SmartChannel {
  id: string;
  name: string;
  description: string;
  prompt: string;
  topic: string;
  server: string;
  authToken: string;
  schedule: string;
  maxItems: number;
  includeTerms: string[];
  excludeTerms: string[];
  color: ChannelColor;
  state: ChannelState;
  createdAt: number;
  updatedAt: number;
  lastSyncAt?: number;
  isDemo?: boolean;
}

export interface FeedItem {
  id: string;
  channelId: string;
  title: string;
  body: string;
  timestamp: number;
  source: string;
  clickUrl?: string;
  tags: string[];
  priority: number;
  read: boolean;
  saved: boolean;
  feedback: FeedbackValue;
  stateUpdatedAt?: number;
}

export interface TrashEntry {
  id: string;
  deletedAt: number;
  item?: FeedItem;
  purged?: boolean;
}

export interface AppSettings {
  defaultServer: string;
  backendUrl: string;
  backendAuthToken: string;
  operatorName: string;
  globalAuthToken: string;
  notifications: boolean;
  notificationSound: boolean;
  launchAtLogin: boolean;
  historyWindow: "12h" | "24h" | "3d" | "7d" | "all";
  compactMode: boolean;
}

export interface AuthUser {
  id: number;
  username: string;
  displayName: string;
  staffId: number;
  role: "admin" | "member";
}

export interface AuthSession {
  backendUrl: string;
  accessToken: string;
  expiresAt: string;
  user: AuthUser;
}

export type TaskStatus = "todo" | "doing" | "done";
export type TaskListState = "active" | "done" | "deleted" | "all";
export type StaffPlatform = "qq" | "weixin" | "wecom";

export interface StaffChannel {
  id: number;
  platform: StaffPlatform;
  recipient_id: string;
  enabled: boolean;
}

export interface StaffMember {
  id: number;
  name: string;
  enabled: boolean;
  channels: StaffChannel[];
  created_at: string;
  updated_at: string;
}

export interface TaskItem {
  id: number;
  task_no: string;
  title: string;
  content: string;
  project_name?: string | null;
  assignee_id: number;
  assignee_name: string;
  creator_name: string;
  status: TaskStatus;
  priority: number;
  due_at: string | null;
  remind_enabled: boolean;
  remind_interval_minutes: number;
  next_remind_at: string | null;
  last_reminded_at: string | null;
  reminder_count: number;
  completed_at: string | null;
  source_platform: string;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskDraft {
  title: string;
  content: string;
  assignee_id: number;
  priority: number;
  due_at: string | null;
  remind_enabled: boolean;
  remind_interval_minutes: number;
}

export interface NtfyMessage {
  id: string;
  time: number;
  event: string;
  topic: string;
  title?: string;
  message: string;
  priority?: number;
  tags?: string[];
  click?: string;
}

export interface SubscriptionStatus {
  status: "idle" | "syncing" | "connected" | "reconnecting" | "error";
  message?: string;
}

export interface AppUpdateInfo {
  currentVersion: string;
  latestVersion: string;
  updateAvailable: boolean;
  downloadUrl: string;
  releaseUrl: string;
  installerAvailable: boolean;
}

export interface UpdateCheckState {
  status: "idle" | "checking" | "current" | "available" | "downloading" | "ready" | "installing" | "error";
  currentVersion?: string;
  latestVersion?: string;
  progress?: number;
  releaseUrl?: string;
  message?: string;
  // Retained for clients using the earlier release-page update bridge.
  downloadUrl?: string;
  installerAvailable?: boolean;
}

export interface ChannelRuleDraft {
  name: string;
  description: string;
  includeTerms: string[];
  excludeTerms: string[];
}
