import { BadgeCheck, CircleAlert, Download, ShieldAlert } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { originalUrl } from "../api/client";
import { useNow } from "../api/hooks";
import { LEGAL_DOC_TYPES, type DocumentDetail, type Obligation, type ReviewAction } from "../api/types";
import { MODEL_KIND, RISK_LEVEL, ROUTE, SOURCE, STATUS } from "../lib/display";
import { formatBytes, formatDateTime, formatMs, formatPercent, isoDay, shortHash, shortModelName } from "../lib/format";
import { AuditTimeline } from "./AuditTimeline";
import { Badge, ConfidenceBadge, DocTypeBadge, InjectionBadge, StatusBadge } from "./Badge";
import { ClauseList, LegalDisclaimer, SummaryPanel } from "./ClauseList";
import { EvidenceViewer, type EvidenceTarget } from "./EvidenceViewer";
import { FieldTable } from "./FieldTable";
import { IssueList } from "./IssueList";
import { LineItems } from "./LineItems";
import { ObligationList } from "./ObligationList";
import { ReviewActions } from "./ReviewActions";
import { Spinner } from "./States";
import { type TabSpec, Tabs } from "./Tabs";

type TabId = "fields" | "line_items" | "issues" | "summary" | "obligations" | "history";

interface DocumentWorkspaceProps {
  detail: DocumentDetail;
  onDetail: (detail: DocumentDetail) => void;
  onReload: () => void;
  onDeleted: () => void;
  onReviewed?: (detail: DocumentDetail, action: ReviewAction) => void;
  keyboard?: boolean;
  /** Collapses provenance so the decision controls sit higher, for the review queue. */
  compact?: boolean;
  nav?: ReactNode;
}

export function DocumentWorkspace({ detail, onDetail, onReload, onDeleted, onReviewed, keyboard = false, compact = false, nav }: DocumentWorkspaceProps) {
  const [target, setTarget] = useState<EvidenceTarget | null>(null);
  const [tab, setTab] = useState<TabId>("fields");
  const now = useNow(60_000);

  useEffect(() => setTarget(null), [detail.id]);

  const fieldLabels = useMemo(() => new Map(detail.fields.map((f) => [f.key, f.label])), [detail.fields]);
  const errorCount = detail.issues.filter((i) => i.severity === "error").length;
  const openObligations = detail.obligations.filter((o) => o.status === "open").length;
  const isLegal = detail.doc_type !== null && LEGAL_DOC_TYPES.includes(detail.doc_type);
  const settled = detail.status !== "processing" && detail.status !== "imported";
  const selectedId = target?.id ?? null;

  const tabs: TabSpec<TabId>[] = [
    { id: "fields", label: "Fields", count: detail.fields.length },
    { id: "line_items", label: "Line items", count: detail.line_items.length },
    { id: "issues", label: "Issues", count: detail.issues.length, tone: errorCount > 0 ? "danger" : detail.issues.length > 0 ? "warning" : undefined },
    { id: "summary", label: "Summary & clauses", count: detail.clauses.length },
    { id: "obligations", label: "Obligations", count: openObligations },
    { id: "history", label: "History" },
  ];

  const onObligation = (updated: Obligation) =>
    onDetail({ ...detail, obligations: detail.obligations.map((o) => (o.id === updated.id ? updated : o)) });

  return (
    <div className="workspace">
      <div className="workspace-viewer">
        <EvidenceViewer docId={detail.id} filename={detail.filename} pages={detail.pages} target={target} onSelect={setTarget} />
      </div>

      <div className="workspace-panel">
        {nav}
        <DocumentHeader detail={detail} provenanceOpen={!compact} />

        {detail.status === "processing" && (
          <div className="banner" data-tone="info" role="status">
            <Spinner size={16} />
            <p>
              <strong>Processing on this device.</strong> {detail.stage ?? "Working"}…
            </p>
          </div>
        )}
        {detail.status === "failed" && (
          <div className="banner" data-tone="danger" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <p>
              <strong>Processing failed.</strong> {detail.error ?? "No reason was recorded."} Reprocess the document or delete it.
            </p>
          </div>
        )}
        {detail.injection_suspected && (
          <div className="banner" data-tone="danger" role="note">
            <ShieldAlert size={16} aria-hidden="true" />
            <p>
              <strong>This document contains text aimed at instructing an AI.</strong> It was quarantined and excluded from AI
              processing. Check the values against the original page.
            </p>
          </div>
        )}

        <ReviewActions
          doc={detail}
          keyboard={keyboard}
          onReviewed={(updated, action) => (onReviewed ?? onDetail)(updated, action)}
          onReprocessed={onReload}
          onDeleted={onDeleted}
        />

        <Tabs tabs={tabs} active={tab} onChange={setTab} label="Document record" />
        <div className="tab-panel" role="tabpanel">
          {tab === "fields" && (
            <>
              <p className="panel-intro">
                <strong>Extracted facts.</strong> Each value links to where it was read on the page. Corrections are recorded
                with the original AI value.
              </p>
              <FieldTable docId={detail.id} fields={detail.fields} editable={settled} selectedId={selectedId} onSelect={setTarget} onSaved={onDetail} />
            </>
          )}
          {tab === "line_items" && (
            <LineItems items={detail.line_items} currency={detail.currency} total={detail.total_amount} selectedId={selectedId} onSelect={setTarget} />
          )}
          {tab === "issues" && <IssueList issues={detail.issues} fieldLabels={fieldLabels} selectedId={selectedId} onSelect={setTarget} />}
          {tab === "summary" && (
            <div className="stack">
              {isLegal && <LegalDisclaimer />}
              <SummaryPanel summary={detail.summary} selectedId={selectedId} onSelect={setTarget} />
              <ClauseList clauses={detail.clauses} selectedId={selectedId} onSelect={setTarget} />
            </div>
          )}
          {tab === "obligations" && (
            <ObligationList obligations={detail.obligations} today={isoDay(now)} onUpdated={onObligation} selectedId={selectedId} onSelect={setTarget} />
          )}
          {tab === "history" && <AuditTimeline entries={detail.history} now={now} fieldLabels={fieldLabels} />}
        </div>
      </div>
    </div>
  );
}

