import { CalendarCheck, Check, LocateFixed, Undo2, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { updateObligation } from "../api/client";
import type { Obligation, ObligationStatus } from "../api/types";
import { OBLIGATION_KIND, OBLIGATION_STATUS } from "../lib/display";
import { formatDate, relativeDue } from "../lib/format";
import { Badge } from "./Badge";
import type { EvidenceTarget } from "./EvidenceViewer";
import { EmptyState } from "./States";
import { useToast } from "./Toast";

interface ObligationListProps {
  obligations: readonly Obligation[];
  today: string;
  onUpdated: (obligation: Obligation) => void;
  showDocument?: boolean;
  selectedId?: string | null;
  onSelect?: (target: EvidenceTarget) => void;
}

export function DueText({ due, today }: { due: string | null; today: string }) {
  if (due === null) return <span className="muted">No date</span>;
  const rel = relativeDue(due, today);
  return (
    <span className="due">
      <span className="num">{formatDate(due)}</span>
      <span className="due-rel" data-tone={rel.tone}>
        {rel.text}
      </span>
    </span>
  );
}

export function ObligationList({ obligations, today, onUpdated, showDocument = false, selectedId = null, onSelect }: ObligationListProps) {
  const [busy, setBusy] = useState<number | null>(null);
  const toast = useToast();

  if (obligations.length === 0) {
    return (
      <EmptyState icon={CalendarCheck} title="No obligations or deadlines">
        Payment terms, renewals, notice periods, and deliverables appear here when a document contains them.
      </EmptyState>
    );
  }

  const setStatus = async (obligation: Obligation, status: ObligationStatus) => {
    setBusy(obligation.id);
    try {
      onUpdated(await updateObligation(obligation.id, { status }));
    } catch (err) {
      toast.push("danger", err instanceof Error ? err.message : "Could not update the obligation.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Type</th>
            <th scope="col">Obligation</th>
            <th scope="col">Party</th>
            <th scope="col">Due</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {obligations.map((ob) => {
            const targetId = `obligation:${ob.id}`;
            const status = OBLIGATION_STATUS[ob.status];
            const evidence = ob.evidence;
            return (
              <tr key={ob.id} data-selected={selectedId === targetId || undefined} data-closed={ob.status !== "open" || undefined}>
                <td>
                  <Badge>{OBLIGATION_KIND[ob.kind]}</Badge>
                </td>
                <td>
                  {ob.description}
                  {showDocument && (
                    <div className="cell-sub">
                      <Link to={`/documents/${ob.doc_id}`}>{ob.doc_filename}</Link>
                    </div>
                  )}
                </td>
                <td>{ob.party ?? <span className="muted">—</span>}</td>
                <td>
                  <DueText due={ob.due_date} today={today} />
                </td>
                <td>
                  <Badge tone={status.tone}>{status.label}</Badge>
                </td>
                <td className="cell-actions">
                  {onSelect && evidence && (
                    <button type="button" className="btn btn-sm btn-icon-only" aria-label="Show in document" onClick={() => onSelect({ id: targetId, label: OBLIGATION_KIND[ob.kind], evidence })}>
                      <LocateFixed size={15} aria-hidden="true" />
                    </button>
                  )}
                  {ob.status === "open" ? (
                    <>
                      <button type="button" className="btn btn-sm" disabled={busy === ob.id} onClick={() => setStatus(ob, "done")}>
                        <Check size={14} aria-hidden="true" /> Done
                      </button>
                      <button type="button" className="btn btn-sm btn-ghost" disabled={busy === ob.id} onClick={() => setStatus(ob, "dismissed")}>
                        <X size={14} aria-hidden="true" /> Dismiss
                      </button>
                    </>
                  ) : (
                    <button type="button" className="btn btn-sm btn-ghost" disabled={busy === ob.id} onClick={() => setStatus(ob, "open")}>
                      <Undo2 size={14} aria-hidden="true" /> Reopen
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
