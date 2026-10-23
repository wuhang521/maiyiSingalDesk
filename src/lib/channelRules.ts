import type { ChannelRuleDraft } from "../types";

const STOP_WORDS = new Set([
  "每天",
  "每日",
  "关注",
  "推送",
  "相关",
  "内容",
  "消息",
  "新闻",
  "信息",
  "给我",
  "想要",
  "国内",
  "国外",
  "全球",
]);

const EXCLUDE_MARKERS = ["排除", "不要", "不包括", "过滤"];

export function deriveChannelRules(prompt: string): ChannelRuleDraft {
  const normalized = prompt.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return {
      name: "新的智能频道",
      description: "等待补充关注范围",
      includeTerms: [],
      excludeTerms: [],
    };
  }

  const clauses = normalized.split(/[。；;\n]/).map((part) => part.trim()).filter(Boolean);
  const excludeClauses = clauses.filter((clause) =>
    EXCLUDE_MARKERS.some((marker) => clause.includes(marker)),
  );
  const includeClauses = clauses.filter((clause) => !excludeClauses.includes(clause));

  const includeTerms = extractTerms(includeClauses.join("，"));
  const excludeTerms = extractTerms(
    excludeClauses
      .join("，")
      .replace(new RegExp(EXCLUDE_MARKERS.join("|"), "g"), ""),
  );
  const subject = includeTerms.slice(0, 3).join(" · ") || "自定义情报";

  return {
    name: subject.length > 24 ? `${subject.slice(0, 22)}…` : subject,
    description: `持续追踪 ${includeTerms.slice(0, 5).join("、") || "指定主题"}`,
    includeTerms,
    excludeTerms,
  };
}

function extractTerms(value: string): string[] {
  const chunks = value
    .split(/[，,、：:\s/]+/)
    .map((term) => term.trim())
    .filter((term) => term.length >= 2 && term.length <= 24 && !STOP_WORDS.has(term));
  return Array.from(new Set(chunks)).slice(0, 12);
}