function DocumentHeader({ detail, provenanceOpen }: { detail: DocumentDetail; provenanceOpen: boolean }) {
  const p = detail.provenance;
  return (
    <header className="doc-header">
      <div className="doc-title-row">
        <div>
          <h1 className="doc-title">{detail.title ?? detail.filename}</h1>
          {detail.title && <p className="doc-filename">{detail.filename}</p>}
        </div>
        <a className="btn btn-sm" href={originalUrl(detail.id)} download>
          <Download size={14} aria-hidden="true" /> Download original
        </a>
      </div>
      <div className="row wrap gap-1">
        <DocTypeBadge type={detail.doc_type} />
        {detail.classification_method && detail.doc_type_confidence !== null && (
          <span className="muted small">
            classified by {detail.classification_method} · {formatPercent(detail.doc_type_confidence)}
          </span>
        )}
        <StatusBadge status={detail.status} deferred={detail.deferred} />
        <ConfidenceBadge value={detail.confidence} />
        {detail.risk_level !== "none" && <Badge tone={RISK_LEVEL[detail.risk_level].tone}>{RISK_LEVEL[detail.risk_level].label}</Badge>}
        {detail.injection_suspected && <InjectionBadge />}
      </div>
      {detail.approval_kind && (
        <p className="approval-line">
          <BadgeCheck size={15} aria-hidden="true" />
          {detail.approval_kind === "human"
            ? `Human-approved by ${detail.approved_by ?? "a reviewer"}`
            : "Auto-approved by policy (high confidence, no issues)"}
          <span className="muted"> · {formatDateTime(detail.approved_at)}</span>
        </p>
      )}
      {!detail.approval_kind && <p className="muted small">{STATUS[detail.status].hint}</p>}
      <details className="provenance-wrap" open={provenanceOpen}>
        <summary>
          Provenance · SHA-256 <span className="mono">{shortHash(p.sha256)}</span>
          {p.route && ` · ${ROUTE[p.route]}`}
          {p.processing_ms !== null && ` · ${formatMs(p.processing_ms)}`}
        </summary>
      <dl className="provenance">
        <div>
          <dt>SHA-256</dt>
          <dd className="mono" title={p.sha256}>
            {shortHash(p.sha256)}
          </dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{p.model_kind ? `${MODEL_KIND[p.model_kind]}${p.model_name ? ` · ${shortModelName(p.model_name)}` : ""}` : "—"}</dd>
        </div>
        <div>
          <dt>Route</dt>
          <dd>{p.route ? ROUTE[p.route] : "—"}</dd>
        </div>
        <div>
          <dt>OCR</dt>
          <dd>{p.ocr_engine ?? "Not used"}</dd>
        </div>
        <div>
          <dt>Schema</dt>
          <dd className="mono">{p.schema_version ?? "—"}</dd>
        </div>
        <div>
          <dt>Processed</dt>
          <dd>
            {formatDateTime(p.processed_at)}
            {p.processing_ms !== null && <span className="muted"> · {formatMs(p.processing_ms)}</span>}
          </dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>
            {SOURCE[detail.source]} · {formatBytes(detail.size_bytes)}
            {detail.page_count !== null && ` · ${detail.page_count} page${detail.page_count === 1 ? "" : "s"}`}
          </dd>
        </div>
      </dl>
      </details>
    </header>
  );
}
