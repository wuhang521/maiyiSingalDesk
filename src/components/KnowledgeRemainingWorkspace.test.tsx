import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import { knowledgeApi, type KnowledgeDetail, type KnowledgeInput } from "../lib/knowledgeApi";
import { KnowledgeWorkspace } from "./KnowledgeWorkspace";

vi.mock("../lib/knowledgeApi", async original => { const actual = await original<typeof import("../lib/knowledgeApi")>(); return { ...actual, knowledgeApi: Object.fromEntries(Object.keys(actual.knowledgeApi).map(name => [name, vi.fn()])) }; });
const api = vi.mocked(knowledgeApi); const settings = { ...defaultSettings, backendUrl: "http://127.0.0.1:19080", backendAuthToken: "synthetic-local-only" }; const at = "2026-10-28T00:00:00";
function item(n = 1, patch: Partial<KnowledgeDetail> = {}): KnowledgeDetail { return { id: `fixture-${n}`, title: `合成资料 ${n}`, source_url: "", kind: "note", publisher: "合成来源", tags: [], status: "ready", status_note: "合成已入库", revision: 4, content: `合成正文 ${n}`, summary: `合成摘要 ${n}`, method: "manual", deleted_at: null, created_at: at, updated_at: at, assets: [], history: [{ revision: 4, method: "manual", created_at: at }], ...patch }; }
const detail = () => screen.getByRole("article", { name: "知识资料详情" });
const row = async (n: number) => (await screen.findByText(`合成资料 ${n}`, { selector: ".kb-list-item strong" })).closest("button")!;
beforeEach(() => {
  vi.resetAllMocks(); api.capabilities.mockResolvedValue({ asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive", cloud_qa_enabled: true, cloud_provider: "合成服务" });
  api.items.mockResolvedValue({ items: [item()], total: 1 }); api.detail.mockImplementation(async (_s, id) => item(Number(id.split("-").at(-1)) || 1));
  api.revision.mockResolvedValue({ revision: 4, method: "manual", created_at: at, content: "合成正文" });
});
afterEach(() => vi.restoreAllMocks());

it("[KBUI-KnowledgeWorkspace-9,KBUI-KnowledgeWorkspace-25,KBUI-KnowledgeWorkspace-26,KBUI-KnowledgeWorkspace-27] list first/middle/last items, pagination boundaries and keyboard tabs", async () => {
  const user = userEvent.setup(); const library = Array.from({ length: 101 }, (_, n) => item(n + 1));
  api.items.mockImplementation(async (_s, _q, state, offset = 0) => ({ items: state === "removed" ? [] : library.slice(offset, offset + 50), total: state === "removed" ? 0 : library.length }));
  render(<KnowledgeWorkspace settings={settings} />);
  await user.click(await row(1)); expect(await within(detail()).findByRole("heading", { name: "合成资料 1" })).toBeVisible();
  await user.click(await row(25)); expect(await within(detail()).findByRole("heading", { name: "合成资料 25" })).toBeVisible();
  await user.click(await row(50)); expect(await within(detail()).findByRole("heading", { name: "合成资料 50" })).toBeVisible();
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled(); await user.click(screen.getByRole("button", { name: "下一页" }));
  await row(51); expect(api.items).toHaveBeenLastCalledWith(settings, "", "active", 50, expect.any(AbortSignal));
  await user.click(screen.getByRole("button", { name: "下一页" })); await row(101); expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "上一页" })); await row(51);
  const active = screen.getByRole("tab", { name: "全部资料" }); active.focus(); await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "待处理" })).toHaveAttribute("aria-selected", "true"); expect(screen.getByRole("tab", { name: "待处理" })).toHaveFocus();
  await user.keyboard("{End}"); expect(screen.getByRole("tab", { name: "知识回收站" })).toHaveAttribute("aria-selected", "true");
  await user.keyboard("{Home}"); expect(screen.getByRole("tab", { name: "全部资料" })).toHaveAttribute("aria-selected", "true");
}, 15000);

it("[KBUI-KnowledgeWorkspace-11,KBUI-KnowledgeWorkspace-12,KBUI-KnowledgeWorkspace-13,KBUI-KnowledgeWorkspace-16] collection fields preserve cancelled drafts and save exact source metadata", async () => {
  const user = userEvent.setup(); const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  api.collect.mockImplementation(async (_s, input) => item(1, { ...input }));
  render(<KnowledgeWorkspace settings={settings} />); await screen.findByRole("heading", { name: "合成资料 1" }); await user.click(screen.getByRole("button", { name: "收集资料" }));
  await user.type(screen.getByLabelText("资料标题（选填）"), "合成新增资料"); await user.type(screen.getByLabelText("来源 / 公众号名称（选填）"), "合成发布者");
  await user.type(screen.getByLabelText("微信链接（选填）"), "https://weixin.qq.com/sph/synthetic-only"); await user.type(screen.getByLabelText("正文 / 转写文字（选填）"), "合成保存正文");
  await user.click(screen.getByRole("button", { name: "取消" })); expect(screen.getByLabelText("资料标题（选填）")).toHaveValue("合成新增资料"); expect(api.collect).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "保存到知识库" }));
  await waitFor(() => expect(api.collect).toHaveBeenCalledWith(settings, { title: "合成新增资料", publisher: "合成发布者", source_url: "https://weixin.qq.com/sph/synthetic-only", content: "合成保存正文", tags: [] }));
  expect(screen.queryByLabelText("资料标题（选填）")).toBeNull();
});

