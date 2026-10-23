import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { defaultSettings } from "../data/demo";
import { ContentGenerationView } from "./ContentGenerationView";

describe("内容生成工作台", () => {
  it("默认进入内容创作，并保留只需主题的视频入口", async () => {
    const user = userEvent.setup();
    render(<ContentGenerationView settings={{ ...defaultSettings, backendAuthToken: "" }} />);
    expect(screen.getByRole("tab", { name: "内容创作" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("alert")).toHaveTextContent("请先登录");
    await user.click(screen.getByRole("tab", { name: "视频制作" }));
    expect(screen.getByRole("button", { name: "AI 规划方案" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "普通 AI 视频" })).toBeChecked();
    expect(screen.queryByText("尿酸高科普短视频")).not.toBeInTheDocument();
  });
});
