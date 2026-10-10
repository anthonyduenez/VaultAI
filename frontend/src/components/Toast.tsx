import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { Tone } from "../lib/display";

type ToastTone = Extract<Tone, "accent" | "danger" | "info">;

interface Toast {
  id: number;
  tone: ToastTone;
  text: string;
}

interface ToastApi {
  push: (tone: ToastTone, text: string) => void;
}

const ToastContext = createContext<ToastApi>({ push: () => undefined });

const ICONS = { accent: CircleCheck, danger: CircleAlert, info: Info } satisfies Record<ToastTone, unknown>;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);

  const push = useCallback(
    (tone: ToastTone, text: string) => {
      const id = nextId.current++;
      setToasts((all) => [...all.slice(-3), { id, tone, text }]);
      window.setTimeout(() => dismiss(id), tone === "danger" ? 8000 : 4500);
    },
    [dismiss],
  );

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite" aria-relevant="additions">
        {toasts.map((toast) => {
          const Icon = ICONS[toast.tone];
          return (
            <div key={toast.id} className="toast" data-tone={toast.tone} role={toast.tone === "danger" ? "alert" : "status"}>
              <Icon size={16} aria-hidden="true" />
              <span>{toast.text}</span>
              <button type="button" className="btn-icon" aria-label="Dismiss notification" onClick={() => dismiss(toast.id)}>
                <X size={14} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
