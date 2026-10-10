import { Activity, CalendarClock, Cpu, HardDrive, Lock, RefreshCw, ScanLine, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { getDashboard, getMetrics } from "../api/client";
import { useNow, useResource } from "../api/hooks";
import { DOC_TYPE_LABELS, type Dashboard, type DocStatus, type Metrics, type Obligation } from "../api/types";
import { AuditTimeline } from "../components/AuditTimeline";
import { Badge, DocTypeBadge } from "../components/Badge";
import { Card } from "../components/Card";
import { useSystemStatus } from "../components/Layout";
import { DueText } from "../components/ObligationList";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, Loaded } from "../components/States";
import { MODEL_KIND, OBLIGATION_KIND, STATUS, type Tone } from "../lib/display";
import { formatCount, formatMoney, formatMs, formatPercent, isoDay, shortModelName } from "../lib/format";

interface StatSpec {
  label: string;
  value: (d: Dashboard) => number;
  status: DocStatus | null;
  tone: Tone;
}

const STATS: readonly StatSpec[] = [
  { label: "Total documents", value: (d) => d.total_documents, status: null, tone: "neutral" },
  { label: "Processing", value: (d) => d.processing, status: "processing", tone: STATUS.processing.tone },
  { label: "Needs review", value: (d) => d.needs_review, status: "needs_review", tone: STATUS.needs_review.tone },
  { label: "Ready to approve", value: (d) => d.ready, status: "ready", tone: STATUS.ready.tone },
  { label: "Approved", value: (d) => d.approved, status: "approved", tone: STATUS.approved.tone },
  { label: "Failed", value: (d) => d.failed, status: "failed", tone: STATUS.failed.tone },
];

export function DashboardPage() {
  const dashboard = useResource("dashboard", getDashboard, { poll: (d) => (d && d.processing > 0 ? 3000 : 30_000) });
  const metrics = useResource("metrics", getMetrics, { poll: () => 30_000 });
  const now = useNow(60_000);
  const today = isoDay(now);

  return (
    <div className="page">
      <PageHeader
        title="Dashboard"
        subtitle="What needs a decision today, and what VaultAI has already verified."
        actions={
          <Link to="/import" className="btn btn-primary">
            <ScanLine size={15} aria-hidden="true" /> Import documents
          </Link>
        }
      />
      <Loaded resource={dashboard} loadingLabel="Loading dashboard…">
        {(d) => (
          <div className="stack-lg">
            <div className="stat-grid">
              {STATS.map((stat) => (
                <Link key={stat.label} to={stat.status ? `/documents?status=${stat.status}` : "/documents"} className="stat" data-tone={stat.tone}>
                  <span className="stat-label">{stat.label}</span>
                  <span className="stat-value num">{formatCount(stat.value(d))}</span>
                </Link>
              ))}
            </div>

            <div className="grid-2">
              <Card title="Upcoming deadlines" subtitle="Overdue and next 60 days" action={<Link to="/actions">Action Center</Link>}>
                <DeadlineList items={d.upcoming_deadlines} today={today} empty="No open deadlines in the next 60 days." />
              </Card>
              <Card title="Contract renewals" subtitle="Renewals, notice periods, expirations">
                <DeadlineList items={d.renewals} today={today} empty="No renewals or expirations coming up." />
              </Card>
            </div>

            <div className="grid-2">
              <Card title="Financial totals" subtitle="Approved records by currency; pending review shown separately">
                <FinancialTotals totals={d.financial_totals} />
              </Card>
              <Card title="Documents by type">
                <TypeBars byType={d.by_type} />
              </Card>
            </div>

            <div className="grid-2">
              <Card title="Recent activity" action={<Link to="/settings#audit">Full audit log</Link>}>
                <AuditTimeline entries={d.recent_activity.slice(0, 8)} now={now} showDocLinks compact />
              </Card>
              <div className="stack">
                <LocalProcessingPanel />
                <Card title="Performance" subtitle="Measured on this device">
                  <Loaded resource={metrics} loadingLabel="Loading metrics…">
                    {(m) => <MetricsPanel metrics={m} />}
                  </Loaded>
                </Card>
              </div>
            </div>
          </div>
        )}
      </Loaded>
    </div>
  );
}

