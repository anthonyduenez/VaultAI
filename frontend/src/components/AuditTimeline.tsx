import { History } from "lucide-react";
import { Link } from "react-router-dom";
import type { AuditEntry } from "../api/types";
import { AUDIT_ACTION } from "../lib/display";
import { formatDateTime, relativeTime } from "../lib/format";
import { EmptyState } from "./States";

interface AuditTimelineProps {
  entries: readonly AuditEntry[];
  now: Date;
  fieldLabels?: ReadonlyMap<string, string>;
  showDocLinks?: boolean;
  compact?: boolean;
}

export function AuditTimeline({ entries, now, fieldLabels, showDocLinks = false, compact = false }: AuditTimelineProps) {
  if (entries.length === 0) {
    return (
      <EmptyState icon={History} title="No activity yet">
        Every import, edit, approval, and export is recorded here.
      </EmptyState>
    );
  }
  return (
    <ol className="timeline" data-compact={compact || undefined}>
      {entries.map((entry) => (
        <li key={entry.id} className="timeline-item" data-action={entry.action}>
          <span className="timeline-dot" aria-hidden="true" />
          <div className="timeline-body">
            <p>
              <strong>{entry.actor}</strong> {AUDIT_ACTION[entry.action]}
              {entry.field_key && <> · {fieldLabels?.get(entry.field_key) ?? entry.field_key}</>}
              {showDocLinks && entry.doc_id && (
                <>
                  {" · "}
                  <Link to={`/documents/${entry.doc_id}`}>{entry.doc_filename ?? "open document"}</Link>
                </>
              )}
            </p>
            {(entry.before !== null || entry.after !== null) && (
              <p className="timeline-change">
                <Change before={entry.before} after={entry.after} />
              </p>
            )}
            {entry.note && <p className="timeline-note">“{entry.note}”</p>}
            <time dateTime={entry.at} title={formatDateTime(entry.at)} className="muted">
              {relativeTime(entry.at, now)}
            </time>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Change({ before, after }: { before: string | null; after: string | null }) {
  return (
    <>
      {before !== null && <s className="muted">{before}</s>}
      {before !== null && after !== null && " → "}
      {after !== null && <span>{after}</span>}
      {before !== null && after === null && <span className="muted"> (cleared)</span>}
    </>
  );
}
