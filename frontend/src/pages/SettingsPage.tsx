import { CircleCheck, Lock, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { deleteAllData, errorMessage, getAudit } from "../api/client";
import { dataOf, useResource } from "../api/hooks";
import { DOC_TYPE_LABELS, type AuditEntry, type SystemStatus } from "../api/types";
import { Change } from "../components/AuditTimeline";
import { Badge } from "../components/Badge";
import { Card } from "../components/Card";
import { useSystemStatusState } from "../components/Layout";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, Loaded, Spinner } from "../components/States";
import { useToast } from "../components/Toast";
import { AUDIT_ACTION, MODEL_KIND } from "../lib/display";
import { formatDateTime } from "../lib/format";

export function SettingsPage() {
  const state = useSystemStatusState();
  const status = dataOf(state);
  return (
    <div className="page">
      <PageHeader title="Settings & Privacy" subtitle="What runs where, how it is configured, and everything VaultAI has done." />
      {status ? (
        <div className="stack-lg">
          <div className="grid-2">
            <Guarantees status={status} />
            <Configuration status={status} />
          </div>
          <AuditLog />
          <DangerZone />
        </div>
      ) : state.kind === "error" ? (
        <ErrorState error={state.error} />
      ) : (
        <div className="loading-block">
          <Spinner label="Waiting for the local service…" />
        </div>
      )}
    </div>
  );
}

