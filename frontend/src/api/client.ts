import type {
  ActionCenter,
  ApiError,
  AssembleRequest,
  AuditEntry,
  BatchReviewRequest,
  BatchReviewResult,
  CaptureSession,
  Dashboard,
  DocStatus,
  DocType,
  DocumentDetail,
  DocumentList,
  DocumentSummary,
  ExportFilters,
  ExportJob,
  ExportPreview,
  FieldEdit,
  IntakeItem,
  Metrics,
  Obligation,
  ObligationUpdate,
  PhonePairing,
  ProcessResult,
  ReviewQueue,
  ReviewRequest,
  ScanRequest,
  ScannerDevice,
  SystemStatus,
} from "./types";

export const API_BASE = "/api";

export class ApiFailure extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiFailure";
    this.status = status;
    this.code = code;
  }
}

type Method = "GET" | "POST" | "PATCH" | "DELETE";
type QueryValue = string | number | boolean | null | undefined | readonly (string | number)[];
export type Query = Record<string, QueryValue>;

interface RequestOptions {
  method?: Method;
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
}

export function apiUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value) params.append(key, String(v));
    } else {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    "message" in value &&
    typeof value.code === "string" &&
    typeof value.message === "string"
  );
}

async function send({ method = "GET", query, body, signal }: RequestOptions, path: string): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-VaultAI"] = "1";
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(apiUrl(path, query), {
      method,
      headers,
      body: payload,
      signal,
      credentials: "same-origin",
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiFailure(0, "network", "Cannot reach the local VaultAI service.");
  }
  if (!response.ok) {
    const parsed: unknown = await response.json().catch(() => null);
    if (isApiError(parsed)) throw new ApiFailure(response.status, parsed.code, parsed.message);
    throw new ApiFailure(response.status, `http_${response.status}`, `Request failed (HTTP ${response.status}).`);
  }
  return response;
}

// Response shapes are generated from the backend's pydantic models, which also validate on the
// way out, so the JSON is trusted to match T here and nowhere else.
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(options, path);
  return (await response.json()) as T;
}

export async function requestVoid(path: string, options: RequestOptions = {}): Promise<void> {
  await send(options, path);
}

export function toFailure(err: unknown): ApiFailure {
  if (err instanceof ApiFailure) return err;
  return new ApiFailure(0, "client", err instanceof Error ? err.message : "Unexpected error.");
}

export const getSystemStatus = (signal?: AbortSignal) => request<SystemStatus>("/system/status", { signal });
export const getMetrics = (signal?: AbortSignal) => request<Metrics>("/system/metrics", { signal });
export const getDashboard = (signal?: AbortSignal) => request<Dashboard>("/dashboard", { signal });
export const getAudit = (args: { limit?: number; docId?: string }, signal?: AbortSignal) =>
  request<AuditEntry[]>("/audit", { query: { limit: args.limit ?? 100, doc_id: args.docId }, signal });
export const deleteAllData = () => requestVoid("/data", { method: "DELETE", body: { confirm: "DELETE" } });

export interface PickedFile {
  file: File;
  /** Relative path inside a picked or dropped folder, or the bare name. */
  path: string;
}

export type UploadSource = "upload" | "folder";

export function uploadFiles(files: readonly PickedFile[], source: UploadSource): Promise<IntakeItem[]> {
  const form = new FormData();
  for (const { file, path } of files) form.append("files", file, path);
  form.append("source", source);
  return request<IntakeItem[]>("/intake/upload", { method: "POST", body: form });
}

export const importSamples = () => request<IntakeItem[]>("/intake/samples", { method: "POST" });
export const listIntake = (signal?: AbortSignal) => request<IntakeItem[]>("/intake", { signal });
export const removeIntake = (id: number) => requestVoid(`/intake/${id}`, { method: "DELETE" });
export const processIntake = (intakeIds: number[] | null) =>
  request<ProcessResult>("/intake/process", { method: "POST", body: { intake_ids: intakeIds } });

export const DOCUMENT_SORTS = ["created_at", "doc_date", "total_amount", "confidence", "filename", "next_deadline"] as const;
export type DocumentSort = (typeof DOCUMENT_SORTS)[number];
export type SortOrder = "asc" | "desc";

