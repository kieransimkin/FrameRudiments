/** Content-free replay diagnostics. The timeline remains the only playback clock. */
export type StatefulMode = 'verified_prefix_replay' | 'bounded_history' | 'independent';
export type StatefulPhase = 'idle' | 'replaying' | 'verifying' | 'ready' | 'unavailable' | 'cancelled';
export type StatefulStatus = {
  enabled: boolean; mode: StatefulMode; phase: StatefulPhase; requested_frame: number | null;
  verified_prefix_frames: number; live_next_frame: number; current_request_frames: number;
  replayed_frames: number; max_prefix_frames: number | null; max_request_frames: number | null;
  last_reason: string | null;
};

const modes = new Set(['verified_prefix_replay','bounded_history','independent']);
const phases = new Set(['idle','replaying','verifying','ready','unavailable','cancelled']);
const reasons: Record<string,string> = {
  prefix_limit_exceeded: 'This position exceeds the preview history limit. Seek earlier.',
  replay_budget_exceeded: 'Reconstructing this position exceeds the frame budget. Seek earlier.',
  pcm_budget_exceeded: 'The full-programme audio cache exceeds the preview budget.',
  pcm_disk_budget_exceeded: 'There is insufficient working disk space for the feedback audio cache.',
  runtime_manifest_limit: 'The feedback runtime exceeds the preview verification limits.',
  replay_mismatch: 'The engine produced different pixels after reset. Reopen the preview to retry.',
  runtime_identity_changed: 'The rendering runtime changed. Reopen the preview.',
  input_identity_changed: 'A source input changed. Reopen the preview.',
  native_runtime_unavailable: 'The feedback runtime is unavailable.',
  native_render_failed: 'The feedback renderer could not complete this position.',
  native_cleanup_failed: 'The previous feedback renderer could not close. A clean restart is required.',
  invalid_replay_result: 'The feedback renderer returned an invalid frame. Reopen the preview.',
  nonsequential_replay: 'Feedback history could not be reconstructed in order. Reopen the preview.',
  profile_unsupported: 'This feedback pipeline requires authored Q5.',
  renderer_closed: 'The feedback renderer is closed. Reopen the preview.',
  worker_timeout: 'Reconstructing this position exceeded the time limit. Seek earlier.',
  worker_failed: 'The feedback worker could not complete this position.',
};
const bounded = (value: unknown, maximum = 20_000_000): value is number =>
  Number.isSafeInteger(value) && (value as number) >= 0 && (value as number) <= maximum;
const reasonCode = (value: unknown): string | null => value == null ? null :
  typeof value === 'string' && Object.hasOwn(reasons,value) ? value : 'unknown';

/** Copy only bounded counters/enums. Arbitrary backend messages and paths are never displayed. */
export function parseStateful(value: unknown): StatefulStatus | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const row = value as Record<string,unknown>;
  if (typeof row.enabled !== 'boolean' || typeof row.mode !== 'string' || !modes.has(row.mode) ||
      typeof row.phase !== 'string' || !phases.has(row.phase)) return null;
  if (row.requested_frame != null && !bounded(row.requested_frame,9_999_999)) return null;
  const counters = ['verified_prefix_frames','live_next_frame','current_request_frames','replayed_frames'] as const;
  for (const name of counters) if (row[name] != null && !bounded(row[name])) return null;
  for (const name of ['max_prefix_frames','max_request_frames'] as const)
    if (row[name] != null && (!bounded(row[name]) || row[name] === 0)) return null;
  const result: StatefulStatus = {
    enabled: row.enabled, mode: row.mode as StatefulMode, phase: row.phase as StatefulPhase,
    requested_frame: row.requested_frame as number | null | undefined ?? null,
    verified_prefix_frames: row.verified_prefix_frames as number | undefined ?? 0,
    live_next_frame: row.live_next_frame as number | undefined ?? 0,
    current_request_frames: row.current_request_frames as number | undefined ?? 0,
    replayed_frames: row.replayed_frames as number | undefined ?? 0,
    max_prefix_frames: row.max_prefix_frames as number | null | undefined ?? null,
    max_request_frames: row.max_request_frames as number | null | undefined ?? null,
    last_reason: reasonCode(row.last_reason),
  };
  if (result.max_prefix_frames !== null && (result.verified_prefix_frames > result.max_prefix_frames ||
      result.live_next_frame > result.max_prefix_frames)) return null;
  if (result.max_request_frames !== null && result.current_request_frames > result.max_request_frames) return null;
  return result;
}

export function statefulSummary(value: StatefulStatus | null): string | null {
  if (!value || !value.enabled || value.mode === 'independent') return null;
  if (value.phase === 'unavailable')
    return 'Stateful seek unavailable. ' + (reasons[value.last_reason ?? ''] ?? 'Reopen the preview or seek earlier.');
  if (value.mode === 'bounded_history') return 'Temporal history is reconstructed on the authored frame clock.';
  const target = value.requested_frame;
  const suffix = target === null ? '' : ` for frame ${target}`;
  if (value.phase === 'idle') return `Preparing feedback history${suffix}.`;
  if (value.phase === 'cancelled') return 'Feedback replay cancelled; following the latest request.';
  if (value.phase === 'ready') return `Feedback history verified${suffix} · ${value.verified_prefix_frames} verified frames`;
  const label = value.phase === 'verifying' ? 'Verifying an independent reset' : 'Replaying feedback from frame zero';
  const position = target === null ? `${value.live_next_frame} frames` :
    `${Math.min(value.live_next_frame,target+1)} / ${target+1} frames`;
  const budget = value.max_request_frames === null ? '' : ` / ${value.max_request_frames}`;
  return `${label}${suffix} · ${position} · ${value.current_request_frames}${budget} work frames`;
}

/** Only diagnostics for the exact current request may change presentation. */
export function currentStatefulEvent(value: unknown,
    fence: {accepts(sequence: number, frame: number): boolean}): StatefulStatus | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string,unknown>;
  if (!Number.isSafeInteger(row.cursor_seq) || !bounded(row.frame,9_999_999) ||
      !fence.accepts(row.cursor_seq as number,row.frame)) return null;
  if (row.type !== 'stateful-progress' && row.type !== 'stateful-seek-unavailable') return null;
  const parsed = parseStateful(row.stateful);
  if (!parsed || (parsed.requested_frame !== null && parsed.requested_frame !== row.frame)) return null;
  return row.type === 'stateful-seek-unavailable' ? {
    ...parsed, phase:'unavailable', requested_frame:row.frame,
    last_reason:reasonCode(row.reason ?? parsed.last_reason),
  } : parsed;
}
