/** Clock-free request/presentation fences. The shared timeline supplies frames. */
export class PresentationFence {
  sequence = 0;
  frame: number;
  constructor(frame: number) { this.frame = checkedFrame(frame); }
  request(frame: number): number {
    this.frame = checkedFrame(frame);
    if (this.sequence >= Number.MAX_SAFE_INTEGER) throw Error('Stream sequence exhausted');
    return ++this.sequence;
  }
  accepts(sequence: number, frame: number): boolean {
    return Number.isSafeInteger(sequence) && sequence === this.sequence && frame === this.frame;
  }
}
function checkedFrame(frame: number): number {
  if (!Number.isSafeInteger(frame) || frame < 0) throw Error('Invalid absolute preview frame');
  return frame;
}
export function previewFrame(time: number, fps: number, duration: number): number {
  if (![time,fps,duration].every(Number.isFinite) || time < 0 || fps <= 0 || duration <= 0)
    throw Error('Invalid preview clock');
  return Math.max(0,Math.min(Math.ceil(duration*fps)-1,Math.round(time*fps)));
}
export function validFrameUrl(value: unknown, token: string): value is string {
  if (typeof value !== 'string' || !value.startsWith('/api/stream/frame?')) return false;
  const url = new URL(value,'http://localhost');
  return url.origin === 'http://localhost' && url.pathname === '/api/stream/frame' &&
    url.searchParams.get('session') === token;
}
