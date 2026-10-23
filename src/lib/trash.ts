import type { TrashEntry } from "../types";

const DAY_MS = 24 * 60 * 60 * 1000;
export const TRASH_RETENTION_MS = 7 * DAY_MS;

export function purgeExpiredTrash(entries: TrashEntry[], now = Date.now()): TrashEntry[] {
  return entries.map((entry) => {
    if (entry.purged || now - entry.deletedAt >= TRASH_RETENTION_MS) {
      return { id: entry.id, deletedAt: entry.deletedAt, purged: true };
    }
    return entry;
  });
}

export function trashDaysRemaining(deletedAt: number, now = Date.now()): number {
  return Math.max(1, Math.ceil((TRASH_RETENTION_MS - (now - deletedAt)) / DAY_MS));
}