function DeadlineList({ items, today, empty }: { items: readonly Obligation[]; today: string; empty: string }) {
  if (items.length === 0) return <EmptyState icon={CalendarClock} title={empty} />;
  return (
    <ul className="deadline-list">
      {items.map((ob) => (
        <li key={ob.id}>
          <DueText due={ob.due_date} today={today} />
          <div className="deadline-main">
            <span>{ob.description}</span>
            <span className="cell-sub">
              <Badge>{OBLIGATION_KIND[ob.kind]}</Badge> <Link to={`/documents/${ob.doc_id}`}>{ob.doc_filename}</Link>
              {ob.party && <> · {ob.party}</>}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function FinancialTotals({ totals }: { totals: Dashboard["financial_totals"] }) {
  if (totals.length === 0) return <EmptyState title="No approved amounts yet" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Currency</th>
            <th scope="col" className="num">Payables</th>
            <th scope="col" className="num">Receipts</th>
            <th scope="col" className="num">Checks</th>
            <th scope="col" className="num">Pending review</th>
          </tr>
        </thead>
        <tbody>
          {totals.map((t) => (
            <tr key={t.currency}>
              <th scope="row">{t.currency}</th>
              <td className="num">{formatMoney(t.approved_payables, t.currency)}</td>
              <td className="num">{formatMoney(t.approved_receipts, t.currency)}</td>
              <td className="num">{formatMoney(t.approved_checks, t.currency)}</td>
              <td className="num muted">{formatMoney(t.pending_review, t.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TypeBars({ byType }: { byType: Dashboard["by_type"] }) {
  const rows = [...byType].filter((r) => r.count > 0).sort((a, b) => b.count - a.count);
  if (rows.length === 0) return <EmptyState title="No documents yet" />;
  const max = Math.max(...rows.map((r) => r.count));
  return (
    <ul className="bar-list">
      {rows.map((row) => (
        <li key={row.doc_type}>
          <Link to={`/documents?type=${row.doc_type}`} className="bar-row" aria-label={`${DOC_TYPE_LABELS[row.doc_type]}: ${row.count}`}>
            <DocTypeBadge type={row.doc_type} />
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(row.count / max) * 100}%` }} />
            </span>
            <span className="num bar-value">{formatCount(row.count)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function LocalProcessingPanel() {
  const status = useSystemStatus();
  return (
    <section className="card local-panel">
      <header className="card-header">
        <div>
          <h2 className="card-title">Local processing</h2>
          {status?.local_only && status.egress_guard ? (
            <p className="local-claim">
              <Lock size={14} aria-hidden="true" /> All processing on this device
            </p>
          ) : (
            <p className="card-subtitle">{status ? "Egress guard is off; see Settings." : "Waiting for the local service."}</p>
          )}
        </div>
      </header>
      {status && (
        <dl className="kv">
          <div>
            <dt>
              <Cpu size={14} aria-hidden="true" /> Model
            </dt>
            <dd>
              {MODEL_KIND[status.model.kind]}
              {status.model.kind !== "mock" && ` · ${shortModelName(status.model.name)}`}
              {status.model.kind !== "mock" && !status.model.reachable && (
                <Badge tone="warning" icon={TriangleAlert}>
                  unreachable
                </Badge>
              )}
            </dd>
          </div>
          <div>
            <dt>
              <ScanLine size={14} aria-hidden="true" /> OCR engine
            </dt>
            <dd>{status.ocr_available ? (status.ocr_engine ?? "Available") : "Not installed (text PDFs and TXT only)"}</dd>
          </div>
          <div>
            <dt>
              <HardDrive size={14} aria-hidden="true" /> Data directory
            </dt>
            <dd className="mono">{status.data_dir}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

function MetricsPanel({ metrics: m }: { metrics: Metrics }) {
  const cacheTotal = m.cache_hits + m.cache_misses;
  const tiles: { label: string; value: string }[] = [
    { label: "Avg latency per page", value: formatMs(m.avg_ms_per_page) },
    { label: "Pages per minute", value: m.pages_per_minute === null ? "—" : m.pages_per_minute.toFixed(1) },
    { label: "Human review rate", value: formatPercent(m.human_review_rate) },
    { label: "Auto-approval rate", value: formatPercent(m.auto_approval_rate) },
    { label: "Field correction rate", value: formatPercent(m.field_correction_rate) },
    { label: "Cache hits", value: cacheTotal === 0 ? "—" : `${formatCount(m.cache_hits)} / ${formatCount(cacheTotal)}` },
    { label: "CPU load (1/5/15 min)", value: m.load_avg ? m.load_avg.map((v) => v.toFixed(2)).join(" · ") : "—" },
    { label: "Process memory", value: m.rss_mb === null ? "—" : `${Math.round(m.rss_mb)} MB` },
  ];
  if (m.gpu) {
    const { name, memory_used_mb, memory_total_mb, utilization_pct } = m.gpu;
    tiles.push({
      label: `GPU · ${name}`,
      value:
        memory_used_mb !== null && memory_total_mb !== null
          ? `${(memory_used_mb / 1024).toFixed(1)} / ${(memory_total_mb / 1024).toFixed(0)} GB`
          : utilization_pct !== null
            ? `${Math.round(utilization_pct)}% busy`
            : "—",
    });
  }
  return (
    <>
      <p className="muted small">
        <Activity size={13} aria-hidden="true" /> {formatCount(m.documents_processed)} documents · {formatCount(m.pages_processed)} pages processed
      </p>
      <dl className="metric-grid">
        {tiles.map((t) => (
          <div key={t.label} className="metric">
            <dt>{t.label}</dt>
            <dd className="num">{t.value}</dd>
          </div>
        ))}
      </dl>
      {m.recent.length > 0 && (
        <details className="recent-timings">
          <summary>
            <RefreshCw size={13} aria-hidden="true" /> Recent documents
          </summary>
          <ul>
            {m.recent.slice(0, 6).map((r) => (
              <li key={r.doc_id}>
                <Link to={`/documents/${r.doc_id}`}>{r.filename}</Link>
                <span className="num muted">
                  {r.pages} p · {formatMs(r.ms)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
