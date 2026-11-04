import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { defaultSettings as settings } from "../data/demo";
import { knowledgeApi, type KnowledgeDetail, type KnowledgeCapabilities, type KnowledgeOrganization as Organization } from "../lib/knowledgeApi";
import { KnowledgeAsr } from "./KnowledgeAsr";
import { KnowledgeOcr } from "./KnowledgeOcr";
import { KnowledgeVideoOcr } from "./KnowledgeVideoOcr";
import { KnowledgeOrganization } from "./KnowledgeOrganization";
import { KnowledgeSemantic } from "./KnowledgeSemantic";
import { KnowledgeMediaMerge } from "./KnowledgeMediaMerge";

vi.mock("../lib/knowledgeApi", async original => { const actual = await original<typeof import("../lib/knowledgeApi")>(); return { ...actual, knowledgeApi: Object.fromEntries(Object.keys(actual.knowledgeApi).map(name => [name, vi.fn()])) }; });
const api = vi.mocked(knowledgeApi); const at = "2026-10-28T00:00:00";
const item = (patch: Partial<KnowledgeDetail> = {}): KnowledgeDetail => ({ id: "media-controls", title: "合成媒体", source_url: "", kind: "file", publisher: "", tags: [], status: "ready", status_note: "", revision: 4, content: "合成已核对正文", summary: "", method: "manual", deleted_at: null, created_at: at, updated_at: at, assets: [], history: [{ revision: 4, method: "manual", created_at: at }], ...patch });
const caps = { asr_enabled: false, ocr_enabled: true, max_upload_mb: 500, scope: "private", qa_mode: "extractive", cloud_asr_enabled: true, cloud_asr_provider: "合成服务", silk_available: true, video_ocr_enabled: true, organization_enabled: true, semantic_enabled: true } as KnowledgeCapabilities;
beforeEach(() => { vi.resetAllMocks(); });

it("[KBUI-KnowledgeAsr-1,KBUI-KnowledgeAsr-4] existing ASR re-submit drawer and original switch require fresh consent", async () => {
  const user = userEvent.setup(), submit = vi.fn().mockResolvedValue(undefined);
  render(<KnowledgeAsr item={item({ method: "qwen_asr_reviewed", assets: [{ id: "one", filename: "one.wav", size: 10 }, { id: "two", filename: "two.aud", size: 10 }] })} caps={caps} busy={false} onSubmit={submit} />);
  const summary = screen.getByText("重新转写音轨（可能再次计费）"); await user.click(summary); expect(summary.closest("details")).toHaveAttribute("open");
  const consent = screen.getByRole("checkbox"); await user.click(consent); await user.selectOptions(screen.getByLabelText("转写原始文件"), "two"); expect(consent).not.toBeChecked();
  expect(screen.getByRole("button", { name: "发送音轨并转写" })).toBeDisabled(); await user.click(consent); await user.click(screen.getByRole("button", { name: "发送音轨并转写" }));
  expect(submit).toHaveBeenCalledExactlyOnceWith("two"); expect(consent).not.toBeChecked();
});

it("[KBUI-KnowledgeOcr-1,KBUI-KnowledgeOcr-2,KBUI-KnowledgeOcr-4] OCR re-submit, disclosure and original selection isolate consent", async () => {
  const user = userEvent.setup(), submit = vi.fn().mockResolvedValue(undefined);
  render(<KnowledgeOcr item={item({ method: "doubao_ocr_reviewed", assets: [{ id: "one", filename: "one.png", size: 10 }, { id: "two", filename: "two.pdf", size: 10 }] })} caps={caps} busy={false} onSubmit={submit} />);
  await user.click(screen.getByText("重新识别原件（可能再次计费）")); const consent = screen.getByRole("checkbox", { name: "同意发送选中的整份原件到云端识别" });
  await user.click(consent); await user.selectOptions(screen.getByLabelText("识别原始文件"), "two"); expect(consent).not.toBeChecked();
  expect(consent).toHaveAttribute("aria-describedby", "kb-ocr-disclosure"); await user.click(consent); await user.click(screen.getByRole("button", { name: "发送文件并识别" }));
  expect(submit).toHaveBeenCalledExactlyOnceWith("two"); expect(consent).not.toBeChecked();
});

