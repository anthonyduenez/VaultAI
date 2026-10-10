import { LocateFixed, Scale, ScrollText, Sparkles } from "lucide-react";
import { ClauseKindValues, ClauseRiskValues, type Clause, type ClauseKind, type Summary } from "../api/types";
import { CLAUSE_KIND, CLAUSE_RISK } from "../lib/display";
import { formatDateTime, shortModelName } from "../lib/format";
import { Badge } from "./Badge";
import type { EvidenceTarget } from "./EvidenceViewer";
import { EmptyState } from "./States";

export function LegalDisclaimer() {
  return (
    <div className="banner" data-tone="warning" role="note">
      <Scale size={16} aria-hidden="true" />
      <p>
        <strong>Document assistance and issue spotting only — not legal advice.</strong> Review the original wording before
        relying on it.
      </p>
    </div>
  );
}

interface SelectableProps {
  selectedId: string | null;
  onSelect: (target: EvidenceTarget) => void;
}

export function SummaryPanel({ summary, selectedId, onSelect }: SelectableProps & { summary: Summary | null }) {
  if (summary === null) {
    return (
      <EmptyState icon={Sparkles} title="No summary yet">
        A summary is written after extraction finishes.
      </EmptyState>
    );
  }
  const isTemplate = summary.generator === "template";
  return (
    <section className="ai-block" aria-labelledby="summary-heading">
      <header className="ai-block-head">
        <Sparkles size={15} aria-hidden="true" />
        <h3 id="summary-heading">{isTemplate ? "Template summary — mock adapter" : "AI-generated summary"}</h3>
        <span className="muted">
          {!isTemplate && summary.model_name && `${shortModelName(summary.model_name)} · `}
          {formatDateTime(summary.generated_at)}
        </span>
      </header>
      <p className="ai-block-note">Each statement links to the text it came from. Verify against the original before acting on it.</p>
      <ol className="claims">
        {summary.claims.map((claim, i) => (
          <li key={i} className="claim">
            <span>{claim.text}</span>
            <span className="chips">
              {claim.evidence.map((evidence, j) => {
                const targetId = `claim:${i}:${j}`;
                return (
                  <button
                    key={j}
                    type="button"
                    className="chip"
                    aria-pressed={selectedId === targetId}
                    onClick={() => onSelect({ id: targetId, label: `Summary point ${i + 1}`, evidence })}
                  >
                    <LocateFixed size={12} aria-hidden="true" />
                    {evidence.page !== null ? `p.${evidence.page}` : "source"}
                  </button>
                );
              })}
              {claim.evidence.length === 0 && <span className="chip chip-warn">no source cited</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ClauseList({ clauses, selectedId, onSelect }: SelectableProps & { clauses: readonly Clause[] }) {
  if (clauses.length === 0) {
    return (
      <EmptyState icon={ScrollText} title="No clauses identified">
        Clause analysis runs on contracts, agreement letters, statements of work, and policies.
      </EmptyState>
    );
  }
  const groups = ClauseKindValues.map((kind): [ClauseKind, Clause[]] => [kind, clauses.filter((c) => c.kind === kind)]).filter(
    ([, items]) => items.length > 0,
  );
  const attention = clauses.filter((c) => c.risk === "attention").length;
  const professional = clauses.filter((c) => c.risk === "professional_review").length;

  return (
    <div className="stack">
      <p className="muted">
        {clauses.length} clauses · {attention} flagged for attention · {professional} suggest professional review
      </p>
      {groups.map(([kind, items]) => (
        <section key={kind} className="clause-group" aria-label={CLAUSE_KIND[kind]}>
          <h3 className="clause-group-title">{CLAUSE_KIND[kind]}</h3>
          {[...items]
            .sort((a, b) => ClauseRiskValues.indexOf(b.risk) - ClauseRiskValues.indexOf(a.risk))
            .map((clause) => {
              const targetId = `clause:${clause.id}`;
              const evidence = clause.evidence;
              const risk = CLAUSE_RISK[clause.risk];
              return (
                <article key={clause.id} className="clause" data-tone={risk.tone} data-selected={selectedId === targetId || undefined}>
                  <header className="clause-head">
                    <strong>{clause.heading}</strong>
                    {clause.risk !== "none" && <Badge tone={risk.tone}>{risk.label}</Badge>}
                    {evidence && (
                      <button type="button" className="link-button" onClick={() => onSelect({ id: targetId, label: clause.heading, evidence })}>
                        <LocateFixed size={14} aria-hidden="true" /> Show in document
                      </button>
                    )}
                  </header>
                  {clause.risk_reason && <p className="clause-risk">Flag: {clause.risk_reason}</p>}
                  <figure className="quote">
                    <figcaption>Original wording</figcaption>
                    <blockquote>{clause.text}</blockquote>
                  </figure>
                  {clause.interpretation && (
                    <div className="ai-inline">
                      <span className="ai-inline-label">
                        <Sparkles size={12} aria-hidden="true" /> AI interpretation · not legal advice
                      </span>
                      <p>{clause.interpretation}</p>
                    </div>
                  )}
                </article>
              );
            })}
        </section>
      ))}
    </div>
  );
}
