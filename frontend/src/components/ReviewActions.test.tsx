import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DocumentSummary } from "../api/types";
import { makeDetail } from "../test/fixtures";
import { mockFetch } from "../test/fetch";
import { ReviewActions } from "./ReviewActions";

const doc: Pick<DocumentSummary, "id" | "filename" | "status" | "error_count"> = {
  id: "doc-1",
  filename: "invoice-0042.pdf",
  status: "needs_review",
  error_count: 2,
};

function renderActions(overrides: Partial<typeof doc> = {}, keyboard = false) {
  const onReviewed = vi.fn();
  render(<ReviewActions doc={{ ...doc, ...overrides }} onReviewed={onReviewed} onReprocessed={vi.fn()} onDeleted={vi.fn()} keyboard={keyboard} />);
  return onReviewed;
}

describe("ReviewActions", () => {
  it("blocks approval until a note explains the unresolved errors", async () => {
    const approved = makeDetail({ status: "approved", approval_kind: "human" });
    const calls = mockFetch(() => ({ body: approved }));
    const onReviewed = renderActions();
    const user = userEvent.setup();

    const approve = screen.getByRole("button", { name: /Approve/ });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/This record has 2 unresolved errors/)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Reviewer note/), "Checked against PO 4471");
    expect(approve).toBeEnabled();
    await user.click(approve);

    expect(calls).toEqual([
      { method: "POST", url: "/api/documents/doc-1/review", csrfHeader: "1", body: { action: "approve", note: "Checked against PO 4471" } },
    ]);
    expect(onReviewed).toHaveBeenCalledWith(approved, "approve");
  });

  it("allows approval without a note when there are no errors", () => {
    renderActions({ error_count: 0 });
    expect(screen.getByRole("button", { name: /Approve/ })).toBeEnabled();
    expect(screen.queryByText(/unresolved error/)).toBeNull();
  });

  it("rejects with the R shortcut", async () => {
    const calls = mockFetch(() => ({ body: makeDetail({ status: "rejected" }) }));
    renderActions({}, true);
    await userEvent.setup().keyboard("r");
    expect(calls.map((c) => c.body)).toEqual([{ action: "reject", note: null }]);
  });

  it("offers only reopen once a record is approved", () => {
    renderActions({ status: "approved", error_count: 0 });
    expect(screen.getByRole("button", { name: /Reopen/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
  });
});
