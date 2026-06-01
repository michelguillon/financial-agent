import type { Mode } from '../lib/types';

interface Props {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  // Lock the toggle while a stream is in flight so we don't race aborts.
  disabled?: boolean;
}

export function Header({ mode, onModeChange, disabled }: Props) {
  return (
    <header className="border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-slate-900">Personal finance agent</h1>
          <p className="text-xs text-slate-500">
            Sonnet 4.6 + Haiku 4.5 · synthetic UK data ·{' '}
            <a
              href="https://github.com/michelguillon/financial-agent"
              target="_blank"
              rel="noreferrer"
              className="underline hover:text-slate-700"
            >
              source
            </a>
          </p>
        </div>

        <div className="flex shrink-0 rounded-md border border-slate-300 bg-slate-50 p-0.5 text-xs">
          <ModeButton
            label="Live"
            active={mode === 'live'}
            onClick={() => onModeChange('live')}
            disabled={disabled}
          />
          <ModeButton
            label="Replay"
            active={mode === 'replay'}
            onClick={() => onModeChange('replay')}
            disabled={disabled}
          />
        </div>
      </div>
    </header>
  );
}

function ModeButton({
  label,
  active,
  onClick,
  disabled,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  const base = 'rounded-[0.3rem] px-3 py-1 font-medium transition';
  const tone = active
    ? 'bg-white text-slate-900 shadow-sm'
    : 'text-slate-500 hover:text-slate-700';
  const isDisabled = disabled || active;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isDisabled}
      aria-pressed={active}
      className={`${base} ${tone} disabled:cursor-default`}
    >
      {label}
    </button>
  );
}
