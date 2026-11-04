import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { KnowledgeCapabilities, KnowledgeDetail } from "../lib/knowledgeApi";
import { KnowledgeAsr } from "./KnowledgeAsr";

const item = (names: string[]) => ({ status: "needs_input", method: "media", assets: names.map((filename, n) => ({ id: String(n), filename })) } as KnowledgeDetail);
const caps = (silk: boolean) => ({ cloud_asr_enabled: true, silk_available: silk, cloud_asr_provider: "千问 / 阿里云百炼", cloud_asr_max_minutes: 10 } as KnowledgeCapabilities);

describe("native voice ASR", () => {
  it.each(["voice.silk", "voice.slk", "voice.aud", "voice.amr"])("requires explicit consent for %s", async filename => {
    const user=userEvent.setup();const submit=vi.fn().mockResolvedValue(undefined);
    render(<KnowledgeAsr item={item([filename])} caps={caps(true)} busy={false} onSubmit={submit} />);
    expect(screen.getByLabelText("转写原始文件")).toHaveTextContent(filename);
    const button=screen.getByRole("button",{name:"发送音轨并转写"});expect(button).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));await user.click(button);
    expect(submit).toHaveBeenCalledWith("0");expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByText(/本地提取所选文件的音轨/)).toHaveTextContent("千问");
  });
  it("blocks native voice without a decoder and resets consent when choosing another file", async () => {
    const user=userEvent.setup();const submit=vi.fn();
    render(<KnowledgeAsr item={item(["voice.aud","audio.wav"])} caps={caps(false)} busy={false} onSubmit={submit} />);
    await user.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button",{name:"发送音轨并转写"})).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("转写原始文件"),"1");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    await user.click(screen.getByRole("checkbox"));await user.click(screen.getByRole("button",{name:"发送音轨并转写"}));
    expect(submit).toHaveBeenCalledWith("1");
  });
});
