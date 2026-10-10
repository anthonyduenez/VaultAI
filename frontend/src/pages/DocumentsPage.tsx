import { FileSearch, LayoutGrid, List, Search, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { DOCUMENT_SORTS, type DocumentQuery, type DocumentSort, listDocuments, type SortOrder } from "../api/client";
import { useDebounced, useNow, useResource } from "../api/hooks";
import {
  DOC_TYPE_LABELS,
  DocStatusValues,
  DocTypeValues,
  type DocStatus,
  type DocType,
  type DocumentList,
  type DocumentSummary,
} from "../api/types";
import { ConfidenceBadge, DocTypeBadge, StatusBadge } from "../components/Badge";
import { DueText } from "../components/ObligationList";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, Loaded } from "../components/States";
import { STATUS } from "../lib/display";
import { formatDate, formatMoney, isoDay } from "../lib/format";

const PAGE_SIZE = 50;

const SORT_LABEL: Record<DocumentSort, string> = {
  created_at: "Date imported",
  doc_date: "Document date",
  total_amount: "Amount",
  confidence: "Confidence",
  filename: "File name",
  next_deadline: "Next deadline",
};

const QUICK_STATUSES: readonly DocStatus[] = ["needs_review", "ready", "approved", "exported", "processing", "failed", "rejected"];

type View = "list" | "grid";

interface Filters {
  q: string;
  type: DocType | null;
  status: DocStatus | null;
  from: string;
  to: string;
  party: string;
  within: number | null;
  minConfidence: number | null;
  sort: DocumentSort;
  order: SortOrder;
  view: View;
  page: number;
}

const pick = <T extends string>(values: readonly T[], raw: string | null): T | null => values.find((v) => v === raw) ?? null;

function positiveInt(raw: string | null): number | null {
  const n = Number(raw);
  return raw !== null && Number.isInteger(n) && n > 0 ? n : null;
}

/** The URL is the source of truth for the explorer, so filters survive reloads and back/forward. */
function parseFilters(params: URLSearchParams): Filters {
  const conf = Number(params.get("minconf"));
  return {
    q: params.get("q") ?? "",
    type: pick(DocTypeValues, params.get("type")),
    status: pick(DocStatusValues, params.get("status")),
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
    party: params.get("party") ?? "",
    within: positiveInt(params.get("within")),
    minConfidence: params.has("minconf") && conf > 0 && conf <= 100 ? conf : null,
    sort: pick(DOCUMENT_SORTS, params.get("sort")) ?? "created_at",
    order: params.get("order") === "asc" ? "asc" : "desc",
    view: params.get("view") === "grid" ? "grid" : "list",
    page: positiveInt(params.get("page")) ?? 1,
  };
}

function writeFilters(f: Filters): URLSearchParams {
  const out = new URLSearchParams();
  const set = (key: string, value: string | number | null, fallback: string | number | null = null) => {
    if (value !== null && value !== "" && value !== fallback) out.set(key, String(value));
  };
  set("q", f.q);
  set("type", f.type);
  set("status", f.status);
  set("from", f.from);
  set("to", f.to);
  set("party", f.party);
  set("within", f.within);
  set("minconf", f.minConfidence);
  set("sort", f.sort, "created_at");
  set("order", f.order, "desc");
  set("view", f.view, "list");
  set("page", f.page, 1);
  return out;
}

function toQuery(f: Filters): DocumentQuery {
  return {
    q: f.q || undefined,
    doc_type: f.type ? [f.type] : undefined,
    status: f.status ? [f.status] : undefined,
    date_from: f.from || undefined,
    date_to: f.to || undefined,
    party: f.party || undefined,
    deadline_within_days: f.within ?? undefined,
    min_confidence: f.minConfidence === null ? undefined : f.minConfidence / 100,
    sort: f.sort,
    order: f.order,
    limit: PAGE_SIZE,
    offset: (f.page - 1) * PAGE_SIZE,
  };
}

