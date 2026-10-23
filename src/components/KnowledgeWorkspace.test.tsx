import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import { knowledgeApi, type KnowledgeDetail } from "../lib/knowledgeApi";
import { KnowledgeWorkspace } from "./KnowledgeWorkspace";

vi.mock("../lib/knowledgeApi", async (original) => ({ ...await original<typeof import("../lib/knowledgeApi")>(), knowledgeApi: { capabilities: vi.fn(), items: vi.fn(), detail: vi.fn(), collect: vi.fn(), update: vi.fn(), upload: vi.fn(), download: vi.fn(), ask: vi.fn(), retry: vi.fn(), ocr: vi.fn(), confirmOcr: vi.fn(), remove: vi.fn(), restore: vi.fn(), purge: vi.fn(), revisions: vi.fn(), revision: vi.fn() } }));
const settings = { ...defaultSettings, backendUrl: "http://127.0.0.1:19080", backendAuthToken: "preview-token" };
const item: KnowledgeDetail = { id: "preview-item", title: "演示健康管理资料", source_url: "https://weixin.qq.com/sph/preview-only", kind: "channels", publisher: "演示来源", tags: [], status: "needs_input", status_note: "链接已保存，请补充视频、音频或字幕。", revision: 1, content: "", summary: "", method: "", deleted_at: null, created_at: "2026-10-08T01:00:00", updated_at: "2026-10-08T01:00:00", assets: [], history: [] };

