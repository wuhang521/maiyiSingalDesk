import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import { videoApi, VideoApiError, type VideoJobQuote, type VideoProject } from "../lib/videoApi";
import { VideoGenerationWorkspace } from "./VideoGenerationWorkspace";

const settings = { ...defaultSettings, backendUrl: "https://example.test", backendAuthToken: "test-session" };
const fixture = (): VideoProject => ({
  id: "project-1", topic: "为什么手机电池怕冷", mode: "standard", platforms: ["视频号", "抖音", "小红书"], revision: 2, status: "planned", locked_fields: [], reviews: {}, jobs: [], updated_at: "2026-10-19T10:00:00",
  plan: {
    brief: { title: "手机遇冷为什么掉电", audience: "普通手机用户", core_message: "解释低温下电池表现" },
    audio_plan: { narration_style: "平静清晰", voice: "温和女声", music: "轻柔", sound_notes: "重点降低音乐" },
    persona: "小知，资料讲解员", visual_style: "清晰图解", layout: "竖屏，字幕居下", platform_variants: { weixin: { title: "电池为什么怕冷", cover_direction: "冬日手机场景", caption: "了解低温与手机电池", layout_notes: "底部字幕留安全区" } },
    scenes: [{ id: "scene-1", title: "冬天的疑问", duration: 6, narration: "低温时手机为什么更快没电？", visual_intent: "冬天使用手机的生活场景", seedance_prompt: "竖屏冬天生活场景，不生成文字" }, { id: "scene-2", title: "解释电池表现", duration: 8, narration: "从电池内部反应解释低温现象。", visual_intent: "用简洁图解回答开头", seedance_prompt: "简洁电池图解" }],
    interest_hooks: [{ id: "hook-1", kind: "opening", scene_id: "scene-1", viewer_question: "为什么掉电", copy: "冬天掉电快，电池真的坏了吗？", promise: "解释低温表现", payoff_scene_id: "scene-2" }],
  },
});

async function openSaved(project = fixture()) {
  vi.mocked(videoApi.listProjects).mockResolvedValue({ items: [project] });
  vi.mocked(videoApi.getProject).mockResolvedValue(project);
  render(<VideoGenerationWorkspace settings={settings} />);
  await userEvent.click(await screen.findByRole("button", { name: /手机遇冷为什么掉电/ }));
  await screen.findByLabelText("视频标题");
  return project;
}