export function DocumentsPage() {
  const [params, setParams] = useSearchParams();
  const filters = parseFilters(params);
  const [qInput, setQInput] = useState(filters.q);
  const debouncedQ = useDebounced(qInput, 300);
  const ids = useId();

  const update = (patch: Partial<Filters>) =>
    setParams(writeFilters({ ...filters, page: 1, ...patch }), { replace: true });

  useEffect(() => {
    // Only a settled search term is pushed to the URL; other filter changes must not re-trigger this.
    if (debouncedQ !== filters.q) update({ q: debouncedQ });
  }, [debouncedQ]);

  const queryKey = writeFilters({ ...filters, view: "list" }).toString();
  const docs = useResource(`documents?${queryKey}`, (signal) => listDocuments(toQuery(filters), signal), {
    poll: (list) => (list?.items.some((d) => d.status === "processing") ? 3000 : null),
  });

  const activeFilterCount = [filters.from, filters.to, filters.party, filters.within, filters.minConfidence].filter((v) => v !== "" && v !== null).length;

  return (
    <div className="page">
      <PageHeader title="Documents" subtitle="Every document stored on this device, with its verified record." />
      <div className="explorer">
        <Loaded resource={docs} loadingLabel="Loading documents…">
          {(list) => (
            <>
              <CategoryNav list={list} filters={filters} onPick={update} />
              <div className="explorer-main">
                <div className="filter-bar">
                  <div className="search">
                    <Search size={16} aria-hidden="true" />
                    <input
                      type="search"
                      className="input"
                      placeholder="Search names, parties, amounts, and text"
                      aria-label="Search documents"
                      value={qInput}
                      onChange={(e) => setQInput(e.target.value)}
                    />
                  </div>
                  <div className="field-inline">
                    <label htmlFor={`${ids}-sort`}>Sort</label>
                    <select id={`${ids}-sort`} className="input" value={filters.sort} onChange={(e) => update({ sort: pick(DOCUMENT_SORTS, e.target.value) ?? "created_at" })}>
                      {DOCUMENT_SORTS.map((s) => (
                        <option key={s} value={s}>
                          {SORT_LABEL[s]}
                        </option>
                      ))}
                    </select>
                    <button type="button" className="btn btn-sm" onClick={() => update({ order: filters.order === "asc" ? "desc" : "asc" })} aria-label={`Sort ${filters.order === "asc" ? "ascending" : "descending"}; switch order`}>
                      {filters.order === "asc" ? "↑ Asc" : "↓ Desc"}
                    </button>
                  </div>
                  <div className="segmented" role="group" aria-label="Layout">
                    <button type="button" aria-pressed={filters.view === "list"} onClick={() => update({ view: "list", page: filters.page })}>
                      <List size={14} aria-hidden="true" /> List
                    </button>
                    <button type="button" aria-pressed={filters.view === "grid"} onClick={() => update({ view: "grid", page: filters.page })}>
                      <LayoutGrid size={14} aria-hidden="true" /> Grid
                    </button>
                  </div>
                </div>
                <details className="filter-more" open={activeFilterCount > 0}>
                  <summary>Filters{activeFilterCount > 0 && ` · ${activeFilterCount} active`}</summary>
                  <div className="filter-grid">
                    <div className="field">
                      <label htmlFor={`${ids}-from`}>Date from</label>
                      <input id={`${ids}-from`} className="input" type="date" value={filters.from} onChange={(e) => update({ from: e.target.value })} />
                    </div>
                    <div className="field">
                      <label htmlFor={`${ids}-to`}>Date to</label>
                      <input id={`${ids}-to`} className="input" type="date" value={filters.to} onChange={(e) => update({ to: e.target.value })} />
                    </div>
                    <div className="field">
                      <label htmlFor={`${ids}-party`}>Party or vendor</label>
                      <input id={`${ids}-party`} key={filters.party} className="input" type="text" defaultValue={filters.party} onBlur={(e) => update({ party: e.target.value.trim() })} onKeyDown={(e) => e.key === "Enter" && update({ party: e.currentTarget.value.trim() })} />
                    </div>
                    <div className="field">
                      <label htmlFor={`${ids}-within`}>Deadline within</label>
                      <select id={`${ids}-within`} className="input" value={filters.within ?? ""} onChange={(e) => update({ within: positiveInt(e.target.value) })}>
                        <option value="">Any time</option>
                        {[7, 14, 30, 60, 90].map((d) => (
                          <option key={d} value={d}>
                            {d} days
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor={`${ids}-conf`}>Minimum confidence</label>
                      <select id={`${ids}-conf`} className="input" value={filters.minConfidence ?? ""} onChange={(e) => update({ minConfidence: positiveInt(e.target.value) })}>
                        <option value="">Any</option>
                        {[50, 75, 90, 95].map((c) => (
                          <option key={c} value={c}>
                            {c}%
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field field-end">
                      <button type="button" className="btn btn-ghost" disabled={activeFilterCount === 0} onClick={() => update({ from: "", to: "", party: "", within: null, minConfidence: null })}>
                        <X size={14} aria-hidden="true" /> Clear filters
                      </button>
                    </div>
                  </div>
                </details>
                <Results list={list} filters={filters} onPage={(page) => setParams(writeFilters({ ...filters, page }), { replace: true })} />
              </div>
            </>
          )}
        </Loaded>
      </div>
    </div>
  );
}

function CategoryNav({ list, filters, onPick }: { list: DocumentList; filters: Filters; onPick: (patch: Partial<Filters>) => void }) {
  const typeCount = new Map(list.by_type.map((t) => [t.doc_type, t.count]));
  const statusCount = new Map(list.by_status.map((s) => [s.status, s.count]));
  return (
    <nav className="category-nav" aria-label="Document categories">
      <p className="category-heading">Categories</p>
      <ul>
        <li>
          <button type="button" aria-pressed={filters.type === null} onClick={() => onPick({ type: null })}>
            All documents
          </button>
        </li>
        {DocTypeValues.map((t) => (
          <li key={t}>
            <button type="button" aria-pressed={filters.type === t} data-empty={!typeCount.get(t) || undefined} onClick={() => onPick({ type: t })}>
              {DOC_TYPE_LABELS[t]} <span className="num">{typeCount.get(t) ?? 0}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="category-heading">Status</p>
      <ul>
        <li>
          <button type="button" aria-pressed={filters.status === null} onClick={() => onPick({ status: null })}>
            Any status
          </button>
        </li>
        {QUICK_STATUSES.map((s) => (
          <li key={s}>
            <button type="button" aria-pressed={filters.status === s} onClick={() => onPick({ status: s })}>
              <span className="status-dot" data-tone={STATUS[s].tone} aria-hidden="true" />
              {STATUS[s].label} <span className="num">{statusCount.get(s) ?? 0}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function Results({ list, filters, onPage }: { list: DocumentList; filters: Filters; onPage: (page: number) => void }) {
  const navigate = useNavigate();
  const today = isoDay(useNow(60_000));
  if (list.items.length === 0) {
    return (
      <EmptyState icon={FileSearch} title="No documents match" action={<Link to="/import" className="btn btn-sm">Import documents</Link>}>
        Try a different category, clear the filters, or import more paperwork.
      </EmptyState>
    );
  }
  const first = (filters.page - 1) * PAGE_SIZE + 1;
  const last = first + list.items.length - 1;
  return (
    <>
      {filters.view === "list" ? (
        <div className="table-wrap">
          <table className="table doc-table">
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Counterparty</th>
                <th scope="col">Date</th>
                <th scope="col" className="num">Total</th>
                <th scope="col">Status</th>
                <th scope="col">Confidence</th>
                <th scope="col" className="num">Issues</th>
                <th scope="col">Next deadline</th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((doc) => (
                <tr key={doc.id} className="row-link" onClick={() => navigate(`/documents/${doc.id}`)}>
                  <td className="cell-file">
                    <DocTypeBadge type={doc.doc_type} />
                    <div>
                      <Link to={`/documents/${doc.id}`} onClick={(e) => e.stopPropagation()}>
                        {doc.title ?? doc.filename}
                      </Link>
                    </div>
                    {doc.title && <div className="cell-sub">{doc.filename}</div>}
                  </td>
                  <td>{doc.counterparty ?? <span className="muted">—</span>}</td>
                  <td className="num">{formatDate(doc.doc_date)}</td>
                  <td className="num">{formatMoney(doc.total_amount, doc.currency)}</td>
                  <td>
                    <StatusBadge status={doc.status} deferred={doc.deferred} />
                  </td>
                  <td>
                    <ConfidenceBadge value={doc.confidence} />
                  </td>
                  <td className="num">
                    <IssueCount doc={doc} />
                  </td>
                  <td>{doc.next_deadline ? <DueText due={doc.next_deadline} today={today} /> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ul className="doc-grid">
          {list.items.map((doc) => (
            <li key={doc.id}>
              <Link to={`/documents/${doc.id}`} className="doc-card">
                <div className="row between">
                  <DocTypeBadge type={doc.doc_type} />
                  <ConfidenceBadge value={doc.confidence} />
                </div>
                <p className="doc-card-title">{doc.title ?? doc.filename}</p>
                <p className="muted small">{doc.counterparty ?? "No counterparty"} · {formatDate(doc.doc_date)}</p>
                <p className="doc-card-amount num">{formatMoney(doc.total_amount, doc.currency)}</p>
                <div className="row between">
                  <StatusBadge status={doc.status} deferred={doc.deferred} />
                  <IssueCount doc={doc} />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <div className="pager">
        <span className="muted num">
          {first}–{last} of {list.total}
        </span>
        <button type="button" className="btn btn-sm" disabled={filters.page <= 1} onClick={() => onPage(filters.page - 1)}>
          Previous
        </button>
        <button type="button" className="btn btn-sm" disabled={last >= list.total} onClick={() => onPage(filters.page + 1)}>
          Next
        </button>
      </div>
    </>
  );
}

function IssueCount({ doc }: { doc: DocumentSummary }) {
  if (doc.issue_count === 0) return <span className="muted">0</span>;
  return (
    <span className="issue-count" data-tone={doc.error_count > 0 ? "danger" : "warning"} title={`${doc.error_count} errors`}>
      {doc.issue_count}
    </span>
  );
}
