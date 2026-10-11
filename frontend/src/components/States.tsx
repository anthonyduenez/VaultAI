import { Inbox, LoaderCircle, type LucideIcon, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import type { Resource } from "../api/hooks";
import type { ApiFailure } from "../api/client";

export function Spinner({ label, size = 16 }: { label?: string; size?: number }) {
  return (
    <span className="spinner-wrap" role={label ? "status" : undefined}>
      <LoaderCircle className="spinner" size={size} aria-hidden="true" />
      {label && <span>{label}</span>}
    </span>
  );
}

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ icon: Icon = Inbox, title, children, action }: EmptyStateProps) {
  return (
    <div className="empty">
      <Icon size={28} strokeWidth={1.5} aria-hidden="true" />
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiFailure; onRetry?: () => void }) {
  const offline = error.code === "network";
  return (
    <div className="empty" role="alert">
      <WifiOff size={28} strokeWidth={1.5} aria-hidden="true" />
      <p className="empty-title">{offline ? "The local VaultAI service is not responding" : "This view could not be loaded"}</p>
      <div className="empty-body">
        {offline
          ? "Start the backend on this machine (it listens on 127.0.0.1:8765). Nothing is retried over the internet."
          : error.message}
      </div>
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          <RefreshCw size={14} aria-hidden="true" /> Try again
        </button>
      )}
    </div>
  );
}

interface LoadedProps<T> {
  resource: Resource<T>;
  children: (data: T) => ReactNode;
  loadingLabel?: string;
}

export function Loaded<T>({ resource, children, loadingLabel = "Loading…" }: LoadedProps<T>) {
  const { state, reload } = resource;
  if (state.kind === "loading") {
    return (
      <div className="loading-block">
        <Spinner label={loadingLabel} />
      </div>
    );
  }
  if (state.kind === "ready") return <>{children(state.data)}</>;
  if (state.data === null) return <ErrorState error={state.error} onRetry={reload} />;
  return (
    <>
      <p className="stale-note" role="status">
        Showing the last loaded data. Refresh failed: {state.error.message}
      </p>
      {children(state.data)}
    </>
  );
}
