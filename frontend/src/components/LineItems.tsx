import { List, LocateFixed } from "lucide-react";
import type { LineItem } from "../api/types";
import { formatMoney } from "../lib/format";
import type { EvidenceTarget } from "./EvidenceViewer";
import { EmptyState } from "./States";

interface LineItemsProps {
  items: readonly LineItem[];
  currency: string | null;
  total: string | null;
  selectedId: string | null;
  onSelect: (target: EvidenceTarget) => void;
}

function sumAmounts(items: readonly LineItem[]): string | null {
  const amounts = items.map((item) => item.amount).filter((a): a is string => a !== null);
  if (amounts.length === 0) return null;
  // Summed in integer cents so the comparison with the document total is exact.
  const cents = amounts.reduce((acc, a) => acc + Math.round(Number(a) * 100), 0);
  return (cents / 100).toFixed(2);
}

export function LineItems({ items, currency, total, selectedId, onSelect }: LineItemsProps) {
  if (items.length === 0) {
    return (
      <EmptyState icon={List} title="No line items">
        Line items are extracted for invoices, receipts, purchase orders, and quotes.
      </EmptyState>
    );
  }
  const sum = sumAmounts(items);
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col" className="num">#</th>
            <th scope="col">Description</th>
            <th scope="col" className="num">Qty</th>
            <th scope="col" className="num">Unit price</th>
            <th scope="col" className="num">Amount</th>
            <th scope="col">
              <span className="sr-only">Evidence</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => {
            const targetId = `line-item:${item.idx}`;
            const evidence = item.evidence;
            return (
              <tr key={item.idx} data-selected={selectedId === targetId || undefined}>
                <td className="num muted">{i + 1}</td>
                <td>{item.description}</td>
                <td className="num">{item.quantity ?? "—"}</td>
                <td className="num">{formatMoney(item.unit_price, currency)}</td>
                <td className="num">{formatMoney(item.amount, currency)}</td>
                <td className="cell-actions">
                  <button
                    type="button"
                    className="btn btn-sm btn-icon-only"
                    disabled={evidence === null}
                    aria-label={`Show line ${i + 1} in the document`}
                    onClick={() => evidence && onSelect({ id: targetId, label: `Line ${i + 1}`, evidence })}
                  >
                    <LocateFixed size={15} aria-hidden="true" />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
        {sum !== null && (
          <tfoot>
            <tr>
              <th scope="row" colSpan={4}>
                Sum of line items
                {total !== null && Number(total) !== Number(sum) && <span className="tfoot-warn"> · document total is {formatMoney(total, currency)}</span>}
              </th>
              <td className="num">
                <strong>{formatMoney(sum, currency)}</strong>
              </td>
              <td />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
