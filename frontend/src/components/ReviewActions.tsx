import { Check, Clock, type LucideIcon, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { deleteDocument, reprocessDocument, reviewDocument } from "../api/client";
import type { DocumentDetail, DocumentSummary, ReviewAction } from "../api/types";
import { REPROCESSABLE, REVIEW_ACTION, REVIEW_ACTIONS } from "../lib/display";
import { useToast } from "./Toast";

type Reviewable = Pick<DocumentSummary, "id" | "filename" | "status" | "error_count">;

interface ReviewActionsProps {
  doc: Reviewable;
  onReviewed: (detail: DocumentDetail, action: ReviewAction) => void;
  onReprocessed: () => void;
  onDeleted: () => void;
  /** Enables A / R / D shortcuts while focus is outside a text field. */
  keyboard?: boolean;
}

const ICON: Record<ReviewAction, LucideIcon> = {
  approve: Check,
  reject: X,
  defer: Clock,
  reopen: RotateCcw,
};

const BUTTON_CLASS: Record<ReviewAction, string> = {
  approve: "btn btn-primary",
  reject: "btn btn-danger-outline",
  defer: "btn",
  reopen: "btn",
};

type Busy = ReviewAction | "reprocess" | "delete" | null;

function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}

export function ReviewActions({ doc, onReviewed, onReprocessed, onDeleted, keyboard = false }: ReviewActionsProps) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const toast = useToast();
  const noteId = useId();
  const reasonId = useId();

  useEffect(() => {
    setNote("");
    setError(null);
    setConfirmDelete(false);
  }, [doc.id]);

  const allowed = REVIEW_ACTIONS[doc.status];
  const noteRequired = doc.error_count > 0 && allowed.includes("approve");
  const approveBlocked = noteRequired && note.trim() === "";

  const isDisabled = (action: ReviewAction) => busy !== null || !allowed.includes(action) || (action === "approve" && approveBlocked);

  const run = async (action: ReviewAction) => {
    if (isDisabled(action)) return;
    setBusy(action);
    setError(null);
    try {
      const detail = await reviewDocument(doc.id, { action, note: note.trim() === "" ? null : note.trim() });
      toast.push("accent", `${REVIEW_ACTION[action].past}: ${doc.filename}`);
      setNote("");
      onReviewed(detail, action);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The action failed.");
    } finally {
      setBusy(null);
    }
  };

  const runRef = useRef(run);
  useEffect(() => {
    runRef.current = run;
  });

  useEffect(() => {
    if (!keyboard) return;
    const keys: Record<string, ReviewAction> = { a: "approve", r: "reject", d: "defer" };
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      const action = keys[event.key.toLowerCase()];
      if (action) {
        event.preventDefault();
        void runRef.current(action);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keyboard]);

  const reprocess = async () => {
    setBusy("reprocess");
    setError(null);
    try {
      await reprocessDocument(doc.id);
      toast.push("info", "Reprocessing locally. Your edits are kept and re-applied.");
      onReprocessed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reprocess.");
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy("delete");
    try {
      await deleteDocument(doc.id);
      toast.push("info", `Deleted ${doc.filename} and its derived files. The audit trail remains.`);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete.");
      setBusy(null);
    }
  };

  return (
    <section className="review-actions" aria-label="Review decision">
      {allowed.length > 0 && (
        <div className="field">
          <label htmlFor={noteId}>
            Reviewer note {noteRequired ? <span className="required-text">required to approve</span> : <span className="muted">optional</span>}
          </label>
          <textarea
            id={noteId}
            className="input"
            rows={2}
            maxLength={1000}
            value={note}
            placeholder={noteRequired ? "Explain why this record is correct despite the flagged errors" : "Add context for the audit trail"}
            aria-describedby={noteRequired ? reasonId : undefined}
            onChange={(e) => setNote(e.target.value)}
          />
          {noteRequired && (
            <p id={reasonId} className="hint" data-tone="danger">
              This record has {doc.error_count} unresolved error{doc.error_count === 1 ? "" : "s"}. Approving it anyway needs a
              note so the audit trail shows why.
            </p>
          )}
        </div>
      )}
      <div className="row wrap gap-1">
        {(["approve", "reject", "defer", "reopen"] as const)
          .filter((action) => allowed.includes(action))
          .map((action) => {
            const Icon = ICON[action];
            const { label, key } = REVIEW_ACTION[action];
            return (
              <button key={action} type="button" className={BUTTON_CLASS[action]} disabled={isDisabled(action)} onClick={() => void run(action)} aria-keyshortcuts={keyboard && key ? key : undefined}>
                <Icon size={15} aria-hidden="true" />
                {busy === action ? `${label}…` : label}
                {keyboard && key && <kbd>{key}</kbd>}
              </button>
            );
          })}
        <span className="spacer" />
        {REPROCESSABLE[doc.status] && (
          <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={() => void reprocess()}>
            <RefreshCw size={15} aria-hidden="true" /> Reprocess
          </button>
        )}
        {confirmDelete ? (
          <span className="confirm" role="group" aria-label="Confirm delete">
            <span>Delete the file, pages, and records?</span>
            <button type="button" className="btn btn-danger btn-sm" disabled={busy !== null} onClick={() => void remove()}>
              Delete permanently
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" className="btn btn-ghost btn-danger-text" disabled={busy !== null || doc.status === "processing"} onClick={() => setConfirmDelete(true)}>
            <Trash2 size={15} aria-hidden="true" /> Delete
          </button>
        )}
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
