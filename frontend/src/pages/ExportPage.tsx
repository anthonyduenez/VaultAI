import { Download, FileSpreadsheet, Lock, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { createExport, errorMessage, exportDownloadUrl, listExports, previewExport } from "../api/client";
import { useDebounced, useResource } from "../api/hooks";
import {
  DOC_TYPE_LABELS,
  DocTypeValues,
  ExportFormatValues,
  type ExportFilters,
  type ExportFormat,
  type ExportJob,
  type ExportPreview,
} from "../api/types";
import { Badge, DocTypeBadge } from "../components/Badge";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, Loaded, Spinner } from "../components/States";
import { useToast } from "../components/Toast";
import { formatDate, formatDateTime, formatMoney, formatMs, shortHash } from "../lib/format";

type ExportForm = Required<ExportFilters>;

const INITIAL: ExportForm = {
  doc_types: [],
  date_from: null,
  date_to: null,
  include_needs_review: false,
  include_evidence: true,
  format: "xlsx",
};

const FORMAT_LABEL: Record<ExportFormat, { label: string; hint: string }> = {
  xlsx: { label: "Excel workbook (.xlsx)", hint: "Approved records plus metadata, warnings, obligations, and optional evidence sheets." },
  csv: { label: "CSV (.csv)", hint: "The Approved Records sheet only. Formula-like cells are neutralized." },
};

