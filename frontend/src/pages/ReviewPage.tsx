import { CheckCheck, CircleCheck, CircleX, ClipboardCheck, Keyboard, X } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { batchReview, getDocument, getReviewQueue } from "../api/client";
import { dataOf, useResource } from "../api/hooks";
import {
  DOC_TYPE_LABELS,
  type BatchReviewResult,
  type DocumentSummary,
  type ReviewGroup,
  type ReviewQueue,
} from "../api/types";
import { ConfidenceBadge, DocTypeBadge, StatusBadge } from "../components/Badge";
import { DocumentWorkspace } from "../components/DocumentWorkspace";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, Loaded, Spinner } from "../components/States";
import { useToast } from "../components/Toast";
import { formatMoney } from "../lib/format";

interface Section {
  title: string;
  items: DocumentSummary[];
}

function sections(items: readonly DocumentSummary[]): Section[] {
  return [
    { title: "Needs review", items: items.filter((d) => d.status === "needs_review" && !d.deferred) },
    { title: "Ready to approve", items: items.filter((d) => d.status === "ready" && !d.deferred) },
    { title: "Deferred", items: items.filter((d) => d.deferred) },
  ].filter((s) => s.items.length > 0);
}

function groupLabel(group: ReviewGroup): string {
  const type = group.doc_type ? DOC_TYPE_LABELS[group.doc_type] : "Unclassified";
  return `${type} · ${group.counterparty ?? "unknown party"} (${group.doc_ids.length})`;
}

function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}

export function ReviewPage() {
  const queue = useResource("review-queue", getReviewQueue);
  return (
    <div className="page page-wide">
      <PageHeader
        title="Review queue"
        subtitle="Approve what is right, correct what is not. Nothing is exported until it has been approved."
      />
      <Loaded resource={queue} loadingLabel="Loading review queue…">
        {(data) => <ReviewBoard data={data} onChanged={queue.reload} />}
      </Loaded>
    </div>
  );
}

