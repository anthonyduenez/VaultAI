import { CircleCheck, LocateFixed } from "lucide-react";
import type { ValidationIssue } from "../api/types";
import { ISSUE_LABELS, SEVERITY } from "../lib/display";
import { SeverityBadge } from "./Badge";
import type { EvidenceTarget } from "./EvidenceViewer";
import { EmptyState } from "./States";

interface IssueListProps {
  issues: readonly ValidationIssue[];
  fieldLabels: ReadonlyMap<string, string>;
  selectedId: string | null;
  onSelect: (target: EvidenceTarget) => void;
}

export function IssueList({ issues, fieldLabels, selectedId, onSelect }: IssueListProps) {
  if (issues.length === 0) {
    return (
      <EmptyState icon={CircleCheck} title="No issues found">
        Every deterministic check passed for this document.
      </EmptyState>
    );
  }
  return (
    <ul className="issue-list">
      {issues.map((issue) => {
        const targetId = `issue:${issue.id}`;
        const evidence = issue.evidence;
        const fieldLabel = issue.field_key ? (fieldLabels.get(issue.field_key) ?? issue.field_key) : null;
        return (
          <li key={issue.id} className="issue" data-tone={SEVERITY[issue.severity].tone} data-selected={selectedId === targetId || undefined}>
            <div className="issue-head">
              <SeverityBadge severity={issue.severity} />
              <strong>{ISSUE_LABELS[issue.code]}</strong>
              <code className="issue-code">{issue.code}</code>
            </div>
            <p className="issue-message">{issue.message}</p>
            <div className="issue-foot">
              {fieldLabel && <span className="muted">Field: {fieldLabel}</span>}
              {evidence && (
                <button type="button" className="link-button" onClick={() => onSelect({ id: targetId, label: ISSUE_LABELS[issue.code], evidence })}>
                  <LocateFixed size={14} aria-hidden="true" /> Show in document
                </button>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