it("[KBUI-KnowledgeVideoOcr-1,KBUI-KnowledgeVideoOcr-2,KBUI-KnowledgeVideoOcr-4] video re-sampling source switch clears consent and submits only selected original", async () => {
  const user = userEvent.setup(), submit = vi.fn().mockResolvedValue(undefined);
  render(<KnowledgeVideoOcr item={item({ method: "video_ocr_reviewed", assets: [{ id: "one", filename: "one.mp4", size: 10 }, { id: "two", filename: "two.webm", size: 10 }] })} caps={caps} busy={false} onSubmit={submit} />);
  await user.click(screen.getByText("重新识别采样画面（可能再次计费）")); const consent = screen.getByRole("checkbox", { name: "同意发送采样图片并按画面 OCR 计费" });
  await user.click(consent); await user.selectOptions(screen.getByLabelText("采样视频原件"), "two"); expect(consent).not.toBeChecked();
  await user.click(consent); await user.click(screen.getByRole("button", { name: "采样画面并识别文字" })); expect(submit).toHaveBeenCalledExactlyOnceWith("two"); expect(consent).not.toBeChecked();
});

it("[KBUI-KnowledgeOrganization-4] organization evidence drawer displays the matching saved source revision", async () => {
  const user = userEvent.setup(); const result = { id: "org", source_revision: 4, state: "draft", status_note: "合成结果", provider: "synthetic", model: "synthetic", created_at: at, payload: { summary: { text: "合成摘要", citations: [1] }, points: [], tags: [], citations: [{ index: 1, locator: "测试段落 1", text: "真实已保存的合成原文证据" }] } } satisfies Organization;
  api.organization.mockResolvedValue({ result });
  render(<KnowledgeOrganization settings={settings} item={item()} caps={caps} busy={false} onGenerate={vi.fn()} onUseTags={vi.fn()} />);
  const summary = await screen.findByText("查看整理依据 · 正文版本 4"); expect(summary.closest("details")).not.toHaveAttribute("open"); await user.click(summary);
  expect(summary.closest("details")).toHaveAttribute("open"); expect(within(summary.closest("details")!).getByText(/真实已保存的合成原文证据/)).toBeVisible();
});

it("[KBUI-KnowledgeSemantic-3] index removal cancellation preserves data, confirmation sends remove only and leaves original text", async () => {
  const user = userEvent.setup(); const original = item(); const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  api.semanticInfo.mockResolvedValue({ state: "ready", source_revision: 4, chunk_count: 2 }); const change = vi.fn().mockResolvedValue({ state: "absent", source_revision: 4, chunk_count: 0 });
  render(<KnowledgeSemantic settings={settings} item={original} caps={caps} busy={false} onChange={change} />);
  const remove = await screen.findByRole("button", { name: "移除语义索引（保留原文）" }); await user.click(remove); expect(change).not.toHaveBeenCalled();
  confirm.mockReturnValue(true); await user.click(remove); await waitFor(() => expect(change).toHaveBeenCalledExactlyOnceWith(true));
  await waitFor(() => expect(screen.queryByRole("button", { name: "移除语义索引（保留原文）" })).toBeNull()); expect(original.content).toBe("合成已核对正文"); confirm.mockRestore();
});

it("[KBUI-KnowledgeMediaMerge-1,KBUI-KnowledgeMediaMerge-4,KBUI-KnowledgeMediaMerge-5] media preview drawers show selected saved pieces and block mixed original IDs", async () => {
  const user = userEvent.setup();
  api.mediaParts.mockResolvedValue({ items: [
    { id: "speech", asset_id: "a", revision: 2, method: "qwen_asr_draft", content: "合成原件甲转写", created_at: at },
    { id: "frames", asset_id: "a", revision: 3, method: "video_ocr_draft", content: "合成原件甲画面", created_at: at },
    { id: "wrong", asset_id: "b", revision: 4, method: "video_ocr_draft", content: "另一原件画面", created_at: at },
  ] }); const merge = vi.fn();
  render(<KnowledgeMediaMerge settings={settings} item={item()} busy={false} onMerge={merge} onConfirm={vi.fn()} />);
  await user.click(await screen.findByText("合并语音与采样画面")); await user.selectOptions(screen.getByLabelText("用于合并的语音转写"), "speech"); await user.selectOptions(screen.getByLabelText("用于合并的画面文字"), "frames");
  await user.click(screen.getByText("预览所选转写")); await user.click(screen.getByText("预览所选画面文字"));
  expect(screen.getByText("合成原件甲转写")).toBeVisible(); expect(screen.getByText("合成原件甲画面")).toBeVisible();
  await user.click(screen.getByRole("checkbox")); await user.selectOptions(screen.getByLabelText("用于合并的画面文字"), "wrong");
  expect(screen.getByRole("checkbox")).not.toBeChecked(); expect(screen.getByRole("alert")).toHaveTextContent("原件不一致"); expect(screen.getByRole("button", { name: "生成合并草稿" })).toBeDisabled(); expect(merge).not.toHaveBeenCalled();
});
