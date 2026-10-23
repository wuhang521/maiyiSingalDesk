import { describe, expect, it } from "vitest";
import type { TrashEntry } from "../types";
import { purgeExpiredTrash, TRASH_RETENTION_MS, trashDaysRemaining } from "./trash";

describe("article trash", () => {
  const now = new Date("2026-08-11T12:00:00+08:00").getTime();

  it("keeps articles for less than seven days", () => {
    const entries: TrashEntry[] = [{ id: "kept", deletedAt: now - TRASH_RETENTION_MS + 1 }];
    expect(purgeExpiredTrash(entries, now)).toEqual(entries);
  });

  it("purges articles after seven days", () => {
    const entries: TrashEntry[] = [{ id: "expired", deletedAt: now - TRASH_RETENTION_MS }];
    expect(purgeExpiredTrash(entries, now)).toEqual([
      { id: "expired", deletedAt: now - TRASH_RETENTION_MS, purged: true },
    ]);
  });

  it("reports the remaining whole days", () => {
    expect(trashDaysRemaining(now, now)).toBe(7);
    expect(trashDaysRemaining(now - 6.5 * 24 * 60 * 60 * 1000, now)).toBe(1);
  });
});
