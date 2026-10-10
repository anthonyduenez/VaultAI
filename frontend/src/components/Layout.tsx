import {
  BellRing,
  ClipboardCheck,
  Cpu,
  FileSpreadsheet,
  Files,
  LayoutDashboard,
  Lock,
  type LucideIcon,
  ScanLine,
  Settings,
  TriangleAlert,
  WifiOff,
} from "lucide-react";
import { Component, createContext, type ErrorInfo, type ReactNode, useContext } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { getSystemStatus } from "../api/client";
import { dataOf, type ResourceState, useResource } from "../api/hooks";
import type { SystemStatus } from "../api/types";
import type { Tone } from "../lib/display";
import { shortModelName } from "../lib/format";

const SystemStatusContext = createContext<ResourceState<SystemStatus>>({ kind: "loading" });

export function useSystemStatusState(): ResourceState<SystemStatus> {
  return useContext(SystemStatusContext);
}

export function useSystemStatus(): SystemStatus | null {
  return dataOf(useContext(SystemStatusContext));
}

function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <path d="M16 2 4 6.5v8.2c0 7.3 5.1 13.4 12 15.3 6.9-1.9 12-8 12-15.3V6.5L16 2Z" fill="currentColor" opacity="0.14" />
      <path d="M16 2 4 6.5v8.2c0 7.3 5.1 13.4 12 15.3 6.9-1.9 12-8 12-15.3V6.5L16 2Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx="16" cy="15" r="5.2" fill="none" stroke="var(--brand-accent)" strokeWidth="1.8" />
      <circle cx="16" cy="15" r="1.4" fill="var(--brand-accent)" />
      <path d="M16 8.6v1.8M16 19.6v1.8M9.6 15h1.8M20.6 15h1.8" stroke="var(--brand-accent)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

interface Chip {
  tone: Tone;
  icon: LucideIcon;
  text: string;
  title: string;
}

function privacyChip(state: ResourceState<SystemStatus>): Chip {
  if (state.kind === "loading") return { tone: "neutral", icon: Lock, text: "Checking local service…", title: "" };
  if (state.kind === "error") {
    return { tone: "neutral", icon: WifiOff, text: "Local service unreachable", title: "The VaultAI backend on 127.0.0.1:8765 is not responding." };
  }
  const { local_only, egress_guard } = state.data;
  if (local_only && egress_guard) {
    return { tone: "accent", icon: Lock, text: "Local-only · Network egress blocked", title: "Outbound connections to non-local addresses are blocked in-process." };
  }
  if (local_only) {
    return { tone: "warning", icon: TriangleAlert, text: "Local-only · Egress guard off", title: "Set VAULTAI_EGRESS_GUARD=true to block outbound connections." };
  }
  return { tone: "danger", icon: TriangleAlert, text: "Not local-only", title: "This configuration may reach non-local addresses." };
}

function modelChip(state: ResourceState<SystemStatus>): Chip | null {
  if (state.kind !== "ready") return null;
  const { model } = state.data;
  if (model.kind === "mock") return { tone: "neutral", icon: Cpu, text: "Mock adapter · rules only", title: model.detail };
  if (!model.reachable) return { tone: "warning", icon: TriangleAlert, text: "Local model unreachable", title: model.detail };
  return { tone: "accent", icon: Cpu, text: `${shortModelName(model.name)} · local endpoint · ready`, title: model.detail };
}

function StatusChip({ chip, prominent = false }: { chip: Chip; prominent?: boolean }) {
  const Icon = chip.icon;
  return (
    <span className="top-chip" data-tone={chip.tone} data-prominent={prominent || undefined} title={chip.title}>
      <Icon size={14} aria-hidden="true" />
      {chip.text}
    </span>
  );
}

const NAV: readonly { to: string; label: string; icon: LucideIcon; end?: boolean }[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/import", label: "Import & Scan", icon: ScanLine },
  { to: "/documents", label: "Documents", icon: Files },
  { to: "/review", label: "Review queue", icon: ClipboardCheck },
  { to: "/actions", label: "Action Center", icon: BellRing },
  { to: "/export", label: "Export", icon: FileSpreadsheet },
  { to: "/settings", label: "Settings & Privacy", icon: Settings },
];

export function Layout() {
  const status = useResource("system-status", getSystemStatus, { poll: () => 15_000 });
  const privacy = privacyChip(status.state);
  const model = modelChip(status.state);
  const location = useLocation();

  return (
    <SystemStatusContext.Provider value={status.state}>
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <div className="shell">
        <header className="topbar">
          <div className="brand">
            <BrandMark />
            <span className="brand-name">VaultAI</span>
            <span className="brand-tag">Private documents. Verified action.</span>
          </div>
          <div className="topbar-status" aria-live="polite">
            <StatusChip chip={privacy} prominent />
            {model && <StatusChip chip={model} />}
          </div>
        </header>
        <nav className="sidebar" aria-label="Main">
          <ul>
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <li key={to}>
                <NavLink to={to} end={end} className="nav-link">
                  <Icon size={17} aria-hidden="true" />
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
          <p className="sidebar-foot">Automate the routine. Surface the risky. Keep the human in control.</p>
        </nav>
        <main id="main" className="main" tabIndex={-1}>
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </SystemStatusContext.Provider>
  );
}

interface BoundaryProps {
  resetKey: string;
  children: ReactNode;
}

class ErrorBoundary extends Component<BoundaryProps, { failed: boolean; resetKey: string }> {
  override state = { failed: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: BoundaryProps, state: { failed: boolean; resetKey: string }) {
    return props.resetKey === state.resetKey ? null : { failed: false, resetKey: props.resetKey };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("View crashed", error, info.componentStack);
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="page">
        <div className="empty" role="alert">
          <TriangleAlert size={28} strokeWidth={1.5} aria-hidden="true" />
          <p className="empty-title">This view hit an unexpected error</p>
          <div className="empty-body">Your documents are safe on this device. Reload the page or open another section.</div>
          <button type="button" className="btn btn-sm" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
