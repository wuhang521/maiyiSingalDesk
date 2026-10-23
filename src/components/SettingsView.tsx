import {
  Bell,
  Database,
  Download,
  MonitorCog,
  LogOut,
  RefreshCw,
  RotateCcw,
  Server,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import type { AppSettings, AuthUser, UpdateCheckState } from "../types";

interface SettingsViewProps {
  settings: AppSettings;
  authUser?: AuthUser;
  appVersion: string;
  platform: string;
  updateCheck: UpdateCheckState;
  automaticUpdates?: boolean;
  installingUpdate?: boolean;
  onChange: (settings: AppSettings) => void;
  onCheckForUpdates: () => void;
  onDownloadUpdate: () => void;
  onInstallUpdate?: () => void;
  onReset: () => void;
  onLogout: () => void;
}

export function SettingsView({
  settings,
  authUser,
  appVersion,
  platform,
  updateCheck,
  automaticUpdates = false,
  installingUpdate = false,
  onChange,
  onCheckForUpdates,
  onDownloadUpdate,
  onInstallUpdate,
  onReset,
  onLogout,
}: SettingsViewProps) {
  const platformName = platform === "darwin" ? "macOS" : "Windows";
  const updateDescription = describeUpdate(updateCheck, automaticUpdates);
  const updateBusy = ["checking", "downloading", "installing"].includes(updateCheck.status) || installingUpdate;
  const progress = typeof updateCheck.progress === "number" && Number.isFinite(updateCheck.progress)
    ? Math.min(100, Math.max(0, updateCheck.progress))
    : undefined;

  return (
    <div className="settings-page">
      {authUser && (
        <SettingsSection
          icon={UserRound}
          title="账号"
          description="个人身份和任务归属由登录账号自动确定。"
        >
          <div className="settings-row">
            <div>
              <strong>{authUser.displayName}</strong>
              <span>账号 {authUser.username} · {authUser.role === "admin" ? "管理员" : "成员"}</span>
            </div>
            <span className="version-badge">已安全登录</span>
          </div>
          <div className="settings-row">
            <div>
              <strong>工作台连接</strong>
              <span>后台地址和个人访问令牌已自动配置。</span>
            </div>
            <button className="secondary-button" type="button" onClick={onLogout}>
              <LogOut size={15} />
              退出登录
            </button>
          </div>
        </SettingsSection>
      )}
      <SettingsSection
        icon={Server}
        title="连接"
        description="ntfy 实时消息连接设置。"
      >
        <label className="settings-row input-row">
          <div>
            <strong>默认 ntfy 服务器</strong>
            <span>新频道将自动使用该地址。</span>
          </div>
          <input
            value={settings.defaultServer}
            onChange={(event) => onChange({ ...settings, defaultServer: event.target.value })}
          />
        </label>
        <label className="settings-row input-row">
          <div>
            <strong>ntfy 私有频道令牌</strong>
            <span>只有私有频道需要填写，普通频道保持为空。</span>
          </div>
          <input
            type="password"
            value={settings.globalAuthToken}
            placeholder="tk_..."
            onChange={(event) => onChange({ ...settings, globalAuthToken: event.target.value })}
          />
        </label>
      </SettingsSection>

      <SettingsSection icon={Bell} title="通知" description={`控制 ${platformName} 系统通知行为。`}>
        <SettingToggle
          title="桌面通知"
          description={`收到实时消息时显示 ${platformName} 通知。`}
          checked={settings.notifications}
          onChange={(checked) => onChange({ ...settings, notifications: checked })}
        />
        <SettingToggle
          title="通知声音"
          description="关闭后通知保持静默。"
          checked={settings.notificationSound}
          onChange={(checked) => onChange({ ...settings, notificationSound: checked })}
        />
      </SettingsSection>

      <SettingsSection icon={MonitorCog} title="桌面行为" description="启动和阅读密度设置。">
        <SettingToggle
          title={`登录 ${platformName} 后启动`}
          description="保持后台连接，及时接收 ntfy 消息。"
          checked={settings.launchAtLogin}
          onChange={(checked) => onChange({ ...settings, launchAtLogin: checked })}
        />
        <SettingToggle
          title="紧凑列表"
          description="缩短信流行高，在同一屏显示更多内容。"
          checked={settings.compactMode}
          onChange={(checked) => onChange({ ...settings, compactMode: checked })}
        />
      </SettingsSection>

      <SettingsSection icon={Database} title="历史记录" description="控制从 ntfy 获取缓存消息的范围。">
        <label className="settings-row select-row">
          <div>
            <strong>同步范围</strong>
            <span>受 ntfy 服务端实际缓存时间限制。</span>
          </div>
          <select
            value={settings.historyWindow}
            onChange={(event) =>
              onChange({
                ...settings,
                historyWindow: event.target.value as AppSettings["historyWindow"],
              })
            }
          >
            <option value="12h">最近 12 小时</option>
            <option value="24h">最近 24 小时</option>
            <option value="3d">最近 3 天</option>
            <option value="7d">最近 7 天</option>
            <option value="all">服务端全部缓存</option>
          </select>
        </label>
      </SettingsSection>

      <SettingsSection icon={ShieldCheck} title="关于" description="应用版本和本地数据。">
        <div className="settings-row">
          <div>
            <strong>迈医协同 for {platformName}</strong>
            <span>版本 {appVersion}</span>
          </div>
          <span className="version-badge">Electron desktop</span>
        </div>
        <div className="settings-row">
          <div>
            <strong>软件更新</strong>
            <span role="status" aria-live="polite">{updateDescription}</span>
            {automaticUpdates && <span>更新会保留已有账号、数据和配置。安装前会请你确认重启。</span>}
            {updateCheck.status === "downloading" && (
              <progress aria-label="更新下载进度" max={100} value={progress} style={{ accentColor: "var(--green)" }} />
            )}
          </div>
          <div className="settings-actions">
            {updateCheck.status === "available" && (
              <button className="primary-button" type="button" onClick={onDownloadUpdate}>
                <Download size={15} />
                {automaticUpdates
                  ? "下载更新"
                  : updateCheck.installerAvailable ? `下载 v${updateCheck.latestVersion}` : "查看发布页"}
              </button>
            )}
            {updateCheck.status === "ready" ? (
              <button className="primary-button" type="button" disabled={installingUpdate} onClick={onInstallUpdate}>
                <RotateCcw size={15} />
                {installingUpdate ? "等待重启确认…" : "重启并安装"}
              </button>
            ) : (
              <button
                className="secondary-button"
                type="button"
                disabled={updateBusy}
                onClick={onCheckForUpdates}
              >
                <RefreshCw size={15} className={updateBusy ? "spin" : ""} />
                {updateCheck.status === "checking" ? "检查中…"
                  : updateCheck.status === "downloading" ? "正在下载…"
                    : updateCheck.status === "installing" ? "正在安装…"
                      : updateCheck.status === "error" ? "重试更新" : "检查更新"}
              </button>
            )}
          </div>
        </div>
        <div className="settings-row danger-row">
          <div>
            <strong>重置本地数据</strong>
            <span>清除频道、消息、收藏和设置并恢复示例。</span>
          </div>
          <button className="danger-button" type="button" onClick={onReset}>
            <RotateCcw size={15} />
            重置
          </button>
        </div>
      </SettingsSection>
    </div>
  );
}

function describeUpdate(updateCheck: UpdateCheckState, automaticUpdates: boolean): string {
  if (updateCheck.status === "checking") return "正在检查最新版本。";
  if (updateCheck.status === "current") return "当前已是最新版本。";
  if (updateCheck.status === "available") {
    if (automaticUpdates) return `发现新版本${updateCheck.latestVersion ? ` v${updateCheck.latestVersion}` : ""}，将自动下载。`;
    return updateCheck.installerAvailable
      ? `发现新版本 v${updateCheck.latestVersion}，下载后覆盖安装即可。`
      : `发现新版本 v${updateCheck.latestVersion}，请前往发布页下载。`;
  }
  if (updateCheck.status === "downloading") {
    const progress = updateCheck.progress;
    return typeof progress === "number" && Number.isFinite(progress)
      ? `正在下载更新，已完成 ${Math.round(Math.min(100, Math.max(0, progress)))}%。`
      : "正在下载更新，下载完成后会提醒你安装。";
  }
  if (updateCheck.status === "ready") return `新版本${updateCheck.latestVersion ? ` v${updateCheck.latestVersion}` : ""}已下载，可在方便时重启安装。`;
  if (updateCheck.status === "installing") return "正在准备重启并安装更新。";
  if (updateCheck.status === "error") return `更新失败：${updateCheck.message || "请重试更新。"}`;
  if (automaticUpdates) return "自动检查并下载更新，下载完成后由你确认重启安装。";
  return "检查 GitHub 上是否有可下载的新版本。";
}

interface SettingsSectionProps {
  icon: typeof Server;
  title: string;
  description: string;
  children: React.ReactNode;
}

function SettingsSection({ icon: Icon, title, description, children }: SettingsSectionProps) {
  return (
    <section className="settings-section">
      <div className="settings-section-heading">
        <span className="settings-icon"><Icon size={17} /></span>
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      <div className="settings-list">{children}</div>
    </section>
  );
}

interface SettingToggleProps {
  title: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

function SettingToggle({ title, description, checked, onChange }: SettingToggleProps) {
  return (
    <div className="settings-row">
      <div>
        <strong>{title}</strong>
        <span>{description}</span>
      </div>
      <button
        className={`switch ${checked ? "on" : ""}`}
        role="switch"
        aria-checked={checked}
        type="button"
        onClick={() => onChange(!checked)}
      >
        <span />
      </button>
    </div>
  );
}