export interface DocumentQuery {
  q?: string;
  doc_type?: DocType[];
  status?: DocStatus[];
  date_from?: string;
  date_to?: string;
  party?: string;
  deadline_within_days?: number;
  min_confidence?: number;
  sort?: DocumentSort;
  order?: SortOrder;
  limit?: number;
  offset?: number;
}

export const listDocuments = (query: DocumentQuery, signal?: AbortSignal) =>
  request<DocumentList>("/documents", { query: { ...query }, signal });
export const getDocument = (id: string, signal?: AbortSignal) =>
  request<DocumentDetail>(`/documents/${encodeURIComponent(id)}`, { signal });
export const pageImageUrl = (id: string, pageNo: number) =>
  apiUrl(`/documents/${encodeURIComponent(id)}/pages/${pageNo}/image`);
export const originalUrl = (id: string) => apiUrl(`/documents/${encodeURIComponent(id)}/original`);
export const editField = (id: string, key: string, edit: FieldEdit) =>
  request<DocumentDetail>(`/documents/${encodeURIComponent(id)}/fields/${encodeURIComponent(key)}`, {
    method: "PATCH",
    body: edit,
  });
export const reviewDocument = (id: string, review: ReviewRequest) =>
  request<DocumentDetail>(`/documents/${encodeURIComponent(id)}/review`, { method: "POST", body: review });
export const reprocessDocument = (id: string) =>
  request<DocumentSummary>(`/documents/${encodeURIComponent(id)}/reprocess`, { method: "POST" });
export const deleteDocument = (id: string) =>
  requestVoid(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
export const updateObligation = (id: number, update: ObligationUpdate) =>
  request<Obligation>(`/obligations/${id}`, { method: "PATCH", body: update });

export const getReviewQueue = (signal?: AbortSignal) => request<ReviewQueue>("/review/queue", { signal });
export const batchReview = (body: BatchReviewRequest) =>
  request<BatchReviewResult>("/review/batch", { method: "POST", body });
export const getActions = (signal?: AbortSignal) => request<ActionCenter>("/actions", { signal });

export const previewExport = (filters: ExportFilters, signal?: AbortSignal) =>
  request<ExportPreview>("/exports/preview", { method: "POST", body: filters, signal });
export const createExport = (filters: ExportFilters) =>
  request<ExportJob>("/exports", { method: "POST", body: filters });
export const listExports = (signal?: AbortSignal) => request<ExportJob[]>("/exports", { signal });
export const exportDownloadUrl = (id: string) => apiUrl(`/exports/${encodeURIComponent(id)}/download`);

const scanPath = (sessionId: string) => `/scanner/sessions/${encodeURIComponent(sessionId)}`;

export const listScannerDevices = (signal?: AbortSignal) => request<ScannerDevice[]>("/scanner/devices", { signal });
export const createScanSession = (deviceId: string) =>
  request<CaptureSession>("/scanner/sessions", { method: "POST", body: { device_id: deviceId } });
export const scanPage = (sessionId: string, req: ScanRequest) =>
  request<CaptureSession>(`${scanPath(sessionId)}/scan`, { method: "POST", body: req });
export const retakePage = (sessionId: string, pageId: number, req: ScanRequest) =>
  request<CaptureSession>(`${scanPath(sessionId)}/pages/${pageId}/retake`, { method: "POST", body: req });
export const deleteScanPage = (sessionId: string, pageId: number) =>
  request<CaptureSession>(`${scanPath(sessionId)}/pages/${pageId}`, { method: "DELETE" });
export const scanPageImageUrl = (sessionId: string, pageId: number) =>
  apiUrl(`${scanPath(sessionId)}/pages/${pageId}/image`);
export const assembleScan = (sessionId: string, req: AssembleRequest) =>
  request<IntakeItem>(`${scanPath(sessionId)}/assemble`, { method: "POST", body: req });
export const cancelScanSession = (sessionId: string) => requestVoid(scanPath(sessionId), { method: "DELETE" });

export const startPhonePairing = () => request<PhonePairing>("/phone/sessions", { method: "POST" });
export const getPhoneSession = (id: string, signal?: AbortSignal) =>
  request<CaptureSession>(`/phone/sessions/${encodeURIComponent(id)}`, { signal });
export const cancelPhoneSession = (id: string) =>
  requestVoid(`/phone/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
