import { Inbox, Play, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { processIntake, removeIntake } from "../api/client";
import type { IntakeItem } from "../api/types";
import { PREFLIGHT, SOURCE } from "../lib/display";
import { formatBytes } from "../lib/format";
import { Badge, DocTypeBadge, StatusBadge } from "./Badge";
import { EmptyState, Spinner } from "./States";
import { useToast } from "./Toast";

const MIME_LABEL: Record<string, string> = {
  "application/pdf": "PDF",
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "image/tiff": "TIFF",
  "text/plain": "TXT",
};

/** Accepted and stored, but not yet sent through the pipeline. */
export const isQueued = (item: IntakeItem) => item.preflight === "accepted" && item.doc_status === "imported";
export const isProcessing = (item: IntakeItem) => item.doc_status === "processing";

interface IntakeQueueProps {
  items: readonly IntakeItem[];
  onChanged: () => void;
}

export function IntakeQueue({ items, onChanged }: IntakeQueueProps) {
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [processing, setProcessing] = useState(false);
  const [removing, setRemoving] = useState<ReadonlySet<number>>(new Set());
  const toast = useToast();

  const queued = useMemo(() => items.filter(isQueued), [items]);
  const selectedQueued = queued.filter((item) => selected.has(item.id));
  const counts = useMemo(
    () => ({
      processing: items.filter(isProcessing).length,
      rejected: items.filter((i) => i.preflight === "rejected").length,
      duplicates: items.filter((i) => i.preflight === "duplicate").length,
    }),
    [items],
  );

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allSelected = queued.length > 0 && selectedQueued.length === queued.length;
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(queued.map((i) => i.id)));

  const process = async () => {
    setProcessing(true);
    try {
      const ids = selectedQueued.length > 0 ? selectedQueued.map((i) => i.id) : null;
      const result = await processIntake(ids);
      toast.push("info", `Processing ${result.queued.length} document${result.queued.length === 1 ? "" : "s"} on this device.`);
      setSelected(new Set());
      onChanged();
    } catch (err) {
      toast.push("danger", err instanceof Error ? err.message : "Could not start processing.");
    } finally {
      setProcessing(false);
    }
  };

  const remove = async (item: IntakeItem) => {
    setRemoving((prev) => new Set(prev).add(item.id));
    try {
      await removeIntake(item.id);
      onChanged();
    } catch (err) {
      toast.push("danger", err instanceof Error ? err.message : "Could not remove the item.");
    } finally {
      setRemoving((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
    }
  };

  const processCount = selectedQueued.length > 0 ? selectedQueued.length : queued.length;
  const processLabel =
    selectedQueued.length > 0
      ? `Process ${processCount} selected`
      : `Process ${processCount} queued document${processCount === 1 ? "" : "s"}`;

  return (
    <section className="card" aria-labelledby="intake-heading">
      <header className="card-header">
        <div>
          <h2 id="intake-heading" className="card-title">
            Intake queue
          </h2>
          <p className="card-subtitle" aria-live="polite">
            {items.length} file{items.length === 1 ? "" : "s"} · {queued.length} queued · {counts.processing} processing ·{" "}
            {counts.rejected} rejected · {counts.duplicates} duplicate{counts.duplicates === 1 ? "" : "s"}
          </p>
        </div>
        <button type="button" className="btn btn-primary" disabled={processCount === 0 || processing} onClick={() => void process()}>
          <Play size={15} aria-hidden="true" />
          {processing ? "Starting…" : processLabel}
        </button>
      </header>
      <p className="card-note">
        Preflight runs before anything is saved. <strong>Rejected and duplicate files are never stored</strong>; only the
        file name and reason are kept in this list.
      </p>
      {items.length === 0 ? (
        <EmptyState icon={Inbox} title="Nothing imported yet">
          Drop files or a folder above, scan pages, or import the demo samples.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table intake-table">
            <thead>
              <tr>
                <th scope="col" className="cell-check">
                  <input type="checkbox" aria-label="Select all queued documents" checked={allSelected} disabled={queued.length === 0} onChange={toggleAll} />
                </th>
                <th scope="col">File</th>
                <th scope="col">Source</th>
                <th scope="col">Type</th>
                <th scope="col" className="num">Size</th>
                <th scope="col" className="num">Pages</th>
                <th scope="col">Preflight</th>
                <th scope="col">Classification</th>
                <th scope="col">Processing</th>
                <th scope="col">Error</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <IntakeRow
                  key={item.id}
                  item={item}
                  selected={selected.has(item.id)}
                  removing={removing.has(item.id)}
                  onToggle={() => toggle(item.id)}
                  onRemove={() => void remove(item)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface IntakeRowProps {
  item: IntakeItem;
  selected: boolean;
  removing: boolean;
  onToggle: () => void;
  onRemove: () => void;
}

function IntakeRow({ item, selected, removing, onToggle, onRemove }: IntakeRowProps) {
  const preflight = PREFLIGHT[item.preflight];
  const queued = isQueued(item);
  const opensDocument = item.document_id !== null && item.preflight === "accepted" && item.doc_status !== "imported";
  return (
    <tr data-preflight={item.preflight} data-selected={selected || undefined}>
      <td className="cell-check">
        {queued && <input type="checkbox" aria-label={`Select ${item.filename}`} checked={selected} onChange={onToggle} />}
      </td>
      <td className="cell-file">
        {opensDocument ? <Link to={`/documents/${item.document_id}`}>{item.filename}</Link> : <span>{item.filename}</span>}
      </td>
      <td>{SOURCE[item.source]}</td>
      <td>{item.mime ? (MIME_LABEL[item.mime] ?? item.mime) : "—"}</td>
      <td className="num">{formatBytes(item.size_bytes)}</td>
      <td className="num">{item.page_count ?? "—"}</td>
      <td>
        <Badge tone={preflight.tone} icon={preflight.icon}>
          {preflight.label}
        </Badge>
        {item.preflight === "rejected" && item.error && <div className="cell-sub cell-reason">{item.error}</div>}
        {item.preflight === "duplicate" && item.duplicate_of && (
          <div className="cell-sub">
            <Link to={`/documents/${item.duplicate_of}`}>Open the original</Link>
          </div>
        )}
      </td>
      <td>{item.doc_type ? <DocTypeBadge type={item.doc_type} /> : <span className="muted">—</span>}</td>
      <td>
        {item.doc_status ? (
          <div className="status-cell">
            <StatusBadge status={item.doc_status} />
            {item.doc_status === "processing" && <Spinner label={item.stage ?? "Working"} size={14} />}
          </div>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="cell-reason">{item.preflight === "accepted" && item.error ? item.error : ""}</td>
      <td className="cell-actions">
        <button
          type="button"
          className="btn btn-sm btn-icon-only"
          aria-label={`Remove ${item.filename} from the queue`}
          title={queued ? "Remove and delete the stored file" : "Dismiss from this list (any document stays)"}
          disabled={removing || item.doc_status === "processing"}
          onClick={onRemove}
        >
          <X size={15} aria-hidden="true" />
        </button>
      </td>
    </tr>
  );
}
