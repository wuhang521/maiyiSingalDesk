import { afterEach, describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import { VideoApiError, videoApi } from "./videoApi";

const settings = {
  ...defaultSettings,
  backendUrl: "https://api.example.com",
  backendAuthToken: "shared-key",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("video API", () => {
  it("routes project JSON through the attached service without changing the production session", async () => {
    const original = { ...settings };
    const getVideoConnection = vi.fn().mockResolvedValue({ baseUrl: "http://127.0.0.1:18865", accessToken: "local-video-capability", identityHash: "identity-1" });
    vi.stubGlobal("signalDesk", { getVideoConnection });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "project-1" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await videoApi.createProject(Object.freeze({ ...settings }), { topic: "电池为什么怕冷", mode: "standard", platforms: ["weixin"] });

    expect(getVideoConnection).toHaveBeenCalledWith({ backendUrl: settings.backendUrl, accessToken: settings.backendAuthToken });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18865/api/v1/video/projects", expect.objectContaining({
      method: "POST", redirect: "error", headers: { "Content-Type": "application/json", Authorization: "Bearer local-video-capability" },
    }));
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain(settings.backendAuthToken);
    expect(settings).toEqual(original);
  });

  it("resolves a fresh local capability for JSON and protected media requests", async () => {
    const getVideoConnection = vi.fn()
      .mockResolvedValueOnce({ baseUrl: "http://127.0.0.1:18865/", accessToken: "first-local-cap", identityHash: "identity-1" })
      .mockResolvedValueOnce({ baseUrl: "http://127.0.0.1:18865", accessToken: "second-local-cap", identityHash: "identity-1" });
    vi.stubGlobal("signalDesk", { getVideoConnection });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "project-1" }), { status: 200 }))
      .mockResolvedValueOnce(new Response("media", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await videoApi.getProject(settings, "project-1");
    const blob = await videoApi.projectFile(settings, "project-1", "skill/out/merged.wav");

    expect(getVideoConnection).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer first-local-cap");
    expect(fetchMock).toHaveBeenLastCalledWith("http://127.0.0.1:18865/api/v1/video/projects/project-1/files/skill/out/merged.wav", {
      headers: { Authorization: "Bearer second-local-cap" }, redirect: "error",
    });
    expect(blob.size).toBe(5);
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain(settings.backendAuthToken);
  });

  it("keeps the remote transport when this installation has no attached runtime", async () => {
    const getVideoConnection = vi.fn().mockResolvedValue(null);
    vi.stubGlobal("signalDesk", { getVideoConnection });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response("zip", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await videoApi.listProjects(settings);
    await videoApi.packageProject(settings, "project-1");

    expect(fetchMock.mock.calls[0][0]).toBe("https://api.example.com/api/v1/video/projects");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer shared-key");
    expect(fetchMock).toHaveBeenLastCalledWith("https://api.example.com/api/v1/video/projects/project-1/package", { headers: { Authorization: "Bearer shared-key" } });
  });

  it("does not silently send project requests to production after an attached-service error", async () => {
    vi.stubGlobal("signalDesk", { getVideoConnection: vi.fn().mockRejectedValue(new Error("账号已切换，请重试")) });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(videoApi.listProjects(settings)).rejects.toThrow("账号已切换，请重试");
    await expect(videoApi.projectFile(settings, "project-1", "clip.mp4")).rejects.toThrow("账号已切换，请重试");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "https://127.0.0.1:18865", "http://localhost:18865", "http://192.168.1.2:18865", "http://127.0.0.1",
    "http://user:secret@127.0.0.1:18865", "http://127.0.0.1:18865/api", "http://127.0.0.1:18865?secret=1",
    "http://127.0.0.1:18865#fragment", "http://127.0.0.1:65536", "http://127.0.0.1:0",
  ])("rejects an invalid attached-service address before transmitting credentials: %s", async (baseUrl) => {
    vi.stubGlobal("signalDesk", { getVideoConnection: vi.fn().mockResolvedValue({ baseUrl, accessToken: "local-cap", identityHash: "identity-1" }) });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(videoApi.listProjects(settings)).rejects.toThrow("本机视频服务连接无效");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["", "   ", "local\r\ncap", settings.backendAuthToken])("rejects invalid or production capabilities without falling back: %j", async (accessToken) => {
    vi.stubGlobal("signalDesk", { getVideoConnection: vi.fn().mockResolvedValue({ baseUrl: "http://127.0.0.1:18865", accessToken, identityHash: "identity-1" }) });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(videoApi.listProjects(settings)).rejects.toThrow("本机视频服务连接无效");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves legacy compose requests on the existing backend", async () => {
    const getVideoConnection = vi.fn().mockRejectedValue(new Error("legacy must not use sidecar"));
    vi.stubGlobal("signalDesk", { getVideoConnection });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ value: "旁白" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await videoApi.compose(settings, { target: "narration", context: { source_text: "主题" }, lockedFields: [] });

    expect(getVideoConnection).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.example.com/api/v1/video/prompts/compose");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer shared-key");
  });

  it("sends locked fields using the backend schema", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ value: { title: "新标题" } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await videoApi.compose(settings, {
      target: "brief.title",
      context: { source_text: "主题" },
      lockedFields: ["brief.audience"],
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).toMatchObject({
      target: "brief.title",
      locked_fields: ["brief.audience"],
    });
    expect(body).not.toHaveProperty("lockedFields");
  });

  it("sends project revisions and keeps budget approval separate from plan confirmation", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ id: "project-1" }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    await videoApi.reviewProject(settings, "project-1", { expected_revision: 4, stage: "inputs", decision: "approved", quote: "点击确认方案" });
    const review = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(review).toMatchObject({ expected_revision: 4, stage: "inputs" });
    expect(review).not.toHaveProperty("budget_grant");
    await videoApi.createProjectJob(settings, "project-1", { expected_revision: 4, kind: "scene", scene_id: "scene-2", idempotency_key: "one-click-only", budget_grant: { max_cost: 3, currency: "CNY", quote: "仅本次镜头最多3元" } });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toMatchObject({ expected_revision: 4, idempotency_key: "one-click-only", budget_grant: { max_cost: 3, currency: "CNY" } });
  });

  it("preserves conflict status and presents structured blocking reasons", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { message: "请先确认当前版本", missing_reviews: ["voice"] } }), { status: 428 })));
    const caught = await videoApi.getProject(settings, "project-1").catch((error) => error);
    expect(caught).toBeInstanceOf(VideoApiError);
    expect(caught.status).toBe(428);
    expect(caught.message).toBe("请先确认当前版本");
  });

  it("authenticates own project media and rejects traversal before sending requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("media", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await videoApi.projectFile(settings, "project-1", "skill/out/merged.wav");
    expect(fetchMock).toHaveBeenCalledWith("https://api.example.com/api/v1/video/projects/project-1/files/skill/out/merged.wav", { headers: { Authorization: "Bearer shared-key" } });
    await expect(videoApi.projectFile(settings, "project-1", "../other-user.mp4")).rejects.toThrow("项目文件路径无效");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("binds a batch budget to quoted scene IDs and postprocessing review to its raw avatar job", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ id: "job-1" }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    await videoApi.quoteProjectJob(settings, "project-1", "scene_batch", undefined, ["scene-1", "scene-2"]);
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.example.com/api/v1/video/projects/project-1/quote?kind=scene_batch&scene_ids=scene-1&scene_ids=scene-2");
    await videoApi.createProjectJob(settings, "project-1", { expected_revision: 7, kind: "scene_batch", scene_ids: ["scene-1", "scene-2"], idempotency_key: "batch-1", budget_grant: { max_cost: 4, currency: "CNY", quote: "仅本批两镜" } });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toMatchObject({ kind: "scene_batch", scene_ids: ["scene-1", "scene-2"], budget_grant: { currency: "CNY" } });
    await videoApi.reviewProject(settings, "project-1", { expected_revision: 7, stage: "postprocess", job_id: "avatar-raw-7", decision: "approved", quote: "看过此原片并确认去停顿" });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body as string)).toMatchObject({ stage: "postprocess", job_id: "avatar-raw-7", expected_revision: 7 });
  });
});
