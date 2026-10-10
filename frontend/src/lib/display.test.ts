import { describe, expect, it } from "vitest";
import {
  ActionCategoryValues,
  AuditActionValues,
  ClauseRiskValues,
  DocStatusValues,
  DocTypeValues,
  FieldOriginValues,
  IssueCodeValues,
  SeverityValues,
} from "../api/types";
import { ACTION_CATEGORY, AUDIT_ACTION, CLAUSE_RISK, DOC_TYPE_ICON, FIELD_ORIGIN, ISSUE_LABELS, SEVERITY, STATUS } from "./display";

function labelsFor<V extends string>(values: readonly V[], label: (v: V) => string | undefined): string[] {
  return values.map((v) => label(v) ?? "");
}

describe("display tables", () => {
  it.each([
    ["DocStatus", labelsFor(DocStatusValues, (v) => STATUS[v]?.label)],
    ["IssueCode", labelsFor(IssueCodeValues, (v) => ISSUE_LABELS[v])],
    ["ActionCategory", labelsFor(ActionCategoryValues, (v) => ACTION_CATEGORY[v]?.label)],
    ["ClauseRisk", labelsFor(ClauseRiskValues, (v) => CLAUSE_RISK[v]?.label)],
    ["FieldOrigin", labelsFor(FieldOriginValues, (v) => FIELD_ORIGIN[v]?.label)],
    ["Severity", labelsFor(SeverityValues, (v) => SEVERITY[v]?.label)],
    ["AuditAction", labelsFor(AuditActionValues, (v) => AUDIT_ACTION[v])],
  ])("gives every %s value its own non-empty label", (_name, labels) => {
    expect(labels.filter((l) => l.trim() === "")).toEqual([]);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("has an icon for every action category and document type", () => {
    expect(ActionCategoryValues.filter((c) => !ACTION_CATEGORY[c]?.icon)).toEqual([]);
    expect(DocTypeValues.filter((t) => !DOC_TYPE_ICON[t])).toEqual([]);
  });
});
