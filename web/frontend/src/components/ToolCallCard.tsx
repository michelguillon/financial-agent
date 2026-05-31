import { useState } from 'react';

interface Props {
  name: string;
  input: Record<string, unknown>;
  result?: string;
  isError?: boolean;
}

export function ToolCallCard({ name, input, result, isError }: Props) {
  const [expanded, setExpanded] = useState(false);
  const inputSummary = summariseInput(input);

  return (
    <div className={`rounded-md border text-sm ${isError ? 'border-red-200 bg-red-50' : 'border-slate-200 bg-slate-50'}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2 truncate">
          <span className={`font-mono text-xs ${isError ? 'text-red-700' : 'text-slate-500'}`}>
            {isError ? '⚠' : '›'}
          </span>
          <span className="font-mono text-xs font-medium">{name}</span>
          <span className="truncate text-xs text-slate-500">({inputSummary})</span>
        </span>
        <span className="shrink-0 text-xs text-slate-400">
          {result === undefined ? '...' : expanded ? '−' : '+'}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-slate-200 px-3 py-2 text-xs">
          <pre className="whitespace-pre-wrap break-words font-mono text-slate-700">
{JSON.stringify(input, null, 2)}
          </pre>
          {result !== undefined && (
            <>
              <div className="mt-2 mb-1 text-[10px] uppercase tracking-wider text-slate-400">Result</div>
              <pre className="whitespace-pre-wrap break-words font-mono text-slate-700">
{prettifyResult(result)}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function summariseInput(input: Record<string, unknown>): string {
  const entries = Object.entries(input);
  if (entries.length === 0) return '';
  return entries
    .map(([k, v]) => `${k}=${shortValue(v)}`)
    .slice(0, 3)
    .join(', ');
}

function shortValue(v: unknown): string {
  if (typeof v === 'string') return v.length > 24 ? JSON.stringify(v.slice(0, 21) + '...') : JSON.stringify(v);
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (v === null) return 'null';
  return '...';
}

function prettifyResult(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