beforeEach(() => {
  vi.spyOn(videoApi, "listProjects").mockResolvedValue({ items: [] });
  vi.spyOn(videoApi, "projectCapabilities").mockResolvedValue({});
  vi.spyOn(videoApi, "getProject").mockResolvedValue(fixture());
  vi.spyOn(videoApi, "createProject").mockResolvedValue({ ...fixture(), revision: 1, plan: {} });
  vi.spyOn(videoApi, "composeProject").mockResolvedValue(fixture());
  vi.spyOn(videoApi, "saveProject").mockResolvedValue({ ...fixture(), revision: 3 });
  vi.spyOn(videoApi, "reviewProject").mockResolvedValue(fixture());
  vi.spyOn(videoApi, "quoteProjectJob").mockResolvedValue({ amount: 1.8, currency: "CNY", basis: "6 秒，配置每秒 0.3 元", revision: 2 });
  vi.spyOn(videoApi, "createProjectJob").mockResolvedValue({ id: "job-1", kind: "scene", scene_id: "scene-1", revision: 2, status: "queued", created_at: "", updated_at: "" });
  vi.spyOn(videoApi, "refreshProjectJob").mockResolvedValue({ id: "job-1", kind: "scene", revision: 2, status: "succeeded", created_at: "", updated_at: "" });
  vi.spyOn(videoApi, "projectFile").mockResolvedValue(new Blob(["audio"], { type: "audio/wav" }));
  vi.spyOn(videoApi, "uploadAvatarAsset").mockResolvedValue({ path: "assets/presenter.png", name: "presenter.png", sha256: "hash", size: 5 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Hermes 视频项目工作台", () => {
  it("只需主题，默认普通视频；没有虚构项目或假预览", async () => {
    render(<VideoGenerationWorkspace settings={settings} />);
    expect(screen.getByRole("radio", { name: "普通 AI 视频" })).toBeChecked();
    expect(screen.getByText("AI 策划调用按 Hermes 已配置模型的账单计费，单独于视频制作预算。")).toBeVisible();
    expect(screen.getByLabelText("视频主题")).toHaveValue("");
    expect(screen.getByRole("button", { name: "AI 规划方案" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("视频主题"), "电池怕冷的原因");
    await waitFor(() => expect(screen.getByRole("button", { name: "AI 规划方案" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "AI 规划方案" }));
    expect(videoApi.createProject).toHaveBeenCalledWith(settings, { topic: "电池怕冷的原因", mode: "standard", platforms: ["视频号", "抖音", "小红书"] });
    await screen.findByLabelText("视频标题");
    expect(videoApi.composeProject).toHaveBeenCalledWith(settings, "project-1", { expected_revision: 1 });
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "播放工作预览" })).not.toBeInTheDocument();
  });

  it("明确选择数字人后才创建数字人项目", async () => {
    render(<VideoGenerationWorkspace settings={settings} />);
    await screen.findByText("还没有已保存的视频");
    await userEvent.type(screen.getByLabelText("视频主题"), "解释海洋潮汐");
    await userEvent.click(screen.getByRole("radio", { name: "数字人口播" }));
    await userEvent.click(screen.getByRole("button", { name: "AI 规划方案" }));
    expect(videoApi.createProject).toHaveBeenCalledWith(settings, expect.objectContaining({ mode: "avatar" }));
  });

  it("策划缺配置会保留空项目，不伪装成成功方案", async () => {
    vi.mocked(videoApi.composeProject).mockRejectedValue(new VideoApiError("请配置策划模型和 API 凭证", 503));
    render(<VideoGenerationWorkspace settings={settings} />);
    await userEvent.type(screen.getByLabelText("视频主题"), "电池");
    await waitFor(() => expect(screen.getByRole("button", { name: "AI 规划方案" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "AI 规划方案" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请配置策划模型");
    expect(screen.getByText("项目已保存，等待 AI 策划")).toBeInTheDocument();
    expect(screen.queryByLabelText("本镜头旁白")).not.toBeInTheDocument();
  });

  it("恢复真实方案，显示人设、兴趣钩子与答案位置，模型参数放高级", async () => {
    await openSaved();
    expect(screen.getByLabelText("讲解人设")).toHaveValue("小知，资料讲解员");
    expect(screen.getByText("冬天掉电快，电池真的坏了吗？")).toBeInTheDocument();
    expect(screen.getByText("提出：冬天的疑问（约 0–6 秒） · 回答：解释电池表现（约 6–14 秒）")).toBeInTheDocument();
    expect(screen.getByLabelText("模型提示词")).not.toBeVisible();
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    expect(screen.getByLabelText("模型提示词")).toBeVisible();
  });

  it("手工编辑与锁定可保存，保存前禁止确认和生成", async () => {
    await openSaved();
    await userEvent.clear(screen.getByLabelText("本镜头旁白"));
    await userEvent.type(screen.getByLabelText("本镜头旁白"), "这是新旁白。");
    await userEvent.click(screen.getByRole("button", { name: "锁定本镜头旁白" }));
    expect(screen.getByLabelText("本镜头旁白")).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认内容方案" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "核算当前镜头", hidden: true })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存方案" }));
    expect(videoApi.saveProject).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ expected_revision: 2, locked_fields: ["scenes.scene-1.narration"], plan: expect.objectContaining({ scenes: expect.arrayContaining([expect.objectContaining({ narration: "这是新旁白。" })]) }) }));
  });

  it("AI 局部调整包含当前镜头范围，且不创建生成任务", async () => {
    await openSaved();
    await userEvent.click(screen.getByRole("tab", { name: /解释电池表现/ }));
    await userEvent.click(screen.getByRole("button", { name: "AI 只改这一镜" }));
    expect(videoApi.composeProject).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ expected_revision: 2, instruction: "仅调整 分镜 scene-2，保留其他模块和已锁定字段。" }));
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("四阶段分别确认，记录实际按钮操作；试听未生成时不允许确认听过", async () => {
    await openSaved();
    vi.mocked(videoApi.reviewProject).mockResolvedValue({ ...fixture(), reviews: { inputs: { revision: 2, decision: "approved", quote: "确认方案", confirmed_at: "" } } });
    expect(screen.getByRole("button", { name: "确认声音试听" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认生成方案" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认成片验收" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "确认内容方案" }));
    expect(videoApi.reviewProject).toHaveBeenCalledWith(settings, "project-1", { expected_revision: 2, stage: "inputs", decision: "approved", quote: "用户在 Hermes 客户端点击“确认内容方案”（项目版本 2）" });
    await userEvent.click(screen.getByText("不需要声音试听"));
    await waitFor(() => expect(screen.getByRole("button", { name: "明确跳过试听" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "明确跳过试听" }));
    expect(videoApi.reviewProject).toHaveBeenLastCalledWith(settings, "project-1", expect.objectContaining({ stage: "voice", decision: "skipped" }));
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("过期版本保存失败时保留编辑，提醒重新载入", async () => {
    await openSaved();
    vi.mocked(videoApi.saveProject).mockRejectedValue(new VideoApiError("revision conflict", 409));
    await userEvent.clear(screen.getByLabelText("视频标题"));
    await userEvent.type(screen.getByLabelText("视频标题"), "我的编辑标题");
    await userEvent.click(screen.getByRole("button", { name: "保存方案" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("项目版本已更新");
    expect(screen.getByLabelText("视频标题")).toHaveValue("我的编辑标题");
  });

  it("旧费用确认模式报价未知或服务未配置时不能提交付费任务", async () => {
    await openSaved();
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({ amount: null, currency: "RH", basis: "未配置价格", blocked_reason: "生成服务尚未配置" });
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    await userEvent.click(screen.getByRole("button", { name: "核算当前镜头" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("暂无法核算")).toBeInTheDocument();
    expect(within(approval).getByRole("alert")).toHaveTextContent("生成服务尚未配置");
    await userEvent.click(within(approval).getByRole("checkbox"));
    expect(within(approval).getByRole("button", { name: "授权并提交本次任务" })).toBeDisabled();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("明确金额、币种、范围和单次授权后才提交，并带幂等键", async () => {
    await openSaved();
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    await userEvent.click(screen.getByRole("button", { name: "核算当前镜头" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("1.8 元")).toBeInTheDocument();
    expect(within(approval).getByText("生成当前镜头 · 冬天的疑问")).toBeInTheDocument();
    expect(within(approval).getByRole("button", { name: "授权并提交本次任务" })).toBeDisabled();
    await userEvent.click(within(approval).getByRole("checkbox"));
    await userEvent.clear(within(approval).getByLabelText("本次预算上限"));
    await userEvent.type(within(approval).getByLabelText("本次预算上限"), "1");
    expect(within(approval).getByRole("button", { name: "授权并提交本次任务" })).toBeDisabled();
    await userEvent.clear(within(approval).getByLabelText("本次预算上限"));
    await userEvent.type(within(approval).getByLabelText("本次预算上限"), "2");
    await userEvent.click(within(approval).getByRole("button", { name: "授权并提交本次任务" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ kind: "scene", scene_id: "scene-1", expected_revision: 2, idempotency_key: expect.any(String), budget_grant: { max_cost: 2, currency: "CNY", quote: expect.stringContaining("不含后续重做或其他任务") } }));
  });

  it("只显示真实完成媒体，使用本人鉴权文件接口试听", async () => {
    const project = fixture();
    project.reviews.inputs = { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" };
    project.jobs = [{ id: "voice-1", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "skill/out/merged.wav" }, created_at: "", updated_at: "" }];
    const create = vi.fn().mockReturnValue("blob:voice");
    vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn(); });
    await openSaved(project);
    expect(screen.getByRole("button", { name: "确认声音试听" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "播放配音试听" }));
    expect(videoApi.projectFile).toHaveBeenCalledWith(settings, "project-1", "skill/out/merged.wav");
    await waitFor(() => expect(screen.getByLabelText("配音试听")).toHaveAttribute("src", "blob:voice"));
    expect(screen.getByLabelText("配音试听")).toHaveAttribute("preload", "metadata");
    fireEvent.play(screen.getByLabelText("配音试听"));
    expect(screen.getByRole("button", { name: "确认声音试听" })).toBeDisabled();
    fireEvent.playing(screen.getByLabelText("配音试听"));
    expect(screen.getByRole("button", { name: "确认声音试听" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "下载发布包" })).toBeDisabled();
  });

  it.each(["voice", "render"] as const)("%s 文件加载失败可重载原文件，实际播放后才允许确认", async (kind) => {
    const project = fixture();
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].filter(stage => kind !== "voice" || stage !== "voice").map(stage => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    const file = kind === "voice" ? "skill/out/merged.wav" : "render/master.mp4";
    project.jobs = [{ id: "media-1", kind, revision: 2, status: "succeeded", result: kind === "voice" ? { audio_path: file } : { path: file }, created_at: "", updated_at: "" }];
    const revoke = vi.fn();
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn().mockReturnValueOnce("blob:first").mockReturnValueOnce("blob:reload");
      static revokeObjectURL = revoke;
    });
    await openSaved(project);
    await userEvent.click(screen.getByRole("button", { name: kind === "voice" ? "播放配音试听" : "查看生成视频" }));
    const playerLabel = kind === "voice" ? "配音试听" : "生成视频预览";
    const confirmation = kind === "voice" ? "确认声音试听" : "确认成片验收";
    const player = await screen.findByLabelText(playerLabel);
    expect(player).toHaveAttribute("preload", "metadata");
    fireEvent.play(player);
    fireEvent.error(player);
    expect(await screen.findByRole("alert")).toHaveTextContent("当前文件加载失败。请重新加载现有文件后再试听或预览。");
    expect(screen.getByRole("button", { name: confirmation })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "重新加载现有文件" }));
    await waitFor(() => expect(screen.getByLabelText(playerLabel)).toHaveAttribute("src", "blob:reload"));
    expect(revoke).toHaveBeenCalledWith("blob:first");
    expect(videoApi.projectFile).toHaveBeenCalledTimes(2);
    expect(videoApi.projectFile).toHaveBeenNthCalledWith(1, settings, "project-1", file);
    expect(videoApi.projectFile).toHaveBeenNthCalledWith(2, settings, "project-1", file);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: confirmation })).toBeDisabled();
    fireEvent.playing(screen.getByLabelText(playerLabel));
    expect(screen.getByRole("button", { name: confirmation })).toBeEnabled();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
    expect(videoApi.reviewProject).not.toHaveBeenCalled();
  });

  it("未保存修改切换项目可取消，刷新已在处理的任务仍读取最新状态", async () => {
    const project = fixture(); project.jobs = [{ id: "running-1", kind: "scene", revision: 2, status: "running", created_at: "", updated_at: "" }];
    await openSaved(project);
    vi.mocked(videoApi.refreshProjectJob).mockRejectedValue(new VideoApiError("任务正在处理", 409));
    await userEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect(videoApi.getProject).toHaveBeenCalledTimes(2));
    await userEvent.type(screen.getByLabelText("视频标题"), "修改");
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await userEvent.click(screen.getByRole("button", { name: "新建视频" }));
    expect(screen.getByLabelText("视频标题")).toHaveValue("手机遇冷为什么掉电修改");
    expect(window.confirm).toHaveBeenCalled();
  });

  it("数字人人像需上传和授权确认，动作在主页面可编辑", async () => {
    const project = fixture(); project.mode = "avatar";
    project.plan.avatar = { motion: "双手轻轻摊开", hands: "双手下垂", posture: "站直", image_type: "D" };
    vi.mocked(videoApi.saveProject).mockImplementation(async (_settings, _id, payload) => ({ ...project, plan: payload.plan, locked_fields: payload.locked_fields, revision: 3 }));
    vi.stubGlobal("URL", class extends URL { static createObjectURL = vi.fn().mockReturnValue("blob:picture"); static revokeObjectURL = vi.fn(); });
    await openSaved(project);
    expect(screen.getByLabelText("人物动作")).toBeVisible();
    expect(screen.getByLabelText("人物动作")).toHaveValue("双手轻轻摊开");
    expect(screen.getByRole("button", { name: "确认内容方案" })).toBeDisabled();
    await userEvent.upload(screen.getByLabelText("数字人人物图"), new File(["image"], "presenter.png", { type: "image/png" }));
    expect(videoApi.uploadAvatarAsset).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ name: "presenter.png", role: "avatar_image" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /拥有使用此人物图/ }));
    await userEvent.click(screen.getByRole("button", { name: "保存方案" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认内容方案" })).toBeEnabled());
    expect(videoApi.saveProject).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ plan: expect.objectContaining({ avatar: expect.objectContaining({ image_path: "assets/presenter.png", image_use_confirmed: true }) }) }));
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("退出登录后不让上一账号的延迟策划结果重新出现", async () => {
    let complete: (project: VideoProject) => void = () => {};
    vi.mocked(videoApi.composeProject).mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    const mounted = render(<VideoGenerationWorkspace settings={settings} />);
    await screen.findByText("还没有已保存的视频");
    await userEvent.type(screen.getByLabelText("视频主题"), "延迟结果测试");
    await userEvent.click(screen.getByRole("button", { name: "AI 规划方案" }));
    await waitFor(() => expect(videoApi.composeProject).toHaveBeenCalled());
    mounted.rerender(<VideoGenerationWorkspace settings={{ ...settings, backendAuthToken: "" }} />);
    complete(fixture());
    await waitFor(() => expect(screen.getByLabelText("视频主题")).toHaveValue(""));
    expect(screen.queryByLabelText("视频标题")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("请先登录");
  });

  it("人设与平台方案使用同一份可编辑数据保存", async () => {
    await openSaved();
    await userEvent.clear(screen.getByLabelText("讲解人设"));
    await userEvent.type(screen.getByLabelText("讲解人设"), "用生活例子解释的知识讲解员");
    await userEvent.click(screen.getByText("视频号", { selector: "summary" }));
    await userEvent.clear(screen.getByLabelText("视频号发布文案"));
    await userEvent.type(screen.getByLabelText("视频号发布文案"), "冷天掉电快，先了解原因。");
    await userEvent.click(screen.getByRole("button", { name: "保存方案" }));
    expect(videoApi.saveProject).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ plan: expect.objectContaining({ persona: "用生活例子解释的知识讲解员", platform_variants: { weixin: expect.objectContaining({ caption: "冷天掉电快，先了解原因。", cover_direction: "冬日手机场景", layout_notes: "底部字幕留安全区" }) } }) }));
  });

  it("主流程一次授权固定的缺失镜头名单，不自动开启后续合成", async () => {
    const project = fixture();
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [{ id: "voice-1", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" }];
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({ amount: 4.2, currency: "CNY", basis: "两镜分别计费", revision: 2, scope: "scene_batch", scene_ids: ["scene-1", "scene-2"], segment_count: 2, duration_seconds: 14 });
    await openSaved(project);
    await userEvent.click(screen.getByRole("button", { name: "核算剩余全部镜头" }));
    expect(videoApi.quoteProjectJob).toHaveBeenCalledWith(settings, "project-1", "scene_batch", undefined);
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("冬天的疑问、解释电池表现")).toBeInTheDocument();
    await userEvent.click(within(approval).getByRole("checkbox"));
    await userEvent.click(within(approval).getByRole("button", { name: "授权并提交本次任务" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ kind: "scene_batch", scene_ids: ["scene-1", "scene-2"], budget_grant: expect.objectContaining({ max_cost: 4.2, currency: "CNY" }) }));
    expect(vi.mocked(videoApi.createProjectJob).mock.calls[0][2]).not.toHaveProperty("scene_id");
    expect(videoApi.createProjectJob).toHaveBeenCalledTimes(1);
  });

  it("数字人核算整条真实分段，企业共享Key显示人民币而不是RH余额", async () => {
    const project = fixture(); project.mode = "avatar";
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [{ id: "voice-1", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav", avatar_preflight: { total_seconds: 13.2, segment_count: 2 } }, created_at: "", updated_at: "" }];
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({ amount: 2.64, currency: "CNY", basis: "企业现金可靠报价", revision: 2, scope: "avatar", credential_type: "SHARED", duration_seconds: 13.2, segment_count: 2, segments: [{ duration: 7 }, { duration: 6.2 }] });
    await openSaved(project);
    expect(screen.getByLabelText("镜头预计时长")).toHaveAttribute("max", "14");
    expect(screen.getByRole("checkbox", { name: /去除数字人口播停顿/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "将字幕烧录到成片画面" })).not.toBeChecked();
    await userEvent.click(screen.getByRole("tab", { name: /解释电池表现/ }));
    await userEvent.click(screen.getByRole("button", { name: "核算整条数字人口播" }));
    expect(videoApi.quoteProjectJob).toHaveBeenCalledWith(settings, "project-1", "avatar", undefined);
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("2.64 元")).toBeInTheDocument();
    expect(within(approval).getByText(/13.2 秒 · 2 段/)).toBeInTheDocument();
    expect(within(approval).getByText("人民币（CNY） · 企业级共享 Key")).toBeInTheDocument();
    expect(within(approval).getByText(/RH币余额不可替代模型调用预算/)).toBeInTheDocument();
    await userEvent.click(within(approval).getByRole("checkbox"));
    await userEvent.click(within(approval).getByRole("button", { name: "授权并提交本次任务" }));
    const payload = vi.mocked(videoApi.createProjectJob).mock.calls[0][2];
    expect(payload.kind).toBe("avatar");
    expect(payload).not.toHaveProperty("scene_id");
  });

  it("批次部分成功时复用已有镜头，主流程只提示缺失的一镜", async () => {
    const project = fixture();
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [
      { id: "voice-1", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" },
      { id: "partial-batch", kind: "scene_batch", revision: 2, status: "failed", result: { scene_ids: ["scene-1", "scene-2"], scene_results: [{ scene_id: "scene-1", status: "succeeded", path: "scene-1.mp4" }, { scene_id: "scene-2", status: "failed", error: "模型失败" }] }, created_at: "", updated_at: "" },
    ];
    await openSaved(project);
    expect(screen.getByText(/本次只制作 1 个缺失镜头/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看镜头：冬天的疑问" })).toBeInTheDocument();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("去停顿必须先播放本版本原片，确认绑定原片任务且不自动合成", async () => {
    const project = fixture(); project.mode = "avatar";
    project.plan.postproduction = { trim_pauses: true, burn_subtitles: false };
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [
      { id: "voice-1", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" },
      { id: "avatar-2", kind: "avatar", revision: 2, status: "succeeded", result: { path: "raw-avatar.mp4" }, created_at: "", updated_at: "" },
    ];
    vi.stubGlobal("URL", class extends URL { static createObjectURL = vi.fn().mockReturnValue("blob:avatar"); static revokeObjectURL = vi.fn(); });
    await openSaved(project);
    expect(screen.getByRole("button", { name: "确认原片与后处理" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "打开数字人原片" }));
    const player = await screen.findByLabelText("生成视频预览");
    expect(screen.getByRole("button", { name: "确认原片与后处理" })).toBeDisabled();
    fireEvent.playing(player);
    await userEvent.click(screen.getByRole("button", { name: "确认后处理并继续" }));
    expect(videoApi.reviewProject).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ stage: "postprocess", job_id: "avatar-2", expected_revision: 2, decision: "approved" }));
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("官方估算与软件预留分开展示，默认预留且不足预留时禁止提交", async () => {
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({
      amount: 2.9808, reservation_amount: 4.1732, currency: "CNY", revision: 2,
      pricing_kind: "estimate", provider_hard_cap: false,
      basis: "按固定画幅与时长估算，逐镜预留额外时长与余量",
      source_url: "https://docs.volcengine.com/docs/ark/model-pricing?lang=zh",
      verified_at: "2026-10-22", model: "doubao-seedance-2-0-mini-260615",
    });
    await openSaved();
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    await userEvent.click(screen.getByRole("button", { name: "核算当前镜头" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("官方刊例价估算")).toBeInTheDocument();
    expect(within(approval).getByText("2.9808 元")).toBeInTheDocument();
    expect(within(approval).getByText("建议预算预留")).toBeInTheDocument();
    expect(within(approval).getByText("4.1732 元")).toBeInTheDocument();
    expect(within(approval).getByLabelText("本次预算上限")).toHaveValue(4.1732);
    expect(within(approval).getByRole("link", { name: "查看官方计费说明" })).toHaveAttribute("href", "https://docs.volcengine.com/docs/ark/model-pricing?lang=zh");
    expect(within(approval).getByText(/核验于 2026-10-22/)).toBeInTheDocument();
    expect(within(approval).getByText("doubao-seedance-2-0-mini-260615")).not.toBeVisible();
    const consent = within(approval).getByRole("checkbox", { name: /预留不是云端硬上限/ });
    expect(consent).toHaveAccessibleName(/已提交的请求可能继续计费.*超预算会停止后续新请求/);
    expect(within(approval).queryByText(/在此上限内产生费用/)).not.toBeInTheDocument();
    await userEvent.click(consent);
    const budget = within(approval).getByLabelText("本次预算上限");
    await userEvent.clear(budget);
    await userEvent.type(budget, "3");
    expect(within(approval).getByRole("button", { name: "授权并提交本次任务" })).toBeDisabled();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
    await userEvent.clear(budget);
    await userEvent.type(budget, "4.1732");
    await userEvent.click(within(approval).getByRole("button", { name: "授权并提交本次任务" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({ budget_grant: { max_cost: 4.1732, currency: "CNY", quote: expect.stringContaining("预留不是云端硬上限") } }));
  });

  it("配音软件预留不冒充官方估算，保留供应商结算与重做确认说明", async () => {
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({
      amount: 0.8, reservation_amount: 0.8, currency: "CNY", revision: 2,
      pricing_kind: "reservation", provider_hard_cap: false,
      basis: "配音与逐词识别按软件预算预留",
      budget_notice: "包含配音、清理与对齐可能产生的请求。",
      source_url: "https://help.aliyun.com/zh/model-studio/model-pricing",
    });
    await openSaved();
    await userEvent.click(screen.getByRole("button", { name: "单独核算配音试听" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("软件预算预留")).toBeInTheDocument();
    expect(within(approval).queryByText("官方刊例价估算")).not.toBeInTheDocument();
    expect(within(approval).queryByText("建议预算预留")).not.toBeInTheDocument();
    expect(within(approval).getByText("包含配音、清理与对齐可能产生的请求。")).toBeInTheDocument();
    expect(within(approval).getByRole("checkbox")).toHaveAccessibleName(/供应商实际结算.*重做和后续任务需另行确认/);
  });

  it.each([
    "javascript:alert(1)", "http://docs.volcengine.com/docs/ark/model-pricing",
    "https://docs.volcengine.com.attacker.test/pricing", "https://user:secret@help.aliyun.com/pricing",
    "https://www.runninghub.cn:8443/pricing",
  ])("不把非可信来源变成官方计费链接：%s", async (source_url) => {
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({ amount: 1, currency: "CNY", basis: "预留", pricing_kind: "reservation", provider_hard_cap: false, source_url });
    await openSaved();
    await userEvent.click(screen.getByRole("button", { name: "单独核算配音试听" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).queryByRole("link", { name: "查看官方计费说明" })).not.toBeInTheDocument();
  });

  it("任务费用区分已知用量与供应商返回，缺少记录保持待对账", async () => {
    const project = fixture();
    project.jobs = [
      { id: "scene-cost", kind: "scene", scene_id: "scene-1", revision: 2, status: "succeeded", created_at: "", updated_at: "", result: { path: "scene.mp4", billing: { currency: "CNY", known_cost: 2.9808, complete: false, source: "provider_usage", unknown_requests: 1 } } },
      { id: "avatar-cost", kind: "avatar", revision: 2, status: "succeeded", created_at: "", updated_at: "", result: { path: "avatar.mp4", billing: { currency: "CNY", known_cost: 0.36, complete: true, source: "provider_report", third_party_reported_cny: 0.12, note: "第三方金额与供应商金额可能重叠，未直接合计。" } } },
    ];
    await openSaved(project);
    expect(screen.getByText(/已知用量费用：2.9808 元/)).toHaveTextContent("另有 1 次请求待对账");
    expect(screen.getByText(/供应商返回金额：0.36 元/)).toHaveTextContent("用量记录完整");
    expect(screen.getByText("第三方返回金额（包含关系待核）：0.12 元")).toBeInTheDocument();
    expect(screen.getByText("第三方金额与供应商金额可能重叠，未直接合计。")).toBeInTheDocument();
    expect(screen.queryByText(/供应商返回金额：0.48 元/)).not.toBeInTheDocument();
    expect(screen.queryByText(/实际扣款|已结清/)).not.toBeInTheDocument();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("普通视频显示 RunningHub 共享 Key，镜头规格来自当前通道", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ scene: {
      ready: true, provider: "runninghub", model: "Seedance 2.0 Mini", credential_type: "SHARED",
      currency: "CNY", credential_source: "runninghub_shared_key", constraints: {
        duration_min: 4, duration_max: 15, duration_step: 1, resolutions: ["480p", "720p"],
        ratios: ["9:16"], default_resolution: "720p", default_ratio: "9:16",
      },
    } });
    await openSaved();
    expect(await screen.findByText(/普通 AI 视频使用 RunningHub（Seedance 2.0 Mini）/)).toHaveTextContent("已接入企业级共享 Key，费用按本次官方报价币种结算");
    const duration = screen.getByLabelText("镜头预计时长");
    expect(duration).toHaveAttribute("min", "4");
    expect(duration).toHaveAttribute("max", "15");
    expect(duration).toHaveAttribute("step", "1");
    expect(screen.getByText(/当前通道单镜头为 4～15 秒.*默认 720p · 9:16/)).toBeVisible();
  });

  it("通道规格变化时更新输入边界，未提供规格不沿用 Seedance 默认限制", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ scene: {
      ready: true, provider: "runninghub", constraints: {
        duration_min: 3, duration_max: 9, duration_step: 3, resolutions: ["480p"],
        ratios: ["9:16"], default_resolution: "480p", default_ratio: "9:16",
      },
    } });
    await openSaved();
    expect(screen.getByLabelText("镜头预计时长")).toHaveAttribute("min", "3");
    expect(screen.getByLabelText("镜头预计时长")).toHaveAttribute("max", "9");
    expect(screen.getByLabelText("镜头预计时长")).toHaveAttribute("step", "3");
    cleanup();
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ scene: { ready: false, provider: "runninghub", reason: "规格待核验" } });
    await openSaved();
    expect(screen.getByLabelText("镜头预计时长")).not.toHaveAttribute("min");
    expect(screen.getByLabelText("镜头预计时长")).not.toHaveAttribute("max");
    expect(screen.getByText("当前通道的镜头规格待后台核验。")).toBeVisible();
  });

  it("RunningHub 实时报价绑定校验凭据、批次和人民币预算，不沿用历史报价", async () => {
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({
      amount: 3, reservation_amount: 3.6, pricing_kind: "estimate", provider_hard_cap: false,
      provider: "runninghub", pricing_source: "runninghub_price_preview", credential_type: "SHARED",
      currency: "CNY", basis: "RunningHub 对本批次参数返回的预估价格", revision: 2,
      scope: "scene_batch", scene_ids: ["scene-1", "scene-2"], quote_fingerprint: "quote-exact-123",
      request_fingerprint: "request-exact-123", route: "/openapi/v2/test-model",
      source_url: "https://www.runninghub.cn/runninghub-api-doc-cn/", quoted_at: "2026-10-22T12:00:00+08:00",
      expires_at: "2099-10-22T12:05:00+08:00",
    });
    const project = fixture();
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [{ id: "voice-1", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" }];
    await openSaved(project);
    await userEvent.click(screen.getByRole("button", { name: "核算剩余全部镜头" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("RunningHub 实时报价")).toBeInTheDocument();
    expect(within(approval).getByText("RunningHub", { exact: true })).toBeInTheDocument();
    expect(within(approval).queryByText("官方刊例价估算")).not.toBeInTheDocument();
    expect(within(approval).getByText("人民币（CNY） · 企业级共享 Key")).toBeInTheDocument();
    expect(within(approval).getByText("报价时间")).toBeInTheDocument();
    expect(within(approval).getByText("报价有效至")).toBeInTheDocument();
    expect(within(approval).getByLabelText("本次预算上限")).toHaveValue(3.6);
    expect(within(approval).getByRole("link", { name: "查看官方计费说明" })).toHaveAttribute("href", "https://www.runninghub.cn/runninghub-api-doc-cn/");
    await userEvent.click(within(approval).getByRole("checkbox"));
    await userEvent.click(within(approval).getByRole("button", { name: "授权并提交本次任务" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({
      kind: "scene_batch", scene_ids: ["scene-1", "scene-2"], quote_fingerprint: "quote-exact-123",
      budget_grant: { max_cost: 3.6, currency: "CNY", quote: expect.stringContaining("预留不是云端硬上限") },
    }));
  });

  it.each([
    [{ quote_fingerprint: undefined }, "校验凭据"],
    [{ amount: null, reservation_amount: null }, "暂无法核算"],
    [{ currency: "RH" }, "需取得人民币或美元报价"],
    [{ currency: "UNKNOWN" as VideoJobQuote["currency"] }, "币种尚未核验"],
    [{ expires_at: "2000-01-01T00:00:00Z" }, "已过期"],
  ] as Array<[Partial<VideoJobQuote>, string]>)("RunningHub 报价不完整、未知、币种不符或过期时禁止付费：%s", async (changes, message) => {
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({ amount: 1, reservation_amount: 1.2,
      currency: "CNY", provider: "runninghub", pricing_source: "runninghub_price_preview",
      credential_type: "SHARED", basis: "实时核算", quote_fingerprint: "valid-fingerprint", ...changes });
    await openSaved();
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    await userEvent.click(screen.getByRole("button", { name: "核算当前镜头" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText(new RegExp(message))).toBeInTheDocument();
    await userEvent.click(within(approval).getByRole("checkbox"));
    expect(within(approval).getByRole("button", { name: "授权并提交本次任务" })).toBeDisabled();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("历史任务通道只读自身记录，当前 RunningHub 不改写旧火山任务", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ scene: { ready: true, provider: "runninghub", model: "Seedance 2.0 Mini" } });
    const project = fixture();
    project.jobs = [
      { id: "old-ark", kind: "scene", scene_id: "scene-1", revision: 1, status: "succeeded", created_at: "", updated_at: "", estimated_cost: { amount: 2, currency: "CNY", basis: "旧火山报价", provider: "ark" }, result: { path: "old.mp4" } },
      { id: "new-rh", kind: "scene", scene_id: "scene-2", revision: 2, status: "succeeded", created_at: "", updated_at: "", estimated_cost: { amount: 1, currency: "CNY", basis: "RunningHub报价", provider: "runninghub" }, result: { path: "new.mp4" } },
    ];
    await openSaved(project);
    const oldTask = screen.getByText("生成当前镜头 · 冬天的疑问").closest("li")!;
    const newTask = screen.getByText("生成当前镜头 · 解释电池表现").closest("li")!;
    expect(within(oldTask).getByText("生成通道：火山方舟")).toBeInTheDocument();
    expect(within(oldTask).queryByText("生成通道：RunningHub")).not.toBeInTheDocument();
    expect(within(newTask).getByText("生成通道：RunningHub")).toBeInTheDocument();
  });

  it("提交时报价变化保持真实重算提示，不误报项目版本冲突", async () => {
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({ amount: 1, reservation_amount: 1.2,
      currency: "CNY", provider: "runninghub", credential_type: "SHARED", basis: "实时核算",
      quote_fingerprint: "price-before-change" });
    vi.mocked(videoApi.createProjectJob).mockRejectedValue(new VideoApiError("RunningHub 报价已变化，请重新核算后再授权。", 409));
    await openSaved();
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    await userEvent.click(screen.getByRole("button", { name: "核算当前镜头" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    await userEvent.click(within(approval).getByRole("checkbox"));
    await userEvent.click(within(approval).getByRole("button", { name: "授权并提交本次任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("RunningHub 报价已变化，请重新核算后再授权。");
    expect(screen.queryByText(/项目版本已更新/)).not.toBeInTheDocument();
  });

  it("RunningHub 官方返回美元时按美元显示并提交预算，不换算成人民币", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ scene: {
      ready: true, provider: "runninghub", model: "Seedance 2.0 Mini", credential_type: "SHARED",
      currency: null, supported_currencies: ["CNY", "USD"],
    } });
    vi.mocked(videoApi.quoteProjectJob).mockResolvedValue({
      amount: 0.6, reservation_amount: 0.72, pricing_kind: "estimate", provider_hard_cap: false,
      provider: "runninghub", pricing_source: "runninghub_price_preview", credential_type: "SHARED",
      currency: "USD", basis: "官方预估返回 USD，未做汇率换算", quote_fingerprint: "usd-quote-6s",
    });
    await openSaved();
    expect(await screen.findByText(/普通 AI 视频使用 RunningHub/)).toHaveTextContent("按本次官方报价币种结算");
    expect(screen.queryByText(/费用按人民币核算/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    await userEvent.click(screen.getByRole("button", { name: "核算当前镜头" }));
    const approval = await screen.findByRole("region", { name: "本次费用授权" });
    expect(within(approval).getByText("0.6 美元（USD）")).toBeInTheDocument();
    expect(within(approval).getByText("0.72 美元（USD）")).toBeInTheDocument();
    expect(within(approval).getByText("美元（USD） · 企业级共享 Key")).toBeInTheDocument();
    expect(within(approval).getByText(/本次软件预算 · 美元（USD）/)).toBeInTheDocument();
    expect(within(approval).queryByText("0.6 元")).not.toBeInTheDocument();
    expect(within(approval).getByLabelText("本次预算上限")).toHaveValue(0.72);
    await userEvent.click(within(approval).getByRole("checkbox"));
    expect(within(approval).getByRole("button", { name: "授权并提交本次任务" })).toBeEnabled();
    await userEvent.click(within(approval).getByRole("button", { name: "授权并提交本次任务" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", expect.objectContaining({
      quote_fingerprint: "usd-quote-6s", budget_grant: { max_cost: 0.72, currency: "USD", quote: expect.stringContaining("软件预算预留 0.72 USD") },
    }));
  });

  it("美元费用保持原币种，原始金额缺币种和批次缺回执均保持待对账", async () => {
    const project = fixture();
    project.jobs = [
      { id: "usd-known", kind: "scene", scene_id: "scene-1", revision: 2, status: "succeeded", created_at: "", updated_at: "", result: { path: "usd.mp4", billing: { currency: "USD", known_cost: 0.6, complete: true, source: "provider_report" } } },
      { id: "usd-unknown", kind: "scene", scene_id: "scene-2", revision: 2, status: "needs_attention", created_at: "", updated_at: "", result: { billing: { currency: "USD", known_cost: 0, complete: false, source: "provider_report", unknown_requests: 1, reported_amount: 0.7, reported_currency: "UNKNOWN", note: "回执缺少币种，尚未确认是否与授权币种一致。" } } },
      { id: "usd-batch-unknown", kind: "scene_batch", revision: 2, status: "needs_attention", created_at: "", updated_at: "", result: { billing: { currency: "USD", known_cost: 0, complete: false, source: "provider_report", unknown_requests: 2 } } },
    ];
    await openSaved(project);
    expect(screen.getByText(/供应商返回金额：0.6 美元（USD）/)).toHaveTextContent("用量记录完整");
    expect(screen.queryByText(/供应商返回金额：0.6 元/)).not.toBeInTheDocument();
    const unknown = screen.getByText("生成当前镜头 · 解释电池表现").closest("li")!;
    expect(within(unknown).getByText(/费用待对账/)).toHaveTextContent("另有 1 次请求待对账");
    expect(within(unknown).getByText("供应商返回原始金额 0.7，币种待核")).toBeInTheDocument();
    expect(within(unknown).getByText("回执缺少币种，尚未确认是否与授权币种一致。")).toBeInTheDocument();
    expect(within(unknown).queryByText(/供应商返回金额：0 /)).not.toBeInTheDocument();
    const batch = screen.getByText("制作剩余全部镜头").closest("li")!;
    expect(within(batch).getByText(/费用待对账/)).toHaveTextContent("另有 2 次请求待对账");
    expect(within(batch).queryByText(/供应商返回金额：0 /)).not.toBeInTheDocument();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
  });

  it("直接模式点击生成配音，不询价也不弹预算确认", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false });
    const project = fixture();
    project.reviews.inputs = { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" };
    await openSaved(project);
    expect(screen.getByText("云端生成会按服务商规则计费。")).toBeVisible();
    expect(screen.queryByRole("button", { name: /核算/ })).not.toBeInTheDocument();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "生成配音试听" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", {
      expected_revision: 2, kind: "voice", generation_consent: true, idempotency_key: expect.any(String),
    });
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "本次费用授权" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("本次预算上限")).not.toBeInTheDocument();
  });

  it("直接模式固定缺失镜头名单，复用已完成素材并指定 RunningHub", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false,
      scene: { ready: true, provider: "runninghub", model: "Seedance 2.0 Mini", credential_type: "SHARED" } });
    const project = fixture();
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [
      { id: "voice", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" },
      { id: "partial", kind: "scene_batch", revision: 2, status: "failed", result: { scene_results: [{ scene_id: "scene-1", status: "succeeded", path: "first.mp4" }, { scene_id: "scene-2", status: "failed" }] }, created_at: "", updated_at: "" },
    ];
    await openSaved(project);
    expect(screen.getByText(/本次制作 1 个缺失镜头/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "生成剩余全部镜头" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", {
      expected_revision: 2, kind: "scene_batch", scene_ids: ["scene-2"], expected_provider: "runninghub",
      generation_consent: true, idempotency_key: expect.any(String),
    });
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
    expect(screen.queryByText(/实时报价|预算预留|币种结算/)).not.toBeInTheDocument();
  });

  it("直接模式数字人生成整条口播，合成另由点击启动", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false });
    const project = fixture(); project.mode = "avatar";
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [{ id: "voice", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav", avatar_preflight: { total_seconds: 13.2, segment_count: 2 } }, created_at: "", updated_at: "" }];
    await openSaved(project);
    await userEvent.click(screen.getByRole("button", { name: "生成整条数字人口播" }));
    const payload = vi.mocked(videoApi.createProjectJob).mock.calls[0][2];
    expect(payload).toEqual({ expected_revision: 2, kind: "avatar", generation_consent: true, idempotency_key: expect.any(String) });
    expect(payload).not.toHaveProperty("scene_id");
    expect(payload).not.toHaveProperty("budget_grant");
    expect(videoApi.createProjectJob).toHaveBeenCalledTimes(1);
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
  });

  it("火山直连使用已核验通道提交整批与当前镜头，不改历史任务标签", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false,
      scene: { ready: true, provider: "seedance", model: "doubao-seedance-2-0-mini-260615", credential_source: "hermes" },
      scene_batch: { ready: true, provider: "seedance", model: "doubao-seedance-2-0-mini-260615", credential_source: "hermes" } });
    const project = fixture();
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [
      { id: "voice", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" },
      { id: "old-rh", kind: "scene", revision: 1, status: "succeeded", estimated_cost: { provider: "runninghub", amount: null, currency: "USD", basis: "历史记录" }, result: { path: "old.mp4" }, created_at: "", updated_at: "" },
    ];
    await openSaved(project);
    expect(screen.getByText(/普通 AI 视频使用火山方舟直连（Seedance 2.0 Mini），已复用 Hermes 的火山凭证/)).toBeVisible();
    expect(screen.getByText("生成通道：RunningHub")).toBeVisible();
    expect(screen.queryByRole("region", { name: "本次费用授权" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "生成剩余全部镜头" }));
    expect(videoApi.createProjectJob).toHaveBeenLastCalledWith(settings, "project-1", {
      expected_revision: 2, kind: "scene_batch", scene_ids: ["scene-1", "scene-2"], expected_provider: "seedance",
      generation_consent: true, idempotency_key: expect.any(String),
    });
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    await userEvent.click(screen.getByRole("button", { name: "生成当前镜头" }));
    expect(videoApi.createProjectJob).toHaveBeenLastCalledWith(settings, "project-1", {
      expected_revision: 2, kind: "scene", scene_id: "scene-1", expected_provider: "seedance",
      generation_consent: true, idempotency_key: expect.any(String),
    });
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
  });

  it.each([
    { ready: false, provider: "seedance", reason: "火山模型尚未开通" },
    { ready: true, provider: "ark", reason: "" },
    { ready: true, provider: undefined, reason: "" },
  ])("未就绪或未知普通视频通道不能提交：%j", async (capability) => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false, scene: capability });
    const project = fixture();
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [{ id: "voice", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" }];
    await openSaved(project);
    expect(screen.getByRole("button", { name: "生成剩余全部镜头" })).toBeDisabled();
    expect(screen.getAllByText(capability.reason || "普通视频生成通道尚未核验，请刷新后重试。").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    expect(screen.getByRole("button", { name: "生成当前镜头" })).toBeDisabled();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
  });

  it("数字人显示并保持 RunningHub，普通视频通道不可用不影响数字人", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false,
      scene: { ready: false, provider: "seedance", reason: "火山模型尚未开通" },
      avatar: { ready: true, provider: "oldx-runninghub" } });
    const project = fixture(); project.mode = "avatar";
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [{ id: "voice", kind: "voice", revision: 2, status: "succeeded", result: { audio_path: "voice.wav" }, created_at: "", updated_at: "" }];
    await openSaved(project);
    expect(screen.getByText(/数字人口播使用 RunningHub，按已确认的整条配音生成人物视频/)).toBeVisible();
    expect(screen.queryByText(/普通 AI 视频使用火山方舟/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "生成整条数字人口播" }));
    expect(videoApi.createProjectJob).toHaveBeenLastCalledWith(settings, "project-1", {
      expected_revision: 2, kind: "avatar", generation_consent: true, idempotency_key: expect.any(String),
    });
  });

  it("历史医疗审核标记不限制生成，不展示技术审核字段或伪造审核结果", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false, scene: { ready: true, provider: "runninghub" } });
    const project = fixture();
    project.medical_gate = { required: true, cleared: false, reason: "医疗审稿尚未完成" };
    project.plan.medical = { is_medical: true, publishable: false, cleared: false };
    project.reviews = Object.fromEntries(["inputs", "voice", "generation"].map((stage) => [stage, { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" }]));
    project.jobs = [{ id: "old-cost", kind: "scene", revision: 1, status: "succeeded", created_at: "", updated_at: "", result: { path: "old.mp4", billing: { currency: "USD", known_cost: 0.6, complete: true, source: "provider_report" } } }];
    await openSaved(project);
    await userEvent.click(screen.getByText("高级：模型提示词和项目配置"));
    expect(screen.getByRole("button", { name: "生成当前镜头" })).toBeEnabled();
    expect(screen.queryByText(/供应商返回金额|0.6 美元/)).not.toBeInTheDocument();
    expect(screen.queryByText("医疗内容审核与依据")).not.toBeInTheDocument();
    expect(screen.queryByText(/医疗审稿尚未完成|publishable|cleared/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "生成当前镜头" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", {
      expected_revision: 2, kind: "scene", scene_id: "scene-1", generation_consent: true,
      expected_provider: "runninghub", idempotency_key: expect.any(String),
    });
    expect(project.medical_gate).toEqual({ required: true, cleared: false, reason: "医疗审稿尚未完成" });
    expect(project.plan.medical).toEqual({ is_medical: true, publishable: false, cleared: false });
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
  });

  it("医疗主题仍先确认内容，再生成配音；确认只记录内容方案", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false });
    const project = fixture();
    project.topic = "体检发现尿酸高";
    project.medical_gate = { required: true, cleared: false, reason: "旧版医疗审核" };
    const reviewed: VideoProject = { ...project, reviews: { inputs: { revision: 2, decision: "approved", quote: "用户确认内容方案", confirmed_at: "" } } };
    vi.mocked(videoApi.reviewProject).mockResolvedValue(reviewed);
    vi.mocked(videoApi.getProject).mockResolvedValue(reviewed);
    await openSaved(project);
    expect(screen.getByRole("button", { name: "确认方案并继续" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "生成配音试听" })).not.toBeInTheDocument();
    expect(videoApi.createProjectJob).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认方案并继续" }));
    expect(videoApi.reviewProject).toHaveBeenCalledWith(settings, "project-1", {
      expected_revision: 2, stage: "inputs", decision: "approved", quote: expect.stringContaining("确认内容方案"),
    });
    await userEvent.click(await screen.findByRole("button", { name: "生成配音试听" }));
    expect(videoApi.createProjectJob).toHaveBeenCalledWith(settings, "project-1", {
      expected_revision: 2, kind: "voice", generation_consent: true, idempotency_key: expect.any(String),
    });
    expect(project.medical_gate?.cleared).toBe(false);
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
  });

  it("直接生成的未知提交结果重试复用幂等键", async () => {
    vi.mocked(videoApi.projectCapabilities).mockResolvedValue({ cost_review_required: false });
    vi.mocked(videoApi.createProjectJob).mockRejectedValueOnce(new VideoApiError("提交结果暂时未知，请重试读取任务。", 502));
    const project = fixture();
    project.reviews.inputs = { revision: 2, decision: "approved", quote: "已确认", confirmed_at: "" };
    await openSaved(project);
    await userEvent.click(screen.getByRole("button", { name: "生成配音试听" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("提交结果暂时未知");
    await userEvent.click(screen.getByRole("button", { name: "生成配音试听" }));
    const calls = vi.mocked(videoApi.createProjectJob).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1][2].idempotency_key).toBe(calls[0][2].idempotency_key);
    expect(videoApi.quoteProjectJob).not.toHaveBeenCalled();
  });
});
