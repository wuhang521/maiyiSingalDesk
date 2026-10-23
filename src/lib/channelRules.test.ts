import { describe, expect, it } from "vitest";
import { deriveChannelRules } from "./channelRules";

describe("deriveChannelRules", () => {
  it("extracts positive and negative terms from a natural-language prompt", () => {
    const result = deriveChannelRules(
      "关注全球健康管理公司、融资并购和保险支付。排除个人养生、食谱和普通疾病科普。",
    );

    expect(result.includeTerms.join(" ")).toContain("健康管理公司");
    expect(result.includeTerms.join(" ")).toContain("融资并购");
    expect(result.excludeTerms.join(" ")).toContain("个人养生");
    expect(result.name).not.toBe("新的智能频道");
  });

  it("returns a stable empty draft", () => {
    expect(deriveChannelRules("   ")).toEqual({
      name: "新的智能频道",
      description: "等待补充关注范围",
      includeTerms: [],
      excludeTerms: [],
    });
  });
});
