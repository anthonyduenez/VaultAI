import { type KeyboardEvent, type ReactNode, useId } from "react";

export interface TabSpec<K extends string> {
  id: K;
  label: ReactNode;
  count?: number;
  tone?: "danger" | "warning";
}

interface TabsProps<K extends string> {
  tabs: readonly TabSpec<K>[];
  active: K;
  onChange: (id: K) => void;
  label: string;
}

export function Tabs<K extends string>({ tabs, active, onChange, label }: TabsProps<K>) {
  const baseId = useId();
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex((t) => t.id === active);
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0 || index < 0) return;
    event.preventDefault();
    const next = tabs[(index + step + tabs.length) % tabs.length];
    if (next) {
      onChange(next.id);
      document.getElementById(`${baseId}-tab-${next.id}`)?.focus();
    }
  };
  return (
    <div className="tabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          id={`${baseId}-tab-${tab.id}`}
          type="button"
          role="tab"
          className="tab"
          aria-selected={tab.id === active}
          tabIndex={tab.id === active ? 0 : -1}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="tab-count" data-tone={tab.tone}>
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
