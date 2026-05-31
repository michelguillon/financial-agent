interface Props {
  used: number;
  total: number;
  turnsSoFar: number;
  onReset: () => void;
}

export function BudgetBar({ used, total, turnsSoFar, onReset }: Props) {
  const pct = Math.min(100, (used / total) * 100);
  const over = used >= total * 0.85;

  return (
    <div className="border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center justify-between text-xs text-slate-600">
            <span>
              Demo budget · ${used.toFixed(4)} / ${total.toFixed(2)} · {turnsSoFar} turn{turnsSoFar === 1 ? '' : 's'}
            </span>
            <span className="text-slate-400 hidden sm:inline">synthetic data — ephemeral session</span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className={`h-full transition-all duration-300 ${over ? 'bg-amber-500' : 'bg-emerald-500'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <button
          onClick={onReset}
          className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
        >
          Reset
        </button>
      </div>
    </div>
  );
}
