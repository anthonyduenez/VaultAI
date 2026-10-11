import { Check, CircleCheck, Info, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { errorMessage, getActions, updateObligation } from "../api/client";
import { useResource } from "../api/hooks";
import type { ActionCategory, ActionCenter, ActionItem, ObligationStatus } from "../api/types";
import { DocTypeBadge, SeverityBadge } from "../components/Badge";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, Loaded } from "../components/States";
import { useToast } from "../components/Toast";
import { ACTION_CATEGORY, SEVERITY } from "../lib/display";
import { formatDate } from "../lib/format";

export function ActionsPage() {
  const actions = useResource("actions", getActions);
  return (
    <div className="page">
      <PageHeader title="Action Center" subtitle="Deadlines, risks, and decisions computed from your approved and pending records." />
      <div className="banner" data-tone="info" role="note">
        <Info size={16} aria-hidden="true" />
        <p>
          VaultAI never sends emails, signs documents, or changes external systems. Actions here are local reminders.
        </p>
      </div>
      <Loaded resource={actions} loadingLabel="Loading actions…">
        {(data) => <ActionBoard data={data} onChanged={actions.reload} />}
      </Loaded>
    </div>
  );
}

function ActionBoard({ data, onChanged }: { data: ActionCenter; onChanged: () => void }) {
  const [filter, setFilter] = useState<ActionCategory | "all">("all");
  const [busy, setBusy] = useState<number | null>(null);
  const toast = useToast();

  const shown = data.counts.filter((c) => filter === "all" || filter === c.category);

  const setObligation = async (item: ActionItem, status: ObligationStatus) => {
    if (item.obligation_id === null) return;
    setBusy(item.obligation_id);
    try {
      await updateObligation(item.obligation_id, { status });
      toast.push("accent", status === "done" ? "Marked done." : "Dismissed.");
      onChanged();
    } catch (err) {
      toast.push("danger", errorMessage(err, "Could not update."));
    } finally {
      setBusy(null);
    }
  };

  if (data.items.length === 0) {
    return (
      <EmptyState icon={CircleCheck} title="Nothing needs attention">
        No open deadlines, flagged risks, or pending reviews.
      </EmptyState>
    );
  }

  return (
    <div className="stack">
      <div className="chip-row" role="group" aria-label="Filter by category">
        <button type="button" className="chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
          All <span className="num">{data.items.length}</span>
        </button>
        {data.counts.map(({ category, count }) => {
          const Icon = ACTION_CATEGORY[category].icon;
          return (
            <button key={category} type="button" className="chip" aria-pressed={filter === category} onClick={() => setFilter(category)}>
              <Icon size={13} aria-hidden="true" /> {ACTION_CATEGORY[category].label} <span className="num">{count}</span>
            </button>
          );
        })}
      </div>

      {shown.map(({ category, count }) => {
        const items = data.items.filter((i) => i.category === category);
        const Icon = ACTION_CATEGORY[category].icon;
        return (
          <section key={category} className="card" aria-label={ACTION_CATEGORY[category].label}>
            <header className="card-header">
              <h2 className="card-title">
                <Icon size={16} aria-hidden="true" /> {ACTION_CATEGORY[category].label}
              </h2>
              <span className="num muted">{count}</span>
            </header>
            <ul className="action-list">
              {items.map((item) => (
                <li key={item.id} className="action-item" data-tone={SEVERITY[item.severity].tone}>
                  <div className="action-main">
                    <div className="row wrap gap-1">
                      <SeverityBadge severity={item.severity} />
                      <strong>{item.title}</strong>
                    </div>
                    {item.detail && <p className="action-detail">{item.detail}</p>}
                    <p className="action-meta">
                      {item.doc_id && (
                        <>
                          <DocTypeBadge type={item.doc_type} /> <Link to={`/documents/${item.doc_id}`}>{item.doc_filename ?? "Open document"}</Link>
                        </>
                      )}
                    </p>
                  </div>
                  {item.due_date && (
                    <div className="action-due">
                      <span className="due-rel num">{formatDate(item.due_date)}</span>
                    </div>
                  )}
                  {item.obligation_id !== null && (
                    <div className="row gap-1">
                      <button type="button" className="btn btn-sm" disabled={busy === item.obligation_id} onClick={() => void setObligation(item, "done")}>
                        <Check size={14} aria-hidden="true" /> Done
                      </button>
                      <button type="button" className="btn btn-sm btn-ghost" disabled={busy === item.obligation_id} onClick={() => void setObligation(item, "dismissed")}>
                        <X size={14} aria-hidden="true" /> Dismiss
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
