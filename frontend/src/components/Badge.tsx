import { Cpu, type LucideIcon, PenLine, ShieldAlert, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { DOC_TYPE_LABELS, type DocStatus, type DocType, type FieldOrigin, type Severity } from "../api/types";
import { DOC_TYPE_ICON, FIELD_ORIGIN, SEVERITY, STATUS, type Tone, confidenceTone } from "../lib/display";
import { formatPercent } from "../lib/format";

interface BadgeProps {
  tone?: Tone;
  icon?: LucideIcon;
  title?: string;
  children: ReactNode;
}

export function Badge({ tone = "neutral", icon: Icon, title, children }: BadgeProps) {
  return (
    <span className="badge" data-tone={tone} title={title}>
      {Icon && <Icon size={12} strokeWidth={2.25} aria-hidden="true" />}
      {children}
    </span>
  );
}

export function StatusBadge({ status, deferred = false }: { status: DocStatus; deferred?: boolean }) {
  const { label, tone, hint } = STATUS[status];
  return (
    <span className="badge-group">
      <Badge tone={tone} title={hint}>
        <span className="badge-dot" aria-hidden="true" />
        {label}
      </Badge>
      {deferred && <Badge title="Deferred: moved to the end of the review queue">Deferred</Badge>}
    </span>
  );
}

export function ConfidenceBadge({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>;
  return (
    <Badge tone={confidenceTone(value)} title="Extraction confidence">
      {formatPercent(value)}
    </Badge>
  );
}

export function DocTypeBadge({ type }: { type: DocType | null }) {
  if (type === null) return <span className="muted">Unclassified</span>;
  const Icon = DOC_TYPE_ICON[type];
  return (
    <span className="type-badge">
      <Icon size={13} aria-hidden="true" />
      {DOC_TYPE_LABELS[type]}
    </span>
  );
}

const ORIGIN_ICON: Record<FieldOrigin, LucideIcon> = { rules: Cpu, model: Sparkles, human: PenLine };

export function OriginBadge({ origin }: { origin: FieldOrigin }) {
  const { label, tone, description } = FIELD_ORIGIN[origin];
  return (
    <Badge tone={tone} icon={ORIGIN_ICON[origin]} title={description}>
      {label}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  const { label, tone, icon } = SEVERITY[severity];
  return (
    <Badge tone={tone} icon={icon}>
      {label}
    </Badge>
  );
}

export function InjectionBadge() {
  return (
    <Badge tone="danger" icon={ShieldAlert} title="Text that tries to instruct the AI was quarantined">
      Injection quarantined
    </Badge>
  );
}

export function Meter({ value, tone, label }: { value: number; tone: Tone; label: string }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <span className="meter" data-tone={tone} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={label}>
      <span className="meter-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

export function ConfidenceMeter({ value }: { value: number }) {
  return (
    <span className="confidence">
      <Meter value={value} tone={confidenceTone(value)} label="Confidence" />
      <span className="num">{formatPercent(value)}</span>
    </span>
  );
}
