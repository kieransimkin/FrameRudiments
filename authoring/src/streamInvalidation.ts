/** One stable per-page edit identity; retries retain the same admitted snapshot. */
let client: string | undefined;
let sequence = 0;
export function nextDraftRevision(): {client_id: string; edit_sequence: number} {
  client ??= globalThis.crypto.randomUUID().replaceAll('-', '');
  if (sequence >= Number.MAX_SAFE_INTEGER) throw Error('Draft revision sequence exhausted');
  return {client_id: client, edit_sequence: ++sequence};
}

/** Content-free UI receipt. No source names, paths or timing text are displayed. */
export function invalidationSummary(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (row.schema !== 'framerudiments.preview-invalidation.v1' ||
      !Number.isSafeInteger(row.revision) || (row.revision as number) < 0 ||
      !Number.isSafeInteger(row.invalidated_frames) || (row.invalidated_frames as number) < 0 ||
      !Array.isArray(row.affected_sections) || row.affected_sections.length > 4096 ||
      !Array.isArray(row.retained_sections) || row.retained_sections.length > 2048) return null;
  if (row.cause === 'initial') return 'New section cache';
  if (row.cause === 'unchanged') return `${row.retained_sections.length} sections retained`;
  return `Revision ${row.revision}: ${row.affected_sections.length} section records invalidated · ` +
    `${row.invalidated_frames} frames cleared · ${row.retained_sections.length} sections retained`;
}

export function sameDraftImage<T>(image: {project: T; sidecars: unknown; refine: boolean} | null,
    project: T, sidecars: unknown, refine: boolean): boolean {
  return image !== null && image.project === project && image.sidecars === sidecars && image.refine === refine;
}

/** Half-open union of old/new section ranges; malformed receipts fail closed. */
export function invalidatesFrame(value: unknown, frame: number): boolean {
  if (!Number.isSafeInteger(frame) || frame < 0 || !value || typeof value !== 'object') return true;
  const row = value as Record<string, unknown>;
  if (row.schema !== 'framerudiments.preview-invalidation.v1' || !Array.isArray(row.affected_sections) ||
      row.affected_sections.length > 4096) return true;
  for (const section of row.affected_sections) {
    if (!section || typeof section !== 'object') return true;
    for (const range of [(section as any).old_range, (section as any).new_range]) {
      if (range === null) continue;
      if (!Array.isArray(range) || range.length !== 2 || !range.every(Number.isSafeInteger) ||
          range[0] < 0 || range[1] <= range[0]) return true;
      if (range[0] <= frame && frame < range[1]) return true;
    }
  }
  return false;
}
