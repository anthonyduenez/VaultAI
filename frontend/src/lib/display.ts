import {
  BadgeCheck,
  Ban,
  CalendarClock,
  CircleAlert,
  CircleDollarSign,
  CircleX,
  ClipboardCheck,
  Copy,
  FileQuestion,
  FileSignature,
  type LucideIcon,
  Scale,
  ShieldAlert,
  TriangleAlert,
  Info,
  ListChecks,
  CircleCheck,
  ClipboardList,
  FileCheck,
  FilePen,
  FileText,
  Landmark,
  Mail,
  Package,
  Receipt,
  ScrollText,
} from "lucide-react";
import type {
  ActionCategory,
  AuditAction,
  ClauseKind,
  ClauseRisk,
  DocStatus,
  DocType,
  FieldOrigin,
  IssueCode,
  ModelKind,
  ObligationKind,
  ObligationStatus,
  PreflightStatus,
  ReviewAction,
  RiskLevel,
  Route,
  Severity,
  Source,
  TextMethod,
} from "../api/types";

/** Five tones shared by every badge, banner, and meter. Accent = local / verified. */
export type Tone = "neutral" | "info" | "accent" | "warning" | "danger";

interface Labeled {
  label: string;
  tone: Tone;
}

export const STATUS: Record<DocStatus, Labeled & { hint: string }> = {
  imported: { label: "Imported", tone: "neutral", hint: "Stored locally, waiting to be processed." },
  processing: { label: "Processing", tone: "info", hint: "Being read, classified, and extracted on this device." },
  extracted: { label: "Extracted", tone: "info", hint: "Fields extracted; validation pending." },
  validated: { label: "Validated", tone: "info", hint: "Checks complete; routing to review." },
  ready: { label: "Ready", tone: "info", hint: "Passed every check. Waiting for approval." },
  needs_review: { label: "Needs review", tone: "warning", hint: "A person must look at flagged values before approval." },
  approved: { label: "Approved", tone: "accent", hint: "Verified record. Eligible for export." },
  rejected: { label: "Rejected", tone: "neutral", hint: "Excluded from exports." },
  exported: { label: "Exported", tone: "accent", hint: "Approved and included in at least one export." },
  failed: { label: "Failed", tone: "danger", hint: "Processing stopped. Reprocess or delete." },
};

export const REVIEW_ACTION: Record<ReviewAction, { label: string; past: string; key: string | null }> = {
  approve: { label: "Approve", past: "Approved", key: "A" },
  reject: { label: "Reject", past: "Rejected", key: "R" },
  defer: { label: "Defer", past: "Deferred", key: "D" },
  reopen: { label: "Reopen", past: "Reopened", key: null },
};

export const SEVERITY: Record<Severity, Labeled & { icon: LucideIcon }> = {
  error: { label: "Error", tone: "danger", icon: CircleAlert },
  warning: { label: "Warning", tone: "warning", icon: TriangleAlert },
  info: { label: "Info", tone: "info", icon: Info },
};

export const ISSUE_LABELS: Record<IssueCode, string> = {
  missing_required: "Missing required value",
  low_ocr_confidence: "Low OCR confidence",
  low_extraction_confidence: "Low extraction confidence",
  unreadable: "Unreadable page",
  invalid_date: "Invalid date",
  date_order: "Dates out of order",
  conflicting_values: "Conflicting values",
  amount_mismatch: "Amounts do not add up",
  written_amount_mismatch: "Written amount differs",
  tax_inconsistent: "Tax inconsistent",
  currency_mismatch: "Currency mismatch",
  unusual_amount: "Unusual amount",
  invalid_identifier: "Invalid identifier",
  duplicate_document: "Possible duplicate",
  missing_signature: "Missing signature",
  auto_renewal: "Auto-renewal",
  risky_clause: "Risky clause",
  ambiguous_clause: "Ambiguous clause",
  unusual_obligation: "Unusual obligation",
  prompt_injection: "Prompt injection attempt",
  hidden_text: "Hidden text",
  ungrounded_value: "Value not found in document",
  model_output_rejected: "Model output rejected",
  model_unavailable: "Local model unavailable",
  legal_review_required: "Professional review suggested",
};

export const ACTION_CATEGORY: Record<ActionCategory, { label: string; icon: LucideIcon }> = {
  needs_review: { label: "Needs review", icon: ClipboardCheck },
  ready: { label: "Ready to approve", icon: ListChecks },
  missing_info: { label: "Missing information", icon: FileQuestion },
  renewal: { label: "Renewals & expirations", icon: CalendarClock },
  payment_due: { label: "Payments due", icon: CircleDollarSign },
  obligation: { label: "Obligations", icon: ListChecks },
  amount_mismatch: { label: "Amount mismatches", icon: Scale },
  duplicate: { label: "Duplicates", icon: Copy },
  unsigned: { label: "Unsigned", icon: FileSignature },
  risky_clause: { label: "Risky clauses", icon: TriangleAlert },
  injection: { label: "Injection attempts", icon: ShieldAlert },
  processing_failure: { label: "Processing failures", icon: CircleX },
  recently_approved: { label: "Recently approved", icon: BadgeCheck },
};

