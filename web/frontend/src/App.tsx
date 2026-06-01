import { useCallback, useEffect, useRef, useState } from 'react';
import { BudgetBar } from './components/BudgetBar';
import { Chat } from './components/Chat';
import { Header } from './components/Header';
import { streamReplay, streamTurn } from './lib/sse';
import type { AgentEvent, ChatItem, Mode, ReplayMeta, SessionInfo } from './lib/types';

export default function App() {
  const [mode, setMode] = useState<Mode>('live');
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [replayMeta, setReplayMeta] = useState<ReplayMeta | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [budgetExceeded, setBudgetExceeded] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const nextId = useRef(0);
  const startOnceRef = useRef(false);

  const newId = () => `item-${nextId.current++}`;

  // Bootstrap a live session on first mount (StrictMode-safe via ref guard).
  useEffect(() => {
    if (startOnceRef.current) return;
    startOnceRef.current = true;
    void startSession();
  }, []);

  const startSession = useCallback(async () => {
    setError(null);
    setItems([]);
    setBudgetExceeded(false);
    try {
      const r = await fetch('/api/sessions', { method: 'POST' });
      if (r.status === 429) {
        const retry = r.headers.get('Retry-After') ?? '?';
        setError(`Rate limit reached — try again in ${formatRetry(retry)}.`);
        return;
      }
      if (!r.ok) {
        setError(`Couldn't start session (${r.status}).`);
        return;
      }
      const info: SessionInfo = await r.json();
      setSession(info);
    } catch (e) {
      setError(`Network error: ${(e as Error).message}`);
    }
  }, []);

  const resetSession = useCallback(async () => {
    if (session) {
      void fetch(`/api/sessions/${session.session_id}`, { method: 'DELETE' });
    }
    abortRef.current?.abort();
    setSession(null);
    await startSession();
  }, [session, startSession]);

  const sendTurn = useCallback(async (userText: string) => {
    if (!session || isStreaming || budgetExceeded || mode !== 'live') return;
    setItems((cur) => [...cur, { kind: 'user', text: userText, id: newId() }]);
    setIsStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    const pendingTools: ChatItem[] = [];

    try {
      for await (const event of streamTurn(session.session_id, userText, controller.signal)) {
        applyEvent(event, pendingTools);
      }
    } catch (e) {
      setItems((cur) => [...cur, { kind: 'notice', level: 'error', text: `Stream error: ${(e as Error).message}`, id: newId() }]);
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [session, isStreaming, budgetExceeded, mode]);

  const startReplay = useCallback(async () => {
    setError(null);
    setItems([]);

    // Pick the first available replay. Today there's only one, but the
    // endpoint is shaped to support more.
    let replays: ReplayMeta[] = [];
    try {
      const r = await fetch('/api/replays');
      if (!r.ok) {
        setError(`Couldn't load replay catalogue (${r.status}).`);
        return;
      }
      replays = (await r.json()).replays;
    } catch (e) {
      setError(`Network error loading catalogue: ${(e as Error).message}`);
      return;
    }
    if (!replays.length) {
      setError('No replays available.');
      return;
    }
    const target = replays[0];
    setReplayMeta(target);

    setIsStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const pendingTools: ChatItem[] = [];
    try {
      for await (const event of streamReplay(target.id, undefined, controller.signal)) {
        applyEvent(event, pendingTools);
      }
    } catch (e) {
      // AbortError is expected when the user toggles back to Live mid-stream.
      const name = (e as Error).name;
      if (name !== 'AbortError') {
        setItems((cur) => [...cur, { kind: 'notice', level: 'error', text: `Replay error: ${(e as Error).message}`, id: newId() }]);
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, []);

  const switchMode = useCallback(async (next: Mode) => {
    if (next === mode || isStreaming) return;
    // Abort whatever is currently streaming.
    abortRef.current?.abort();
    setMode(next);
    setItems([]);
    setReplayMeta(null);

    if (next === 'replay') {
      void startReplay();
    } else {
      // Going back to Live — make sure a session exists.
      if (!session) await startSession();
    }
  }, [mode, isStreaming, session, startReplay, startSession]);

  function applyEvent(event: AgentEvent, pending: ChatItem[]) {
    switch (event.type) {
      case 'session.info':
        setSession((cur) => cur && {
          ...cur,
          budget_used_usd: event.data.budget_used_usd,
          turns_so_far: event.data.turns_so_far,
        });
        return;

      case 'replay.info':
        setReplayMeta({
          id: event.data.replay_id,
          title: event.data.title,
          summary: event.data.summary,
        });
        setItems((cur) => [...cur, {
          kind: 'notice',
          level: 'info',
          text: `Canned demo: ${event.data.title}`,
          id: newId(),
        }]);
        return;

      case 'user_text':
        setItems((cur) => [...cur, { kind: 'user', text: event.data.text, id: newId() }]);
        return;

      case 'tool_call': {
        const toolItem: ChatItem = {
          kind: 'tool',
          name: event.data.name,
          input: event.data.input,
          id: newId(),
        };
        pending.push(toolItem);
        setItems((cur) => [...cur, toolItem]);
        return;
      }

      case 'tool_result': {
        const idx = [...pending].reverse().findIndex(
          (t) => t.kind === 'tool' && t.name === event.data.name && t.result === undefined,
        );
        if (idx === -1) return;
        const realIdx = pending.length - 1 - idx;
        const target = pending[realIdx] as Extract<ChatItem, { kind: 'tool' }>;
        target.result = event.data.result;
        target.is_error = event.data.is_error;
        setItems((cur) => cur.map((it) => (it.id === target.id ? { ...target } : it)));
        return;
      }

      case 'assistant_text':
        setItems((cur) => [...cur, { kind: 'assistant', text: event.data.text, id: newId() }]);
        return;

      case 'usage':
        return;

      case 'error':
        setItems((cur) => [...cur, { kind: 'notice', level: 'error', text: `${event.data.where}: ${event.data.detail}`, id: newId() }]);
        return;

      case 'turn.completed':
        setSession((cur) => cur && {
          ...cur,
          budget_used_usd: event.data.cumulative_cost_usd,
          turns_so_far: event.data.turns_so_far,
        });
        return;

      case 'replay.completed':
        // Stream-end marker; the for-await loop exits naturally and
        // setIsStreaming(false) runs in the `finally`.
        return;

      case 'budget.exceeded':
        setBudgetExceeded(true);
        setItems((cur) => [...cur, {
          kind: 'notice',
          level: 'budget',
          text: `Demo budget reached ($${event.data.used_usd.toFixed(4)} / $${event.data.budget_usd.toFixed(2)}). Reset to start a fresh session.`,
          id: newId(),
        }]);
        return;
    }
  }

  return (
    <div className="flex h-full flex-col">
      <Header
        mode={mode}
        onModeChange={switchMode}
        disabled={isStreaming}
      />

      {mode === 'live' && session && (
        <BudgetBar
          used={session.budget_used_usd}
          total={session.budget_total_usd}
          turnsSoFar={session.turns_so_far}
          onReset={resetSession}
        />
      )}

      {mode === 'replay' && replayMeta && (
        <div className="border-b border-slate-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 sm:px-6">
          <div className="mx-auto max-w-3xl">
            <span className="font-semibold">Replay mode</span> · {replayMeta.title} · {replayMeta.summary}
          </div>
        </div>
      )}

      <main className="flex-1 overflow-hidden">
        {error ? (
          <div className="flex h-full items-center justify-center p-6">
            <div className="max-w-md rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-900">
              {error}
              <button
                onClick={mode === 'live' ? startSession : startReplay}
                className="mt-3 block rounded-md border border-red-300 bg-white px-3 py-1.5 text-xs font-medium hover:bg-red-100"
              >
                Try again
              </button>
            </div>
          </div>
        ) : mode === 'live' && !session ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">Starting session…</div>
        ) : (
          <Chat
            items={items}
            isStreaming={isStreaming}
            onSend={sendTurn}
            disabled={mode === 'replay' || budgetExceeded}
            disabledReason={
              mode === 'replay'
                ? 'Watching a canned demo — switch to Live to chat.'
                : budgetExceeded
                  ? 'Budget reached. Reset to start a fresh demo session.'
                  : undefined
            }
            hideSamples={mode === 'replay'}
          />
        )}
      </main>
    </div>
  );
}

function formatRetry(retryAfter: string): string {
  const seconds = parseInt(retryAfter, 10);
  if (!isFinite(seconds)) return retryAfter;
  if (seconds < 60) return `${seconds}s`;
  const hours = Math.round(seconds / 3600);
  return hours <= 1 ? `${Math.round(seconds / 60)} minutes` : `${hours} hours`;
}
