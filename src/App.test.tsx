import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ChannelDialog } from "./components/ChannelDialog";
import { defaultSettings } from "./data/demo";
import { articleApi } from "./lib/articleApi";
import { authApi } from "./lib/authApi";
import { knowledgeApi } from "./lib/knowledgeApi";
import type { UpdateCheckState } from "./types";

vi.mock("./lib/authApi", () => ({
  authApi: {
    login: vi.fn(),
    me: vi.fn(),
    logout: vi.fn(),
  },
}));

describe("迈医协同 app", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: undefined,
    });
  });

  it("renders the operational workspace on first launch", () => {
    render(<App />);
    expect(screen.getAllByText("迈医协同").length).toBeGreaterThan(0);
    expect(screen.getByText("迈医智能协同平台")).toBeInTheDocument();
    expect(screen.getByText("今日情报")).toBeInTheDocument();
    expect(screen.getAllByText("健康管理商业情报").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("企业健康福利从报销转向持续健康管理").length,
    ).toBeGreaterThan(0);
  });

  it("uses a personal account instead of asking for backend settings", async () => {
    const user = userEvent.setup();
    const session = {
      backendUrl: defaultSettings.backendUrl,
      accessToken: "personal-token",
      expiresAt: "2026-11-16T08:00:00",
      user: {
        id: 3,
        username: "boss",
        displayName: "敏总",
        staffId: 7,
        role: "admin" as const,
      },
    };
    vi.mocked(authApi.login).mockResolvedValue(session);
    const saveAuthSession = vi.fn().mockResolvedValue(true);
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        loadAuthSession: vi.fn().mockResolvedValue(null),
        saveAuthSession,
        clearAuthSession: vi.fn().mockResolvedValue(true),
        getAppInfo: vi.fn().mockResolvedValue({
          version: "0.3.5",
          platform: "win32",
          launchAtLogin: false,
        }),
        onMessage: vi.fn().mockReturnValue(() => undefined),
        subscribe: vi.fn().mockResolvedValue({ subscriptionId: "test" }),
        unsubscribe: vi.fn().mockResolvedValue(true),
        onNavigate: vi.fn().mockReturnValue(() => undefined),
      },
    });

    render(<App />);
    await screen.findByRole("heading", { name: "登录工作台" });
    await user.type(screen.getByLabelText("账号"), "boss");
    await user.type(screen.getByLabelText("密码"), "password-2026");
    await user.click(screen.getByRole("button", { name: "继续" }));

    await waitFor(() => expect(saveAuthSession).toHaveBeenCalledWith(session));
    expect(await screen.findByText("今日情报")).toBeInTheDocument();
  });

  it("refreshes server articles when the top sync button is clicked", async () => {
    const user = userEvent.setup();
    const session = {
      backendUrl: defaultSettings.backendUrl,
      accessToken: "personal-token",
      expiresAt: "2026-11-16T08:00:00",
      user: {
        id: 3,
        username: "boss",
        displayName: "敏总",
        staffId: 7,
        role: "admin" as const,
      },
    };
    vi.mocked(authApi.login).mockResolvedValue(session);
    const upsertChannel = vi.spyOn(articleApi, "upsertChannel").mockResolvedValue({ id: "test" });
    const syncArticles = vi.spyOn(articleApi, "syncArticles").mockResolvedValue({ accepted: 0 });
    const listArticles = vi.spyOn(articleApi, "listArticles").mockResolvedValue({
      items: [],
      total: 0,
    });
    const listChannels = vi.spyOn(articleApi, "listChannels").mockResolvedValue([]);
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        loadAuthSession: vi.fn().mockResolvedValue(null),
        saveAuthSession: vi.fn().mockResolvedValue(true),
        clearAuthSession: vi.fn().mockResolvedValue(true),
        getAppInfo: vi.fn().mockResolvedValue({
          version: "0.3.6",
          platform: "win32",
          launchAtLogin: false,
        }),
        onMessage: vi.fn().mockReturnValue(() => undefined),
        subscribe: vi.fn().mockResolvedValue({ subscriptionId: "test" }),
        unsubscribe: vi.fn().mockResolvedValue(true),
        pollMessages: vi.fn().mockResolvedValue([]),
      },
    });

    try {
      render(<App />);
      await screen.findByRole("heading", { name: "登录工作台" });
      await user.type(screen.getByLabelText("账号"), "boss");
      await user.type(screen.getByLabelText("密码"), "password-2026");
      await user.click(screen.getByRole("button", { name: "继续" }));

      await waitFor(() => expect(listArticles).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: "同步" }));
      await waitFor(() => expect(listArticles).toHaveBeenCalledTimes(2));
      expect(await screen.findByText("同步完成")).toBeInTheDocument();
      expect(listChannels).toHaveBeenCalledTimes(2);
    } finally {
      upsertChannel.mockRestore();
      syncArticles.mockRestore();
      listArticles.mockRestore();
      listChannels.mockRestore();
    }
  });

  it("opens the content generation workspace without channel actions", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "内容生成" }));
    expect(screen.getByRole("tab", { name: "内容创作" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("从账号定位到平台定稿，逐步确认每一期内容")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建频道" })).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "n", ctrlKey: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps unsaved knowledge text when leaving is cancelled and changes pages only after confirmation", async () => {
    const user = userEvent.setup();
    const capabilities = vi.spyOn(knowledgeApi, "capabilities").mockResolvedValue({ asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive" });
    const items = vi.spyOn(knowledgeApi, "items").mockResolvedValue({ items: [], total: 0 });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    window.localStorage.setItem("signaldesk.settings.v1", JSON.stringify({ ...defaultSettings, backendUrl: "http://127.0.0.1:19080", backendAuthToken: "draft-test-only", operatorName: "" }));
    try {
      render(<App />);
      await user.click(screen.getByRole("button", { name: "知识库" }));
      await user.click(await screen.findByRole("button", { name: "收集资料" }));
      await user.type(screen.getByLabelText("正文 / 转写文字（选填）"), "尚未保存的知识资料正文");
      await user.click(screen.getByRole("button", { name: "知识库" }));
      expect(confirm).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "设置" }));
      expect(confirm).toHaveBeenCalledWith("当前页面有未保存的内容。确定离开并放弃这些修改吗？");
      expect(screen.getByLabelText("正文 / 转写文字（选填）")).toHaveValue("尚未保存的知识资料正文");
      expect(screen.queryByRole("heading", { name: "设置" })).not.toBeInTheDocument();

      confirm.mockReturnValueOnce(true);
      await user.click(screen.getByRole("button", { name: "设置" }));
      expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument();
      expect(screen.queryByLabelText("正文 / 转写文字（选填）")).not.toBeInTheDocument();
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(false);
    } finally {
      confirm.mockRestore(); capabilities.mockRestore(); items.mockRestore();
    }
  });

  it("opens the desktop task widget and enables login startup", async () => {
    const user = userEvent.setup();
    const toggleTaskWidget = vi.fn().mockResolvedValue({
      visible: true,
      launchAtLogin: true,
    });
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        getAppInfo: vi.fn().mockResolvedValue({
          version: "0.3.4",
          platform: "win32",
          launchAtLogin: false,
        }),
        onMessage: vi.fn().mockReturnValue(() => undefined),
        subscribe: vi.fn().mockResolvedValue({ subscriptionId: "test" }),
        unsubscribe: vi.fn().mockResolvedValue(true),
        onNavigate: vi.fn().mockReturnValue(() => undefined),
        toggleTaskWidget,
      },
    });

    render(<App />);
    await user.click(screen.getByRole("button", { name: "桌面挂件" }));

    expect(toggleTaskWidget).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("桌面挂件已打开")).toBeInTheDocument();
    await waitFor(() => {
      expect(
        JSON.parse(window.localStorage.getItem("signaldesk.settings.v1") || "{}"),
      ).toMatchObject({ launchAtLogin: true });
    });
  });

  it("keeps Windows-specific settings labels on Windows", async () => {
    const user = userEvent.setup();
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        getAppInfo: vi.fn().mockResolvedValue({
          version: "0.1.0",
          platform: "win32",
          launchAtLogin: false,
        }),
        onMessage: vi.fn().mockReturnValue(() => undefined),
        subscribe: vi.fn().mockResolvedValue({ subscriptionId: "test" }),
        unsubscribe: vi.fn().mockResolvedValue(true),
      },
    });

    render(<App />);
    await user.click(screen.getByRole("button", { name: "设置" }));

    expect(await screen.findByText("迈医协同 for Windows")).toBeInTheDocument();
    expect(screen.getByText("登录 Windows 后启动")).toBeInTheDocument();
    expect(screen.getByText("控制 Windows 系统通知行为。")).toBeInTheDocument();
    expect(screen.getByText("收到实时消息时显示 Windows 通知。")).toBeInTheDocument();
  });

  it("creates a generic smart channel from a natural-language request", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <ChannelDialog
        open
        channel={null}
        settings={defaultSettings}
        onClose={vi.fn()}
        onSave={onSave}
      />,
    );

    const prompt = screen.getByLabelText("关注需求");
    await user.type(
      prompt,
      "关注机器人行业的融资并购和新品发布。排除消费级玩具和无来源传闻。",
    );
    await user.click(screen.getByRole("button", { name: /生成频道规则/ }));
    expect(screen.getAllByDisplayValue(/机器人行业/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: /保存频道/ }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ name: expect.stringMatching(/机器人行业/) }),
    );
  });

  it("supports search, save and feedback interactions", async () => {
    const user = userEvent.setup();
    render(<App />);

    const search = screen.getByLabelText("搜索信息");
    fireEvent.change(search, { target: { value: "可编辑视频" } });
    expect(screen.getAllByText(/可编辑视频生成/).length).toBeGreaterThan(0);

    await user.click(screen.getAllByText(/可编辑视频生成/)[0]);
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    expect(screen.getByRole("button", { name: "有帮助" })).toHaveClass("active");
  });

  it("moves one article to trash and restores it", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);

    const title = "企业健康福利从报销转向持续健康管理";
    expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "删除本机消息" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: title })).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: /^回收站/ }));
    expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    expect(screen.getByText(/还有 7 天自动清除/)).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "n", ctrlKey: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "恢复文章" }));
    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem("signaldesk.trash.v1") || "[]"))
        .toEqual([]);
    });
    await user.click(screen.getByRole("button", { name: /^全部信息/ }));
    expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    confirm.mockRestore();
  });

  it("uses the macOS title bar layout on Darwin", async () => {
    const user = userEvent.setup();
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        getAppInfo: vi.fn().mockResolvedValue({
          version: "0.1.0",
          platform: "darwin",
          launchAtLogin: false,
        }),
        onMessage: vi.fn().mockReturnValue(() => undefined),
        subscribe: vi.fn().mockResolvedValue({ subscriptionId: "test" }),
        unsubscribe: vi.fn().mockResolvedValue(true),
      },
    });

    const { container } = render(<App />);

    await waitFor(() =>
      expect(container.firstElementChild).toHaveClass("platform-darwin"),
    );
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByText("迈医协同 for macOS")).toBeInTheDocument();
    expect(screen.getByText("登录 macOS 后启动")).toBeInTheDocument();
    expect(screen.getByText("控制 macOS 系统通知行为。")).toBeInTheDocument();
    expect(screen.getByText("收到实时消息时显示 macOS 通知。")).toBeInTheDocument();
  });

  it("checks GitHub releases and opens the matching installer", async () => {
    const user = userEvent.setup();
    const downloadUrl =
      "https://github.com/H59-star/SignalDesk-Releases/releases/download/v0.1.1/SignalDesk-Setup-0.1.1.exe";
    const checkForUpdates = vi.fn().mockResolvedValue({
      currentVersion: "0.1.0",
      latestVersion: "0.1.1",
      updateAvailable: true,
      downloadUrl,
      releaseUrl: "https://github.com/H59-star/SignalDesk-Releases/releases/tag/v0.1.1",
      installerAvailable: true,
    });
    const openExternal = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        getAppInfo: vi.fn().mockResolvedValue({
          version: "0.1.0",
          platform: "win32",
          launchAtLogin: false,
        }),
        onMessage: vi.fn().mockReturnValue(() => undefined),
        subscribe: vi.fn().mockResolvedValue({ subscriptionId: "test" }),
        unsubscribe: vi.fn().mockResolvedValue(true),
        checkForUpdates,
        openExternal,
      },
    });

    render(<App />);
    await user.click(screen.getByRole("button", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: "检查更新" }));

    expect(
      await screen.findByText("发现新版本 v0.1.1，下载后覆盖安装即可。"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下载 v0.1.1" }));
    expect(checkForUpdates).toHaveBeenCalledTimes(1);
    expect(openExternal).toHaveBeenCalledWith(downloadUrl);
  });

  function automaticUpdateBridge(initialState: UpdateCheckState) {
    let listener: ((state: UpdateCheckState) => void) | undefined;
    const unsubscribe = vi.fn();
    const bridge = {
      getAppInfo: vi.fn().mockResolvedValue({ version: "0.5.3", platform: "win32", launchAtLogin: false }),
      onMessage: vi.fn().mockReturnValue(() => undefined),
      subscribe: vi.fn().mockResolvedValue({ subscriptionId: "test" }),
      unsubscribe: vi.fn().mockResolvedValue(true),
      getUpdateState: vi.fn().mockResolvedValue(initialState),
      onUpdateState: vi.fn((callback: (state: UpdateCheckState) => void) => {
        listener = callback;
        return unsubscribe;
      }),
      checkForUpdates: vi.fn().mockResolvedValue({ status: "current" }),
      downloadUpdate: vi.fn().mockResolvedValue({ status: "downloading" }),
      installUpdate: vi.fn().mockResolvedValue({ started: false, reason: "已取消本次安装，可稍后重启安装。" }),
      openExternal: vi.fn().mockResolvedValue(undefined),
    };
    Object.defineProperty(window, "signalDesk", { configurable: true, value: bridge });
    return {
      bridge,
      unsubscribe,
      emit(state: UpdateCheckState) { act(() => listener?.(state)); },
    };
  }

  it("shows downloaded updates on any page and installs only after an explicit click", async () => {
    const user = userEvent.setup();
    const { bridge, emit, unsubscribe } = automaticUpdateBridge({ status: "downloading", progress: 37.5 });
    const confirm = vi.spyOn(window, "confirm");
    const { unmount } = render(<App />);
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(await screen.findByText("正在下载更新，已完成 38%。")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "更新下载进度" })).toHaveAttribute("value", "37.5");
    expect(screen.getByRole("button", { name: "正在下载…" })).toBeDisabled();
    expect(bridge.installUpdate).not.toHaveBeenCalled();
    expect(bridge.checkForUpdates).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "内容生成" }));
    emit({ status: "ready", latestVersion: "0.5.4" });
    const readyButton = screen.getByRole("button", { name: "新版已下载，重启安装" });
    expect(screen.getByText("可在方便时重启安装，保留已有账号、数据和配置。")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "内容创作" })).toBeInTheDocument();
    expect(bridge.installUpdate).not.toHaveBeenCalled();
    const settingsBefore = window.localStorage.getItem("signaldesk.settings.v1");

    await user.click(readyButton);
    await waitFor(() => expect(bridge.installUpdate).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "新版已下载，重启安装" })).toBeEnabled();
    expect(screen.getByText("已取消本次安装，可稍后重启安装。")).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("signaldesk.settings.v1")).toBe(settingsBefore);

    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByRole("button", { name: "重启并安装" })).toBeEnabled();
    bridge.installUpdate.mockResolvedValueOnce({ started: true, reason: "" });
    await user.click(screen.getByRole("button", { name: "重启并安装" }));
    expect(await screen.findByRole("button", { name: "正在安装…" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "新版已下载，重启安装" })).not.toBeInTheDocument();
    expect(bridge.openExternal).not.toHaveBeenCalled();
    unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });

  it("downloads through the desktop bridge and retries update failures", async () => {
    const user = userEvent.setup();
    const { bridge, emit } = automaticUpdateBridge({ status: "available", latestVersion: "0.5.4" });
    render(<App />);
    await user.click(screen.getByRole("button", { name: "设置" }));
    await user.click(await screen.findByRole("button", { name: "下载更新" }));
    expect(bridge.downloadUpdate).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("正在下载更新，下载完成后会提醒你安装。")).toBeInTheDocument();
    expect(bridge.openExternal).not.toHaveBeenCalled();

    emit({ status: "error", message: "网络连接中断" });
    expect(screen.getByText("更新失败：网络连接中断")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试更新" }));
    expect(bridge.checkForUpdates).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("当前已是最新版本。")).toBeInTheDocument();
    expect(bridge.installUpdate).not.toHaveBeenCalled();
  });

  it("keeps a new ready event when an older initial state arrives later", async () => {
    const { bridge, emit } = automaticUpdateBridge({ status: "idle" });
    let resolveInitial!: (state: UpdateCheckState) => void;
    bridge.getUpdateState.mockImplementationOnce(() => new Promise<UpdateCheckState>((resolve) => {
      resolveInitial = resolve;
    }));
    render(<App />);
    emit({ status: "ready", latestVersion: "0.5.4" });
    await act(async () => resolveInitial({ status: "idle" }));
    expect(screen.getByRole("button", { name: "新版已下载，重启安装" })).toBeInTheDocument();
    expect(bridge.installUpdate).not.toHaveBeenCalled();
  });

  it("prevents duplicate install confirmations and keeps the download after an install error", async () => {
    const user = userEvent.setup();
    const { bridge } = automaticUpdateBridge({ status: "ready", latestVersion: "0.5.4" });
    let rejectInstall!: (error: Error) => void;
    bridge.installUpdate.mockImplementationOnce(() => new Promise<{ started: boolean; reason: string }>((_resolve, reject) => {
      rejectInstall = reject;
    }));
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "新版已下载，重启安装" }));
    expect(screen.getByRole("button", { name: "等待重启确认…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getAllByRole("button", { name: "等待重启确认…" })).toHaveLength(2);
    expect(bridge.installUpdate).toHaveBeenCalledTimes(1);

    await act(async () => rejectInstall(new Error("安装程序暂时无法启动")));
    expect(screen.getByText("安装程序暂时无法启动")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重启并安装" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "新版已下载，重启安装" })).toBeEnabled();
    expect(bridge.downloadUpdate).not.toHaveBeenCalled();
  });

  it("replaces a default-server subscription without leaking the old connection", async () => {
    const user = userEvent.setup();
    const subscribe = vi.fn().mockResolvedValue({ subscriptionId: "channel-default" });
    const unsubscribe = vi.fn().mockResolvedValue(true);
    window.localStorage.setItem(
      "signaldesk.channels.v1",
      JSON.stringify([
        {
          id: "channel-default",
          name: "默认服务器频道",
          description: "测试默认服务器切换",
          prompt: "关注默认服务器切换后的订阅行为",
          topic: "default-server-topic",
          server: "",
          authToken: "",
          schedule: "09:00",
          maxItems: 10,
          includeTerms: [],
          excludeTerms: [],
          color: "green",
          state: "active",
          createdAt: 1,
          updatedAt: 1,
        },
      ]),
    );
    window.localStorage.setItem(
      "signaldesk.settings.v1",
      JSON.stringify({ defaultServer: "https://old.example.com" }),
    );
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        getAppInfo: vi.fn().mockResolvedValue({
          version: "0.1.0",
          platform: "win32",
          launchAtLogin: false,
        }),
        onMessage: vi.fn().mockReturnValue(() => undefined),
        subscribe,
        unsubscribe,
      },
    });

    render(<App />);
    await waitFor(() =>
      expect(subscribe).toHaveBeenCalledWith({
        server: "https://old.example.com",
        topic: "default-server-topic",
        token: undefined,
        subscriptionId: "channel-default",
      }),
    );

    await user.click(screen.getByRole("button", { name: "设置" }));
    fireEvent.change(screen.getByDisplayValue("https://old.example.com"), {
      target: { value: "https://new.example.com" },
    });

    await waitFor(() =>
      expect(subscribe).toHaveBeenCalledWith({
        server: "https://new.example.com",
        topic: "default-server-topic",
        token: undefined,
        subscriptionId: "channel-default",
      }),
    );
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(unsubscribe).toHaveBeenCalledWith("channel-default");
    expect(subscribe).toHaveBeenCalledTimes(2);
    expect(unsubscribe.mock.invocationCallOrder[0]).toBeLessThan(
      subscribe.mock.invocationCallOrder[1],
    );
  });
});