function Guarantees({ status }: { status: SystemStatus }) {
  const rows: { what: string; where: string; ok: boolean }[] = [
    { what: "Document storage", where: `This device · ${status.data_dir}`, ok: true },
    { what: "Text extraction & OCR", where: status.ocr_available ? `This device · ${status.ocr_engine ?? "OCR"}` : "This device · OCR not installed", ok: true },
    {
      what: "AI model",
      where:
        status.model.kind === "mock"
          ? "None · deterministic rules only"
          : `Local endpoint · ${status.model.endpoint ?? "not set"}${status.model.reachable ? "" : " (unreachable)"}`,
      ok: true,
    },
    {
      what: "Network egress",
      where: status.egress_guard ? "Blocked in-process for non-local addresses" : "Not enforced (egress guard off)",
      ok: status.egress_guard,
    },
    {
      what: "Phone capture",
      where: status.phone_enabled ? "On · LAN-only listener, private addresses only" : "Off",
      ok: true,
    },
    { what: "Exports", where: "Written to this device; downloaded by you", ok: true },
  ];
  return (
    <Card title="Local-only guarantees" subtitle={status.local_only ? "All processing on this device" : "Check the items marked below"}>
      <ul className="guarantees">
        {rows.map((r) => (
          <li key={r.what} data-ok={r.ok}>
            {r.ok ? <CircleCheck size={16} aria-hidden="true" /> : <TriangleAlert size={16} aria-hidden="true" />}
            <span className="guarantee-what">{r.what}</span>
            <span className="guarantee-where">{r.where}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Configuration({ status }: { status: SystemStatus }) {
  const rows: { variable: string; value: string }[] = [
    { variable: "VAULTAI_MODEL_BACKEND", value: `${status.model.kind} (${MODEL_KIND[status.model.kind]})` },
    { variable: "VAULTAI_MODEL_ENDPOINT", value: status.model.endpoint ?? "—" },
    { variable: "VAULTAI_MODEL_NAME", value: status.model.name },
    { variable: "VAULTAI_MODEL_VISION", value: status.model.vision ? "true" : "false" },
    { variable: "VAULTAI_OCR_ENGINE", value: status.ocr_available ? (status.ocr_engine ?? "available") : "none available" },
    { variable: "VAULTAI_SCANNER_BACKEND", value: status.scanner_backends.length > 0 ? status.scanner_backends.join(", ") : "none" },
    { variable: "VAULTAI_PHONE_ENABLED", value: status.phone_enabled ? "true" : "false" },
    { variable: "VAULTAI_EGRESS_GUARD", value: status.egress_guard ? "true" : "false" },
    { variable: "VAULTAI_AUTO_APPROVE_TYPES", value: status.auto_approve_types.map((t) => DOC_TYPE_LABELS[t]).join(", ") || "none (every record is reviewed)" },
    { variable: "VAULTAI_DATA_DIR", value: status.data_dir },
  ];
  return (
    <Card title="Configuration" subtitle={<>Set in <code>.env</code> and applied on restart. Current values:</>}>
      <p className="muted small">
        Model status: {status.model.detail} · App {status.app_version} · schema {status.schema_version}
      </p>
      <div className="table-wrap">
        <table className="table config-table">
          <tbody>
            {rows.map((r) => (
              <tr key={r.variable}>
                <th scope="row" className="mono">
                  {r.variable}
                </th>
                <td>{r.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!status.phone_enabled && (
        <p className="hint">
          Phone capture: set <code>VAULTAI_PHONE_ENABLED=true</code> and restart. It listens on your LAN only and rejects public
          addresses.
        </p>
      )}
    </Card>
  );
}

function AuditLog() {
  const audit = useResource("audit", (signal) => getAudit({ limit: 200 }, signal));
  return (
    <Card id="audit" title="Audit log" subtitle="Newest first · the last 200 entries" action={<button type="button" className="btn btn-sm" onClick={audit.reload}>Refresh</button>}>
      <Loaded resource={audit} loadingLabel="Loading audit log…">
        {(entries) => <AuditTable entries={entries} />}
      </Loaded>
    </Card>
  );
}

function AuditTable({ entries }: { entries: readonly AuditEntry[] }) {
  if (entries.length === 0) return <EmptyState title="No audit entries yet" />;
  return (
    <div className="table-wrap audit-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Actor</th>
            <th scope="col">Action</th>
            <th scope="col">Document</th>
            <th scope="col">Change</th>
            <th scope="col">Note</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td className="num nowrap">{formatDateTime(e.at)}</td>
              <td>{e.actor}</td>
              <td>
                <Badge>{e.action}</Badge>
                <div className="cell-sub">{AUDIT_ACTION[e.action]}</div>
              </td>
              <td>{e.doc_id ? <Link to={`/documents/${e.doc_id}`}>{e.doc_id.slice(0, 8)}</Link> : <span className="muted">—</span>}</td>
              <td>
                {e.field_key && <div className="mono small">{e.field_key}</div>}
                <span className="small">
                  <Change before={e.before} after={e.after} />
                </span>
              </td>
              <td className="small">{e.note ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DangerZone() {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const navigate = useNavigate();
  const inputId = useId();

  const wipe = async () => {
    setBusy(true);
    try {
      await deleteAllData();
      toast.push("info", "All local documents, derived files, exports, and records were deleted.");
      setConfirm("");
      navigate("/");
    } catch (err) {
      toast.push("danger", errorMessage(err, "Delete failed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card danger-zone" aria-labelledby={`${inputId}-title`}>
      <header className="card-header">
        <div>
          <h2 id={`${inputId}-title`} className="card-title">
            <Lock size={16} aria-hidden="true" /> Delete all local data
          </h2>
          <p className="card-subtitle">
            Permanently removes every document, page image, capture, export, and record from this device. One audit entry
            recording the wipe is kept. This cannot be undone.
          </p>
        </div>
      </header>
      <div className="card-body danger-row">
        <div className="field">
          <label htmlFor={inputId}>
            Type <code>DELETE</code> to confirm
          </label>
          <input id={inputId} className="input" type="text" autoComplete="off" spellCheck={false} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        <button type="button" className="btn btn-danger" disabled={confirm !== "DELETE" || busy} onClick={() => void wipe()}>
          {busy ? "Deleting…" : "Delete all local data"}
        </button>
      </div>
    </section>
  );
}
