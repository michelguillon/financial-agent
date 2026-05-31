import { useCallback, useEffect, useRef, useState } from 'react';
import { BudgetBar } from './components/BudgetBar';
import { Chat } from './components/Chat';
import { streamTurn } from './lib/sse';
import type { AgentEvent, ChatItem, SessionInfo } from './lib/types';

export default function App() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [budgetExceeded, setBudgetExceeded] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const nextId = useRef(0);
  const startOnceRef = useRef(false);

  const newId = () => `item-${nextId.current++}`;

  // Bootstrap a session on first mount (StrictMode-safe via ref guard).
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
    if (!session || isStreaming || budgetExceeded) return;
    setItems((cur) => [...cur, { kind: 'user', text: userText, id: newId() }]);
    setIsStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    // Pending tool calls keyed by name (so we can fill in their result when it arrives).
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
  }, [session, isStreaming, budgetExceeded]);

  function applyEvent(event: AgentEvent, pending: ChatItem[]) {
    switch (event.type) {
      case 'session.info':
        setSession((cur) => cur && {
          ...cur,
          budget_used_usd: event.data.budget_used_usd,
          turns_so_far: event.data.turns_so_far,
        });
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
        // Pair with the most recent unresolved tool_call with the same name.
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
        // Per-iteration usage; the cumulative number arrives in turn.completed.
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
      <header className="border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
        <div className="mx-auto flex max-w-3xl items-center justify-between">
          <div>
            <h1 className="text-base font-semibold text-slate-900">Personal finance agent</h1>
            <p className="text-xs text-slate-500">Sonnet 4.6 + Haiku 4.5 · synthetic UK data · <a href="https://github.com/michelguillon/financial-agent" target="_blank" rel="noreferrer" className="underline hover:text-slate-700">source</a></p>
          </div>
        </div>
      </header>

      {session && (
        <BudgetBar
          used={session.budget_used_usd}
          total={session.budget_total_usd}
          turnsSoFar={session.turns_so_far}
          onReset={resetSession}
        />
      )}

      <main className="flex-1 overflow-hidden">
        {error ? (
          <div className="flex h-full items-center justify-center p-6">
            <div className="max-w-md rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-900">
              {error}
              <button onClick={startSession} className="mt-3 block rounded-md border border-red-300 bg-white px-3 py-1.5 text-xs font-medium hover:bg-red-100">
                Try again
              </button>
            </div>
          </div>
        ) : !session ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">Starting session…</div>
        ) : (
          <Chat
            items={items}
            isStreaming={isStreaming}
            onSend={sendTurn}
            disabled={budgetExceeded}
            disabledReason={budgetExceeded ? 'Budget reached. Reset to start a fresh demo session.' : undefined}
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