describe("个人知识库", () => {
  it("详情先结束识别时同步列表状态，不并列显示等待与失败", async () => {
    const queued = { ...item, revision: 2, status: "received", summary: "等待 OCR" };
    const failed = { ...queued, status: "needs_input", summary: "云端 OCR 请求失败，未自动重试", status_note: "云端 OCR 请求失败，未自动重试", updated_at: "2026-10-08T01:00:01" };
    vi.mocked(knowledgeApi.items).mockResolvedValue({ items: [queued], total: 1 });
    vi.mocked(knowledgeApi.detail).mockResolvedValue(failed);
    render(<KnowledgeWorkspace settings={settings} />);
    expect(await screen.findByRole("status")).toHaveTextContent("未自动重试");
    await waitFor(() => expect(screen.queryByText("等待识别", { exact: true })).not.toBeInTheDocument());
    expect(document.querySelector(".kb-list-item")).toHaveTextContent("待补充素材");
    expect(document.querySelector(".kb-list-item")).toHaveTextContent("未自动重试");
  });
  it("OCR 需整件发送同意，失败清除同意且不重复提交", async () => {
    const user = userEvent.setup();
    const image = { ...item, assets: [{ id: "original-image", filename: "截图.png", size: 100 }] };
    vi.mocked(knowledgeApi.capabilities).mockResolvedValue({ asr_enabled: false, ocr_enabled: true, max_upload_mb: 500, scope: "private", qa_mode: "extractive" });
    vi.mocked(knowledgeApi.items).mockResolvedValue({ items: [image], total: 1 });
    vi.mocked(knowledgeApi.detail).mockResolvedValue(image);
    vi.mocked(knowledgeApi.ocr).mockRejectedValue(new Error("本次未确认，未自动重试"));
    render(<KnowledgeWorkspace settings={settings} />);
    const choice = await screen.findByRole("checkbox", { name: "同意发送选中的整份原件到云端识别" });
    expect(choice).not.toBeChecked();
    expect(screen.getByRole("button", { name: "发送文件并识别" })).toBeDisabled();
    await user.click(choice);
    await user.click(screen.getByRole("button", { name: "发送文件并识别" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("未自动重试");
    expect(choice).not.toBeChecked();
    expect(knowledgeApi.ocr).toHaveBeenCalledExactlyOnceWith(settings, image, "original-image");
  });

  it("待核对正文明确隔离，并提供本人确认入库入口", async () => {
    const user = userEvent.setup();
    const draft = { ...item, status: "needs_review", method: "doubao_ocr_draft", content: "合成识别草稿" };
    vi.mocked(knowledgeApi.items).mockResolvedValue({ items: [draft], total: 1 });
    vi.mocked(knowledgeApi.detail).mockResolvedValue(draft);
    vi.mocked(knowledgeApi.confirmOcr).mockResolvedValue({ ...draft, status: "ready", revision: 2 });
    render(<KnowledgeWorkspace settings={settings} />);
    expect(await screen.findByRole("heading", { name: "AI 识别正文 · 待核对（暂不参与检索）" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "已核对，入库" }));
    expect(knowledgeApi.confirmOcr).toHaveBeenCalledExactlyOnceWith(settings, draft);
  });
  beforeEach(() => {
    vi.resetAllMocks();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(knowledgeApi.capabilities).mockResolvedValue({ asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive" });
    vi.mocked(knowledgeApi.items).mockResolvedValue({ items: [item], total: 1 });
    vi.mocked(knowledgeApi.detail).mockResolvedValue(item);
  });

  it("只保存链接不会冒充识别成功，显示补充入口和未配置语音识别", async () => {
    render(<KnowledgeWorkspace settings={settings} />);
    const detail = screen.getByRole("article", { name: "知识资料详情" });
    expect(await within(detail).findByText("还没有可搜索的正文")).toBeVisible();
    expect(within(detail).getByText(/后台尚未启用语音识别/)).toBeVisible();
    expect(within(detail).getByRole("button", { name: "重试识别" })).toBeDisabled();
    expect(screen.getByText("仅本人可见")).toBeVisible();
  });

  it("保存失败保留输入且阻止重复提交", async () => {
    const user = userEvent.setup();
    let reject!: (e: Error) => void;
    vi.mocked(knowledgeApi.collect).mockImplementation(() => new Promise((_, r) => { reject = r; }));
    render(<KnowledgeWorkspace settings={settings} />);
    await screen.findByRole("heading", { name: item.title });
    await user.click(screen.getByRole("button", { name: "收集资料" }));
    await user.type(screen.getByLabelText("资料标题（选填）"), "新资料");
    await user.type(screen.getByLabelText("正文 / 转写文字（选填）"), "健康管理测试正文");
    await user.click(screen.getByRole("button", { name: "保存到知识库" }));
    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
    reject(new Error("服务暂不可用"));
    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByLabelText("资料标题（选填）")).toHaveValue("新资料");
    expect(screen.getByLabelText("正文 / 转写文字（选填）")).toHaveValue("健康管理测试正文");
    expect(knowledgeApi.collect).toHaveBeenCalledTimes(1);
  });

  it("只保护实际修改的新资料，清空或保存后解除离开拦截", async () => {
    const user = userEvent.setup();
    const blocked = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    vi.mocked(knowledgeApi.collect).mockResolvedValue({ ...item, title: "新资料", content: "待保存正文", status: "ready" });
    render(<KnowledgeWorkspace settings={settings} />);
    await screen.findByRole("heading", { name: item.title });
    await user.click(screen.getByRole("button", { name: "收集资料" }));
    expect(blocked()).toBe(false);
    const text = screen.getByLabelText("正文 / 转写文字（选填）");
    await user.type(text, "待保存正文");
    expect(blocked()).toBe(true);
    await user.clear(text);
    expect(blocked()).toBe(false);
    await user.type(screen.getByLabelText("资料标题（选填）"), "新资料");
    await user.type(text, "待保存正文");
    expect(blocked()).toBe(true);
    await user.click(screen.getByRole("button", { name: "保存到知识库" }));
    await waitFor(() => expect(screen.queryByLabelText("正文 / 转写文字（选填）")).not.toBeInTheDocument());
    expect(blocked()).toBe(false);
  });

  it("已有资料只在修改后拦截，恢复原值不拦截，保存失败保留保护", async () => {
    const user = userEvent.setup();
    const blocked = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    vi.mocked(knowledgeApi.update).mockRejectedValue(new Error("保存失败，请重试"));
    const { unmount } = render(<KnowledgeWorkspace settings={settings} />);
    await screen.findByRole("heading", { name: item.title });
    await user.click(screen.getByRole("button", { name: "补充 / 修订正文" }));
    expect(blocked()).toBe(false);
    const text = screen.getByLabelText("正文 / 转写文字");
    await user.type(text, "尚未保存的修订");
    expect(blocked()).toBe(true);
    await user.clear(text);
    expect(blocked()).toBe(false);
    await user.type(text, "尚未保存的修订");
    await user.click(screen.getByRole("button", { name: "保存修订" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败，请重试");
    expect(text).toHaveValue("尚未保存的修订");
    expect(blocked()).toBe(true);
    unmount();
    expect(blocked()).toBe(false);
  });

  it("修订必须带现有版本，失败保留草稿", async () => {
    const user = userEvent.setup();
    vi.mocked(knowledgeApi.update).mockRejectedValue(new Error("资料已被更新，请刷新"));
    render(<KnowledgeWorkspace settings={settings} />);
    await screen.findByRole("heading", { name: item.title });
    await user.click(screen.getByRole("button", { name: "补充 / 修订正文" }));
    await user.type(screen.getByLabelText("正文 / 转写文字"), "修订内容");
    await user.click(screen.getByRole("button", { name: "保存修订" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("资料已被更新");
    expect(screen.getByLabelText("正文 / 转写文字")).toHaveValue("修订内容");
    expect(knowledgeApi.update).toHaveBeenCalledWith(settings, item, expect.objectContaining({ content: "修订内容" }));
  });

  it("问答明确摘录模式并提供版本和时间出处", async () => {
    const user = userEvent.setup();
    vi.mocked(knowledgeApi.ask).mockResolvedValue({ answer: "[1] 测试原文摘录", mode: "extractive", citations: [{ index: 1, item_id: item.id, chunk_id: "cue-1", revision: 2, title: item.title, source_url: item.source_url, locator: "00:00:01,000 --> 00:00:03,000", text: "测试原文摘录" }] });
    render(<KnowledgeWorkspace settings={settings} />);
    await user.click(screen.getByRole("tab", { name: "知识问答" }));
    await user.type(screen.getByLabelText("问题或关键词"), "健康管理");
    await user.click(screen.getByRole("button", { name: "查找依据" }));
    expect(await screen.findByText("[1] 测试原文摘录")).toBeVisible();
    expect(screen.getByRole("button", { name: /00:00:01,000.*版本 2/ })).toBeVisible();
    expect(screen.getByText(/默认采用关键词检索与原文摘录/)).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "使用云端 AI 综合（可选）" })).toBeDisabled();
    expect(knowledgeApi.ask).toHaveBeenCalledWith(settings, "健康管理", false);
  });

  it("云端问答需主动选择，说明上传费用；失败保留问题，可改回摘录", async () => {
    const user = userEvent.setup();
    vi.mocked(knowledgeApi.capabilities).mockResolvedValue({ asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive", cloud_qa_enabled: true, cloud_provider: "DeepSeek" });
    let reject!: (e: Error) => void;
    vi.mocked(knowledgeApi.ask).mockImplementation(() => new Promise((_, r) => { reject = r; }));
    render(<KnowledgeWorkspace settings={settings} />);
    await user.click(screen.getByRole("tab", { name: "知识问答" }));
    const choice = screen.getByRole("checkbox", { name: "使用云端 AI 综合（可选）" });
    expect(choice).not.toBeChecked();
    expect(screen.getByText(/向DeepSeek发送本次问题及最多 5 段/)).toBeVisible();
    await user.click(choice);
    await user.type(screen.getByLabelText("问题或关键词"), "健康管理");
    await user.click(screen.getByRole("button", { name: "发送片段并综合" }));
    expect(screen.getByRole("button", { name: "正在云端综合…" })).toBeDisabled();
    expect(choice).toBeDisabled();
    reject(new Error("云端问答超时，未自动重试。"));
    expect(await screen.findByRole("alert")).toHaveTextContent("未自动重试");
    expect(screen.getByLabelText("问题或关键词")).toHaveValue("健康管理");
    expect(knowledgeApi.ask).toHaveBeenCalledExactlyOnceWith(settings, "健康管理", true);
    await user.click(screen.getByRole("button", { name: "改用原文摘录" }));
    expect(screen.getByRole("button", { name: "查找依据" })).toBeEnabled();
  });

  it("AI 答案明确不是原文，保留出处，切换模式清除旧结果", async () => {
    const user = userEvent.setup();
    vi.mocked(knowledgeApi.capabilities).mockResolvedValue({ asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive", cloud_qa_enabled: true, cloud_provider: "DeepSeek" });
    vi.mocked(knowledgeApi.ask).mockResolvedValue({ answer: "先保存来源。[1]", mode: "cloud", citations: [{ index: 1, item_id: item.id, chunk_id: "cue-1", revision: 2, title: item.title, source_url: item.source_url, locator: "字符 1–40", text: "先保存来源" }] });
    render(<KnowledgeWorkspace settings={settings} />);
    await user.click(screen.getByRole("tab", { name: "知识问答" }));
    await user.click(screen.getByRole("checkbox", { name: "使用云端 AI 综合（可选）" }));
    await user.type(screen.getByLabelText("问题或关键词"), "健康管理");
    await user.click(screen.getByRole("button", { name: "发送片段并综合" }));
    expect(await screen.findByRole("heading", { name: "AI 综合 · 请核对出处" })).toBeVisible();
    expect(screen.getByText(/模型结论仍可能有误/)).toBeVisible();
    expect(screen.getByRole("button", { name: /字符 1–40.*版本 2/ })).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: "使用云端 AI 综合（可选）" }));
    expect(screen.queryByText("先保存来源。[1]")).not.toBeInTheDocument();
  });

  it("删除确认可取消，回收站恢复使用版本", async () => {
    const user = userEvent.setup();
    render(<KnowledgeWorkspace settings={settings} />);
    await screen.findByRole("heading", { name: item.title });
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await user.click(screen.getByRole("button", { name: "移入回收站" }));
    expect(knowledgeApi.remove).not.toHaveBeenCalled();
    const removed = { ...item, revision: 2, deleted_at: "2026-10-08T02:00:00" };
    vi.mocked(knowledgeApi.items).mockResolvedValue({ items: [removed], total: 1 });
    vi.mocked(knowledgeApi.detail).mockResolvedValue(removed);
    vi.mocked(knowledgeApi.restore).mockResolvedValue({ ...item, revision: 3 });
    await user.click(screen.getByRole("tab", { name: "知识回收站" }));
    await user.click(await screen.findByRole("button", { name: "恢复资料" }));
    await waitFor(() => expect(knowledgeApi.restore).toHaveBeenCalledWith(settings, removed));
  });

  it("永久删除必须输入确认词，可取消；失败保留确认，阻止重复提交", async () => {
    const user = userEvent.setup();
    const removed = { ...item, revision: 2, deleted_at: "2026-10-08T02:00:00" };
    vi.mocked(knowledgeApi.items).mockResolvedValue({ items: [removed], total: 1 });
    vi.mocked(knowledgeApi.detail).mockResolvedValue(removed);
    let reject!: (e: Error) => void;
    vi.mocked(knowledgeApi.purge).mockImplementation(() => new Promise((_, r) => { reject = r; }));
    render(<KnowledgeWorkspace settings={settings} />);
    await user.click(await screen.findByRole("button", { name: "永久删除…" }));
    expect(screen.getByRole("button", { name: "确认永久删除" })).toBeDisabled();
    await user.type(screen.getByLabelText("输入“永久删除”确认"), "wrong");
    expect(screen.getByRole("button", { name: "确认永久删除" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "取消永久删除" }));
    expect(knowledgeApi.purge).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "永久删除…" }));
    await user.type(screen.getByLabelText("输入“永久删除”确认"), "永久删除");
    await user.click(screen.getByRole("button", { name: "确认永久删除" }));
    expect(screen.getByRole("button", { name: "正在永久删除…" })).toBeDisabled();
    reject(new Error("资料已被更新"));
    expect(await screen.findByRole("alert")).toHaveTextContent("资料已被更新");
    expect(screen.getByLabelText("输入“永久删除”确认")).toHaveValue("永久删除");
    expect(knowledgeApi.purge).toHaveBeenCalledExactlyOnceWith(settings, removed, "永久删除");
  });
});