function ReviewBoard({ data, onChanged }: { data: ReviewQueue; onChanged: () => void }) {
  const [params, setParams] = useSearchParams();
  const [groupKey, setGroupKey] = useState("");
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const groupSelectId = useId();

  const group = data.groups.find((g) => g.key === groupKey) ?? null;
  const visible = group ? data.items.filter((d) => group.doc_ids.includes(d.id)) : data.items;
  const grouped = useMemo(() => sections(visible), [visible]);
  const ordered = useMemo(() => grouped.flatMap((s) => s.items), [grouped]);

  const requested = params.get("doc");
  const currentId = ordered.some((d) => d.id === requested) ? requested : (ordered[0]?.id ?? null);
  const select = (id: string | null) => setParams(id ? { doc: id } : {}, { replace: true });

  const advanceFrom = (id: string) => {
    const index = ordered.findIndex((d) => d.id === id);
    const next = ordered[index + 1] ?? ordered[index - 1] ?? null;
    select(next?.id ?? null);
    onChanged();
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      const step = event.key === "j" ? 1 : event.key === "k" ? -1 : 0;
      if (step === 0) return;
      const index = ordered.findIndex((d) => d.id === currentId);
      const next = ordered[index + step];
      if (next) {
        event.preventDefault();
        select(next.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (data.items.length === 0) {
    return (
      <EmptyState icon={CheckCheck} title="The review queue is clear" action={<Link to="/documents" className="btn btn-sm">Browse documents</Link>}>
        Every processed document has been approved, rejected, or is still processing.
      </EmptyState>
    );
  }

  return (
    <div className="review-layout">
      <aside className="review-list" aria-label="Documents to review">
        <div className="field">
          <label htmlFor={groupSelectId}>Similar documents</label>
          <select
            id={groupSelectId}
            className="input"
            value={groupKey}
            onChange={(e) => {
              setGroupKey(e.target.value);
              setChecked(new Set());
            }}
          >
            <option value="">Whole queue ({data.items.length})</option>
            {data.groups.map((g) => (
              <option key={g.key} value={g.key}>
                {groupLabel(g)}
              </option>
            ))}
          </select>
          {data.groups.length === 0 && <p className="hint">Groups appear when two or more documents share a type and counterparty.</p>}
        </div>

        {group && (
          <BatchBar
            key={group.key}
            items={visible}
            checked={checked}
            onCheck={setChecked}
            onDone={() => {
              setChecked(new Set());
              onChanged();
            }}
          />
        )}

        {grouped.map((section) => (
          <section key={section.title} className="review-section">
            <h2 className="review-section-title">
              {section.title} <span className="num muted">{section.items.length}</span>
            </h2>
            <ul>
              {section.items.map((doc) => (
                <li key={doc.id} className="review-item" data-current={doc.id === currentId || undefined} data-deferred={doc.deferred || undefined}>
                  {group && (
                    <input
                      type="checkbox"
                      aria-label={`Select ${doc.filename} for batch review`}
                      checked={checked.has(doc.id)}
                      onChange={() =>
                        setChecked((prev) => {
                          const next = new Set(prev);
                          if (next.has(doc.id)) next.delete(doc.id);
                          else next.add(doc.id);
                          return next;
                        })
                      }
                    />
                  )}
                  <button type="button" className="review-item-button" aria-current={doc.id === currentId || undefined} onClick={() => select(doc.id)}>
                    <span className="review-item-title">{doc.title ?? doc.filename}</span>
                    <span className="review-item-meta">
                      <DocTypeBadge type={doc.doc_type} />
                      <span className="num">{formatMoney(doc.total_amount, doc.currency)}</span>
                    </span>
                    <span className="review-item-meta">
                      <span className="muted">{doc.counterparty ?? "No counterparty"}</span>
                      <span className="row gap-1">
                        {doc.issue_count > 0 && (
                          <span className="issue-count" data-tone={doc.error_count > 0 ? "danger" : "warning"}>
                            {doc.issue_count} issue{doc.issue_count === 1 ? "" : "s"}
                          </span>
                        )}
                        <ConfidenceBadge value={doc.confidence} />
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </aside>

      <div className="review-detail">
        <p className="shortcut-help">
          <Keyboard size={14} aria-hidden="true" /> <kbd>A</kbd> approve · <kbd>R</kbd> reject · <kbd>D</kbd> defer · <kbd>J</kbd>/<kbd>K</kbd> next / previous
        </p>
        {currentId ? (
          <ReviewDetail key={currentId} id={currentId} onAdvance={() => advanceFrom(currentId)} />
        ) : (
          <EmptyState icon={ClipboardCheck} title="Select a document to review" />
        )}
      </div>
    </div>
  );
}

function ReviewDetail({ id, onAdvance }: { id: string; onAdvance: () => void }) {
  const doc = useResource(`review-document:${id}`, (signal) => getDocument(id, signal));
  const { state } = doc;
  if (state.kind === "loading") {
    return (
      <div className="loading-block">
        <Spinner label="Loading document…" />
      </div>
    );
  }
  const detail = dataOf(state);
  if (detail === null) return state.kind === "error" ? <ErrorState error={state.error} onRetry={doc.reload} /> : null;
  return (
    <DocumentWorkspace
      detail={detail}
      keyboard
      compact
      onDetail={doc.replace}
      onReviewed={onAdvance}
      onReload={onAdvance}
      onDeleted={onAdvance}
    />
  );
}

interface BatchBarProps {
  items: readonly DocumentSummary[];
  checked: ReadonlySet<string>;
  onCheck: (next: ReadonlySet<string>) => void;
  onDone: () => void;
}

function BatchBar({ items, checked, onCheck, onDone }: BatchBarProps) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ res: BatchReviewResult; names: ReadonlyMap<string, string> } | null>(null);
  const toast = useToast();
  const noteId = useId();
  const selected = items.filter((d) => checked.has(d.id));
  const allChecked = items.length > 0 && selected.length === items.length;
  const errorsSelected = selected.filter((d) => d.error_count > 0).length;

  const run = async (action: "approve" | "reject") => {
    setBusy(true);
    try {
      const res = await batchReview({ doc_ids: selected.map((d) => d.id), action, note: note.trim() === "" ? null : note.trim() });
      setResult({ res, names: new Map(selected.map((d) => [d.id, d.title ?? d.filename])) });
      const ok = res.results.filter((r) => r.ok).length;
      toast.push(ok === res.results.length ? "accent" : "danger", `${action === "approve" ? "Approved" : "Rejected"} ${ok} of ${res.results.length}.`);
      onDone();
    } catch (err) {
      toast.push("danger", err instanceof Error ? err.message : "Batch review failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="batch-bar">
      <label className="checkbox">
        <input type="checkbox" checked={allChecked} onChange={() => onCheck(allChecked ? new Set() : new Set(items.map((d) => d.id)))} />
        Select all in this group
      </label>
      <label htmlFor={noteId} className="sr-only">
        Batch note
      </label>
      <input id={noteId} className="input" type="text" placeholder="Note for the audit trail" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
      {errorsSelected > 0 && (
        <p className="hint" data-tone="danger">
          {errorsSelected} selected record{errorsSelected === 1 ? " has" : "s have"} errors; approving needs a note.
        </p>
      )}
      <div className="row gap-1">
        <button type="button" className="btn btn-primary btn-sm" disabled={busy || selected.length === 0} onClick={() => void run("approve")}>
          <CheckCheck size={14} aria-hidden="true" /> Approve selected ({selected.length})
        </button>
        <button type="button" className="btn btn-danger-outline btn-sm" disabled={busy || selected.length === 0} onClick={() => void run("reject")}>
          <X size={14} aria-hidden="true" /> Reject selected
        </button>
      </div>
      {result && (
        <ul className="batch-results" aria-live="polite">
          {result.res.results.map((r) => (
            <li key={r.doc_id} data-ok={r.ok}>
              {r.ok ? <CircleCheck size={14} aria-label="Done" /> : <CircleX size={14} aria-label="Failed" />} {result.names.get(r.doc_id) ?? r.doc_id}
              {r.status && <StatusBadge status={r.status} />}
              {r.error && <span className="error-text"> {r.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