it("[KBUI-KnowledgeWorkspace-18,KBUI-KnowledgeWorkspace-22] cloud switch clears prior evidence and citation opens exact original revision", async () => {
  const user = userEvent.setup(); api.ask.mockResolvedValue({ answer: "合成摘录", mode: "extractive", citations: [{ item_id: "fixture-2", chunk_id: "chunk", revision: 4, title: "合成资料 2", source_url: "", locator: "合成段落2", text: "可追溯正文" }] });
  render(<KnowledgeWorkspace settings={settings} />); await screen.findByRole("heading", { name: "合成资料 1" });
  await user.click(screen.getByRole("tab", { name: "知识问答" })); await user.type(screen.getByLabelText("问题或关键词"), "合成资料");
  const cloud = screen.getByRole("checkbox", { name: "使用云端 AI 综合（可选）" }); await user.click(cloud); expect(cloud).toBeChecked(); expect(screen.getByRole("button", { name: "发送片段并综合" })).toBeEnabled(); await user.click(cloud);
  await user.click(screen.getByRole("button", { name: "查找依据" })); const citation = await screen.findByRole("button", { name: /合成资料 2.*合成段落2/ });
  await user.click(citation); expect(await within(detail()).findByRole("heading", { name: "合成资料 2" })).toBeVisible(); expect(api.detail).toHaveBeenCalledWith(settings, "fixture-2", expect.any(AbortSignal));
});

it("[KBUI-KnowledgeWorkspace-1,KBUI-KnowledgeWorkspace-2,KBUI-KnowledgeWorkspace-32,KBUI-KnowledgeWorkspace-33,KBUI-KnowledgeWorkspace-38] upload selectors, supplement, retry and authenticated download retain the original item", async () => {
  const user = userEvent.setup(); const original = item(1, { kind: "file", method: "", status: "needs_input", assets: [{ id: "original", filename: "合成原件.txt", size: 10 }] });
  api.items.mockResolvedValue({ items: [original], total: 1 }); api.detail.mockResolvedValue(original); api.download.mockResolvedValue(undefined); api.retry.mockResolvedValue({ ...original, revision: 5, status: "received" });
  render(<KnowledgeWorkspace settings={settings} />); await within(detail()).findByRole("heading", { name: "合成资料 1" });
  const file = screen.getByLabelText("上传知识文件"), folder = screen.getByLabelText("导入知识文件夹"), supplement = screen.getByLabelText("补充知识文件");
  const fileClick = vi.spyOn(file as HTMLInputElement, "click"), folderClick = vi.spyOn(folder as HTMLInputElement, "click"), supplementClick = vi.spyOn(supplement as HTMLInputElement, "click");
  await user.click(screen.getByRole("button", { name: "上传文件" })); expect(fileClick).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "导入文件夹" })); expect(folderClick).toHaveBeenCalledTimes(1); expect(folder).toHaveAttribute("webkitdirectory");
  await user.click(screen.getByRole("button", { name: "补充文件" })); expect(supplementClick).toHaveBeenCalledTimes(1);
  await user.click(within(detail()).getByRole("button", { name: /合成原件.txt.*下载/ })); expect(api.download).toHaveBeenCalledExactlyOnceWith(settings, original.assets[0]);
  await user.click(screen.getByRole("button", { name: "重试识别" })); await waitFor(() => expect(api.retry).toHaveBeenCalledExactlyOnceWith(settings, original));
  expect(await screen.findByText("已重新加入识别队列。")).toBeVisible();
});

it("[KBUI-KnowledgeWorkspace-7,KBUI-KnowledgeWorkspace-8] stopping a multi-file upload keeps first completed original and never starts second", async () => {
  const user = userEvent.setup(); const library = [item()]; let complete!: (value: KnowledgeDetail) => void;
  api.items.mockImplementation(async () => ({ items: [...library], total: library.length }));
  api.collect.mockImplementation(async (_s, input: KnowledgeInput) => { const created = item(2, { ...input }); library.push(created); return created; });
  api.detail.mockImplementation(async (_s, id) => library.find(row => row.id === id)!);
  api.upload.mockReturnValue(new Promise(resolve => { complete = resolve; }));
  render(<KnowledgeWorkspace settings={settings} />); await screen.findByRole("heading", { name: "合成资料 1" });
  await user.upload(screen.getByLabelText("上传知识文件"), [new File(["合成一"], "one.txt", { type: "text/plain" }), new File(["合成二"], "two.txt", { type: "text/plain" })]);
  await waitFor(() => expect(api.upload).toHaveBeenCalledTimes(1)); await user.click(screen.getByRole("button", { name: "停止后续上传" }));
  await act(async () => complete(library[1])); expect(await screen.findByText(/本批已保存 1 个文件.*1 个未开始/)).toBeVisible();
  expect(api.collect).toHaveBeenCalledTimes(1); expect(api.upload).toHaveBeenCalledTimes(1);
  const summary = screen.getByText("查看本批文件保存结果 · 1 个"); await user.click(summary); expect(summary.closest("details")).toHaveAttribute("open"); expect(within(summary.closest("details")!).getByText(/one.txt/)).toBeVisible();
});

it("[KBUI-KnowledgeWorkspace-28] source link activation preserves target and noreferrer without navigating a real site", async () => {
  const user = userEvent.setup(); const source = "https://weixin.qq.com/sph/synthetic-only"; const original = item(1, { source_url: source }); api.items.mockResolvedValue({ items: [original], total: 1 }); api.detail.mockResolvedValue(original);
  render(<KnowledgeWorkspace settings={settings} />); const link = await screen.findByRole("link", { name: "查看原始微信链接" });
  const clicked = vi.fn((event: Event) => event.preventDefault()); link.addEventListener("click", clicked); await user.click(link);
  expect(clicked).toHaveBeenCalledTimes(1); expect(link).toHaveAttribute("href", source); expect(link).toHaveAttribute("target", "_blank"); expect(link).toHaveAttribute("rel", "noreferrer");
});