export const CLAUSE_RISK: Record<ClauseRisk, Labeled> = {
  none: { label: "No flag", tone: "neutral" },
  attention: { label: "Attention", tone: "warning" },
  professional_review: { label: "Professional review", tone: "danger" },
};

export const RISK_LEVEL: Record<RiskLevel, Labeled> = {
  none: { label: "No risk flags", tone: "neutral" },
  low: { label: "Low risk", tone: "info" },
  medium: { label: "Medium risk", tone: "warning" },
  high: { label: "High risk", tone: "danger" },
};

export const FIELD_ORIGIN: Record<FieldOrigin, Labeled & { description: string }> = {
  rules: { label: "AI · rules", tone: "info", description: "Read by deterministic local rules." },
  model: { label: "AI · model", tone: "info", description: "Proposed by the local model and grounded in the page text." },
  human: { label: "Human-edited", tone: "accent", description: "Entered or corrected by a reviewer." },
};

export const CLAUSE_KIND: Record<ClauseKind, string> = {
  term: "Term",
  renewal: "Renewal",
  termination: "Termination",
  payment: "Payment",
  deliverables: "Deliverables",
  confidentiality: "Confidentiality",
  intellectual_property: "Intellectual property",
  liability: "Liability",
  indemnity: "Indemnity",
  warranty: "Warranty",
  dispute_resolution: "Dispute resolution",
  governing_law: "Governing law",
  non_compete: "Non-compete",
  data_protection: "Data protection",
  insurance: "Insurance",
  service_level: "Service level",
  signatures: "Signatures",
  other: "Other",
};

export const OBLIGATION_KIND: Record<ObligationKind, string> = {
  obligation: "Obligation",
  payment: "Payment",
  deliverable: "Deliverable",
  renewal: "Renewal",
  expiration: "Expiration",
  notice: "Notice",
  signature: "Signature",
};

export const OBLIGATION_STATUS: Record<ObligationStatus, Labeled> = {
  open: { label: "Open", tone: "warning" },
  done: { label: "Done", tone: "accent" },
  dismissed: { label: "Dismissed", tone: "neutral" },
};

export const SOURCE: Record<Source, string> = {
  upload: "Upload",
  folder: "Folder",
  sample: "Sample",
  scanner: "Scanner",
  phone: "Phone",
};

export const PREFLIGHT: Record<PreflightStatus, Labeled & { icon: LucideIcon }> = {
  accepted: { label: "Accepted", tone: "accent", icon: CircleCheck },
  rejected: { label: "Rejected · not stored", tone: "danger", icon: Ban },
  duplicate: { label: "Duplicate · not stored", tone: "warning", icon: Copy },
};

export const AUDIT_ACTION: Record<AuditAction, string> = {
  import: "imported a document",
  preflight_reject: "had a file rejected at preflight",
  process: "processed a document",
  auto_approve: "auto-approved a document by policy",
  approve: "approved a document",
  reject: "rejected a document",
  defer: "deferred a document",
  reopen: "reopened a document",
  edit_field: "corrected a field",
  reprocess: "requested reprocessing",
  delete: "deleted a document",
  delete_all: "deleted all local data",
  export: "created an export",
  obligation_update: "updated an obligation",
  capture: "captured pages",
};

export const ROUTE: Record<Route, string> = {
  fast: "Fast path (rules)",
  model: "Local model",
  vision: "Vision fallback",
};

export const MODEL_KIND: Record<ModelKind, string> = {
  mock: "Mock adapter",
  openai_compat: "OpenAI-compatible local endpoint",
};

export const TEXT_METHOD: Record<TextMethod, string> = {
  pdf_text: "Embedded PDF text",
  ocr: "OCR",
  vision: "Vision model",
  plain_text: "Plain text",
  none: "No text",
};

export function confidenceTone(confidence: number): Tone {
  if (confidence >= 0.9) return "accent";
  if (confidence >= 0.75) return "warning";
  return "danger";
}

export const DOC_TYPE_ICON: Record<DocType, LucideIcon> = {
  check: Landmark,
  receipt: Receipt,
  invoice: FileText,
  contract: ScrollText,
  agreement_letter: FileSignature,
  purchase_order: Package,
  statement_of_work: ListChecks,
  proposal: FilePen,
  policy: FileCheck,
  admin_form: ClipboardList,
  correspondence: Mail,
  other: FileQuestion,
};
