import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { makeDetail, makeField } from "../test/fixtures";
import { DocumentWorkspace } from "./DocumentWorkspace";

describe("DocumentWorkspace", () => {
  it.each([
    ["needs_review", true],
    ["rejected", true],
    ["failed", false],
    ["processing", false],
  ] as const)("offers field correction on a %s record: %s", (status, editable) => {
    const detail = makeDetail({ status, fields: [makeField({ key: "invoice_number", label: "Invoice number", value: "INV-0042" })] });
    render(<DocumentWorkspace detail={detail} onDetail={vi.fn()} onReload={vi.fn()} onDeleted={vi.fn()} />);
    expect(screen.queryAllByRole("button", { name: "Edit Invoice number" }).length).toBe(editable ? 1 : 0);
  });
});
