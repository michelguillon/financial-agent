import { useEffect, useRef, useState } from 'react';
import { AssistantMessage } from './AssistantMessage';
import { ToolCallCard } from './ToolCallCard';
import type { ChatItem } from '../lib/types';

interface Props {
  items: ChatItem[];
  isStreaming: boolean;
  onSend: (text: string) => void;
  disabled?: boolean;
  disabledReason?: string;
}

const SAMPLE_PROMPTS = [
  'What did I spend on this year?',
  'What if my mortgage rate went from 2% to 4% on a £185k balance?',
  'Show me 3 unclassified transactions and suggest categories.',
];

export function Chat({ items, isStreaming, onSend, disabled, disabledReason }: Props) {
  const [draft, setDraft] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new items.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [items, isStreaming]);

  const send = () => {
    const trimmed = draft.trim();
    if (!trimmed || isStreaming || disabled) return;
    onSend(trimmed);
    setDraft('');
  };

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <div className="mx-auto max-w-3xl space-y-3">
          {items.length === 0 && <EmptyState onPick={onSend} />}
          {items.map((item) => (
            <ChatItemRow key={item.id} item={item} />
          ))}
          {isStreaming && <TypingIndicator />}
        </div>
      </div>

      <div className="border-t border-slate-200 bg-white px-4 py-3 sm:px-6">
        <div className="mx-auto max-w-3xl">
          {disabled && (
            <div className="mb-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {disabledReason}
            </div>
          )}
          <div className="flex gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={disabled ? 'Reset the session to keep going' : 'Ask about spending, scenarios, classifications…'}
              rows={1}
              disabled={isStreaming || disabled}
              className="flex-1 resize-none rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-emerald-400 focus:outline-none focus:ring-1 focus:ring-emerald-400 disabled:bg-slate-50 disabled:text-slate-400"
            />
            <button
              onClick={send}
              disabled={!draft.trim() || isStreaming || disabled}
              className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:bg-slate-300"
            >
              Send
            </button>
          </div>
          <div className="mt-1 text-[10px] text-slate-400">Enter to send · Shift+Enter for newline</div>
        </div>
      </div>
    </div>
  );

  function EmptyState({ onPick }: { onPick: (s: string) => void }) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-6 text-sm">
        <p className="mb-3 font-medium text-slate-900">Try one of these:</p>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {SAMPLE_PROMPTS.map((p) => (
            <button
              key={p}
              onClick={() => onPick(p)}
              className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs text-slate-700 transition hover:border-emerald-300 hover:bg-emerald-50"
            >
              {p}
            </button>
          ))}
        </div>
        <p className="mt-4 text-xs text-slate-500">
          Data is synthetic — 15 years of UK transactions generated to mirror a real personal-finance dataset. Your session is ephemeral and lives only in your browser tab.
        </p>
      </div>
    );
  }
}

function ChatItemRow({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className="flex justify-end">
          <div className="max-w-[85%] rounded-md bg-slate-900 px-4 py-2 text-sm text-white">
            {item.text}
          </div>
        </div>
      );
    case 'assistant':
      return <AssistantMessage text={item.text} />;
    case 'tool':
      return <ToolCallCard name={item.name} input={item.input} result={item.result} isError={item.is_error} />;
    case 'notice': {
      const style =
        item.level === 'error'
          ? 'border-red-200 bg-red-50 text-red-900'
          : item.level === 'budget'
            ? 'border-amber-200 bg-amber-50 text-amber-900'
            : 'border-slate-200 bg-slate-50 text-slate-700';
      return <div className={`rounded-md border px-3 py-2 text-xs ${style}`}>{item.text}</div>;
    }
  }
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1.5 text-xs text-slate-400">
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400"></span>
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400" style={{ animationDelay: '0.15s' }}></span>
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400" style={{ animationDelay: '0.3s' }}></span>
      <span>thinking…</span>
    </div>
  );
}
