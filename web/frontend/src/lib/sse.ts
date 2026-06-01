// Browser EventSource only supports GET. For POST + SSE we use fetch()
// with a streaming body reader and parse the SSE wire format manually.
// The replay path also uses fetch (despite being GET) so the abort-signal
// plumbing matches.

import type { AgentEvent } from './types';

export async function* streamTurn(
  sessionId: string,
  userText: string,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(`/api/sessions/${sessionId}/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_text: userText }),
    signal,
  });
  yield* drainSseResponse(response, 'Turn request');
}

export async function* streamReplay(
  replayId: string,
  delaySeconds: number | undefined,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const qs = delaySeconds === undefined ? '' : `?delay=${delaySeconds}`;
  const response = await fetch(`/api/replays/${replayId}/stream${qs}`, {
    method: 'GET',
    signal,
  });
  yield* drainSseResponse(response, 'Replay request');
}

async function* drainSseResponse(
  response: Response,
  what: string,
): AsyncGenerator<AgentEvent> {
  if (!response.ok || !response.body) {
    throw new Error(`${what} failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE: events separated by `\n\n`.
      let sep = buffer.indexOf('\n\n');
      while (sep !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        sep = buffer.indexOf('\n\n');

        const parsed = parseSseEvent(rawEvent);
        if (parsed) yield parsed;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseEvent(raw: string): AgentEvent | null {
  let eventName = '';
  let dataJson = '';
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice('event:'.length).trim();
    else if (line.startsWith('data:')) dataJson += line.slice('data:'.length).trim();
  }
  if (!eventName) return null;
  try {
    return { type: eventName, data: JSON.parse(dataJson) } as AgentEvent;
  } catch {
    return null;
  }
}