export function ExportPage() {
  const [form, setForm] = useState<ExportForm>(INITIAL);
  const [creating, setCreating] = useState(false);
  const [lastJob, setLastJob] = useState<ExportJob | null>(null);
  const debounced = useDebounced(form, 400);
  const preview = useResource(`export-preview:${JSON.stringify(debounced)}`, (signal) => previewExport(debounced, signal));
  const history = useResource("exports", listExports);
  const toast = useToast();
  const ids = useId();

  const set = <K extends keyof ExportForm>(key: K, value: ExportForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const toggleType = (type: (typeof DocTypeValues)[number]) =>
    set("doc_types", form.doc_types.includes(type) ? form.doc_types.filter((t) => t !== type) : [...form.doc_types, type]);

  const create = async () => {
    setCreating(true);
    try {
      const job = await createExport(form);
      setLastJob(job);
      toast.push("accent", `Export ready: ${job.record_count} approved records.`);
      history.reload();
      preview.reload();
    } catch (err) {
      toast.push("danger", errorMessage(err, "Export failed."));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="page">
      <PageHeader title="Export" subtitle="Write approved records to an auditable spreadsheet on this device." />
      <div className="export-layout">
        <form
          className="card export-form"
          aria-label="Export options"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <fieldset className="field">
            <legend>Categories</legend>
            <p className="hint">None selected exports every category.</p>
            <div className="chip-row">
              {DocTypeValues.map((t) => (
                <label key={t} className="chip chip-check">
                  <input type="checkbox" checked={form.doc_types.includes(t)} onChange={() => toggleType(t)} />
                  {DOC_TYPE_LABELS[t]}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="grid-2 tight">
            <div className="field">
              <label htmlFor={`${ids}-from`}>Document date from</label>
              <input id={`${ids}-from`} className="input" type="date" value={form.date_from ?? ""} onChange={(e) => set("date_from", e.target.value || null)} />
            </div>
            <div className="field">
              <label htmlFor={`${ids}-to`}>Document date to</label>
              <input id={`${ids}-to`} className="input" type="date" value={form.date_to ?? ""} onChange={(e) => set("date_to", e.target.value || null)} />
            </div>
          </div>

          <fieldset className="field">
            <legend>Contents</legend>
            <label className="checkbox checkbox-locked">
              <input type="checkbox" checked disabled aria-describedby={`${ids}-locked`} />
              <span>
                Approved records only <Lock size={12} aria-hidden="true" />
              </span>
            </label>
            <p id={`${ids}-locked`} className="hint">
              Always on. The main sheet holds only records a person approved or that met the auto-approval policy, so
              unverified data never reaches your books.
            </p>
            <label className="checkbox">
              <input type="checkbox" checked={form.include_needs_review} onChange={(e) => set("include_needs_review", e.target.checked)} />
              Include a separate “Needs Review” sheet
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={form.include_evidence} onChange={(e) => set("include_evidence", e.target.checked)} />
              Include source evidence sheet (page and text for each value)
            </label>
          </fieldset>

          <fieldset className="field">
            <legend>Format</legend>
            {ExportFormatValues.map((f) => (
              <label key={f} className="radio">
                <input type="radio" name="format" value={f} checked={form.format === f} onChange={() => set("format", f)} />
                <span>
                  {FORMAT_LABEL[f].label}
                  <span className="hint">{FORMAT_LABEL[f].hint}</span>
                </span>
              </label>
            ))}
          </fieldset>

          <button type="submit" className="btn btn-primary" disabled={creating}>
            <FileSpreadsheet size={15} aria-hidden="true" /> {creating ? "Creating…" : "Create export"}
          </button>

          {lastJob && (
            <div className="banner" data-tone="accent" role="status">
              <Download size={16} aria-hidden="true" />
              <p>
                <strong>{lastJob.filename}</strong> · {lastJob.record_count} records · SHA-256 <span className="mono">{shortHash(lastJob.sha256)}</span>
                <br />
                <a href={exportDownloadUrl(lastJob.id)} download>
                  Download export
                </a>
              </p>
            </div>
          )}
        </form>

        <Card title="Preview" subtitle="Updates as you change the options" action={preview.state.kind !== "loading" && debounced !== form ? <Spinner size={14} /> : null}>
          <Loaded resource={preview} loadingLabel="Building preview…">
            {(p) => <PreviewBody preview={p} />}
          </Loaded>
        </Card>
      </div>

      <Card title="Export history" subtitle="Every export is hashed and recorded in the audit log">
        <Loaded resource={history} loadingLabel="Loading history…">
          {(jobs) => <HistoryTable jobs={jobs} />}
        </Loaded>
      </Card>
    </div>
  );
}

function PreviewBody({ preview: p }: { preview: ExportPreview }) {
  return (
    <div className="stack">
      <dl className="metric-grid">
        <div className="metric">
          <dt>Approved records</dt>
          <dd className="num">{p.record_count}</dd>
        </div>
        <div className="metric">
          <dt>Waiting for review</dt>
          <dd className="num">{p.needs_review_count}</dd>
        </div>
      </dl>
      {p.by_type.length > 0 && (
        <div className="row wrap gap-1">
          {p.by_type.map((t) => (
            <span key={t.doc_type} className="row gap-1">
              <DocTypeBadge type={t.doc_type} /> <span className="num">{t.count}</span>
            </span>
          ))}
        </div>
      )}
      <div>
        <p className="label-strong">Sheets</p>
        <ol className="sheet-list">
          {p.sheets.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
      </div>
      {p.warnings.map((w) => (
        <div key={w} className="banner" data-tone="warning" role="note">
          <TriangleAlert size={16} aria-hidden="true" />
          <p>{w}</p>
        </div>
      ))}
      {p.rows.length === 0 ? (
        <EmptyState title="No approved records match">
          Approve documents in the <Link to="/review">review queue</Link> to include them.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Counterparty</th>
                <th scope="col">Date</th>
                <th scope="col">ID</th>
                <th scope="col" className="num">Total</th>
                <th scope="col">Approval</th>
                <th scope="col" className="num">Human edits</th>
              </tr>
            </thead>
            <tbody>
              {p.rows.map((row) => (
                <tr key={row.doc_id}>
                  <td className="cell-file">
                    <Link to={`/documents/${row.doc_id}`}>{row.filename}</Link>
                    <div className="cell-sub">{row.doc_type ? DOC_TYPE_LABELS[row.doc_type] : "Unclassified"}</div>
                  </td>
                  <td>{row.counterparty ?? <span className="muted">—</span>}</td>
                  <td className="num">{formatDate(row.doc_date)}</td>
                  <td className="mono">{row.primary_id ?? "—"}</td>
                  <td className="num">{formatMoney(row.total_amount, row.currency)}</td>
                  <td>
                    {row.approval_kind === "human" ? (
                      <Badge tone="accent">Human · {row.approved_by ?? "reviewer"}</Badge>
                    ) : row.approval_kind === "policy" ? (
                      <Badge tone="info">Auto-approval policy</Badge>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="num">{row.human_edits}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function HistoryTable({ jobs }: { jobs: readonly ExportJob[] }) {
  if (jobs.length === 0) return <EmptyState icon={FileSpreadsheet} title="No exports yet" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Created</th>
            <th scope="col">File</th>
            <th scope="col" className="num">Records</th>
            <th scope="col">SHA-256</th>
            <th scope="col" className="num">Duration</th>
            <th scope="col">By</th>
            <th scope="col">
              <span className="sr-only">Download</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td className="num">{formatDateTime(job.created_at)}</td>
              <td className="cell-file">
                {job.filename} <Badge>{job.format.toUpperCase()}</Badge>
              </td>
              <td className="num">{job.record_count}</td>
              <td className="mono" title={job.sha256}>
                {shortHash(job.sha256)}
              </td>
              <td className="num">{formatMs(job.duration_ms)}</td>
              <td>{job.actor}</td>
              <td className="cell-actions">
                <a className="btn btn-sm" href={exportDownloadUrl(job.id)} download>
                  <Download size={14} aria-hidden="true" /> Download
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
