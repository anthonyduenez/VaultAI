import { LocateFixed, Pencil } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { editField, errorMessage } from "../api/client";
import type { DocumentDetail, ExtractedField, ValueType } from "../api/types";
import { formatDate, formatDateTime, formatMoney, optionalText } from "../lib/format";
import { ConfidenceMeter, OriginBadge } from "./Badge";
import type { EvidenceTarget } from "./EvidenceViewer";
import { useToast } from "./Toast";

interface ValueSpec {
  input: "text" | "textarea" | "date" | "money" | "bool" | "enum";
  display: (value: string, field: ExtractedField) => string;
}

const VALUE_SPEC: Record<ValueType, ValueSpec> = {
  text: { input: "text", display: (v) => v },
  long_text: { input: "textarea", display: (v) => v },
  identifier: { input: "text", display: (v) => v },
  enum: { input: "enum", display: (v) => v.replaceAll("_", " ") },
  money: { input: "money", display: (v, field) => formatMoney(v, field.currency) },
  date: { input: "date", display: (v) => formatDate(v) },
  bool: { input: "bool", display: (v) => (v === "true" ? "Yes" : v === "false" ? "No" : v) },
};

function displayFieldValue(field: ExtractedField, value: string | null): string | null {
  return value === null ? null : VALUE_SPEC[field.value_type].display(value, field);
}

interface FieldTableProps {
  docId: string;
  fields: readonly ExtractedField[];
  editable: boolean;
  selectedId: string | null;
  onSelect: (target: EvidenceTarget) => void;
  onSaved: (detail: DocumentDetail) => void;
}

export function FieldTable({ docId, fields, editable, selectedId, onSelect, onSaved }: FieldTableProps) {
  const [editing, setEditing] = useState<string | null>(null);

  return (
    <div className="table-wrap">
      <table className="table field-table">
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Value</th>
            <th scope="col">Source · confidence</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => {
            const targetId = `field:${field.key}`;
            const evidence = field.evidence;
            const isEditing = editing === field.key;
            return (
              <tr key={field.key} data-selected={selectedId === targetId || undefined}>
                <th scope="row" className="field-label">
                  {field.label}
                  {field.required && (
                    <span className="required" title="Required for this document type">
                      *<span className="sr-only"> (required)</span>
                    </span>
                  )}
                </th>
                <td className="field-value">
                  {isEditing ? (
                    <FieldEditor
                      docId={docId}
                      field={field}
                      onCancel={() => setEditing(null)}
                      onSaved={(detail) => {
                        setEditing(null);
                        onSaved(detail);
                      }}
                    />
                  ) : (
                    <FieldValue field={field} />
                  )}
                </td>
                <td className="field-source">
                  <OriginBadge origin={field.origin} />
                  {field.origin === "human" ? (
                    field.edited_by && (
                      <div className="cell-sub" title={formatDateTime(field.edited_at)}>
                        by {field.edited_by}
                      </div>
                    )
                  ) : (
                    <ConfidenceMeter value={field.confidence} />
                  )}
                </td>
                <td className="cell-actions">
                  <button
                    type="button"
                    className="btn btn-sm btn-icon-only"
                    disabled={evidence === null}
                    aria-label={`Show ${field.label} in the document`}
                    title={evidence === null ? "No location recorded" : "Show in document"}
                    onClick={() => evidence && onSelect({ id: targetId, label: field.label, evidence })}
                  >
                    <LocateFixed size={15} aria-hidden="true" />
                  </button>
                  {editable && !isEditing && (
                    <button type="button" className="btn btn-sm btn-icon-only" aria-label={`Edit ${field.label}`} title="Correct this value" onClick={() => setEditing(field.key)}>
                      <Pencil size={15} aria-hidden="true" />
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

const LONG_TEXT_PREVIEW = 140;

function FieldValue({ field }: { field: ExtractedField }) {
  const [expanded, setExpanded] = useState(false);
  const shown = displayFieldValue(field, field.value);
  const aiShown = displayFieldValue(field, field.ai_value);
  const clampable = field.value_type === "long_text" && shown !== null && shown.length > LONG_TEXT_PREVIEW;
  return (
    <>
      {shown === null ? (
        <span className={field.required ? "value-missing" : "muted"}>{field.required ? "Missing" : "Not found"}</span>
      ) : field.value_type === "long_text" ? (
        <span className="value-long" data-clamped={(clampable && !expanded) || undefined}>
          {shown}
        </span>
      ) : (
        <span className={field.value_type === "money" ? "num value" : "value"}>{shown}</span>
      )}
      {clampable && (
        <button type="button" className="link-button" aria-expanded={expanded} onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Show less" : "Show all"}
        </button>
      )}
      {field.origin === "human" && field.ai_value !== field.value && (
        <div className="ai-original" title={`Originally read by ${field.ai_origin === "model" ? "the local model" : "local rules"}`}>
          AI read: {aiShown === null ? <em>nothing</em> : <s>{aiShown}</s>}
        </div>
      )}
    </>
  );
}

// A date input can only hold YYYY-MM-DD; any other stored text would be invisible in it.
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function initialInput(field: ExtractedField): string {
  const value = field.value ?? "";
  if (field.value_type === "date") return ISO_DATE.test(value) ? value : "";
  return value;
}

interface FieldEditorProps {
  docId: string;
  field: ExtractedField;
  onCancel: () => void;
  onSaved: (detail: DocumentDetail) => void;
}

function FieldEditor({ docId, field, onCancel, onSaved }: FieldEditorProps) {
  const spec = VALUE_SPEC[field.value_type];
  const [raw, setRaw] = useState(() => initialInput(field));
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const inputId = useId();
  const errorId = `${inputId}-error`;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const detail = await editField(docId, field.key, { value: optionalText(raw), note: optionalText(note) });
      toast.push("accent", `${field.label} updated. The AI value is kept in the audit trail.`);
      onSaved(detail);
    } catch (err) {
      setError(errorMessage(err, "Could not save this value."));
      setSaving(false);
    }
  };

  const common = {
    id: inputId,
    value: raw,
    "aria-invalid": error !== null,
    "aria-describedby": error ? errorId : undefined,
    autoFocus: true,
  };

  return (
    <form className="field-editor" onSubmit={submit} onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <label htmlFor={inputId} className="sr-only">
        New value for {field.label}
      </label>
      {spec.input === "textarea" && <textarea {...common} className="input" rows={3} onChange={(e) => setRaw(e.target.value)} />}
      {spec.input === "text" && <input {...common} className="input" type="text" onChange={(e) => setRaw(e.target.value)} />}
      {spec.input === "date" && <input {...common} className="input" type="date" onChange={(e) => setRaw(e.target.value)} />}
      {spec.input === "money" && (
        <div className="input-affix">
          <span aria-hidden="true">{field.currency ?? "Amount"}</span>
          <input {...common} className="input num" type="text" inputMode="decimal" onChange={(e) => setRaw(e.target.value)} />
        </div>
      )}
      {spec.input === "enum" && (
        <select {...common} className="input" onChange={(e) => setRaw(e.target.value)}>
          <option value="">Unknown</option>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      )}
      {spec.input === "bool" && (
        <select {...common} className="input" onChange={(e) => setRaw(e.target.value)}>
          <option value="">Unknown</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      )}
      <input className="input input-sm" type="text" placeholder="Reason for the correction (optional)" aria-label="Reason for the correction" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
      {error && (
        <p id={errorId} className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="row gap-1">
        <button type="submit" className="btn btn-sm btn-primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
