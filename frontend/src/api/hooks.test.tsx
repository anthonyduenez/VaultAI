import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useResource } from "./hooks";

afterEach(() => {
  vi.useRealTimers();
});

describe("useResource", () => {
  it("re-renders on a poll only when the data changed, and never overlaps requests", async () => {
    vi.useFakeTimers();
    const replies = [{ n: 1 }, { n: 1 }, { n: 1 }, { n: 2 }];
    let inFlight = 0;
    let maxInFlight = 0;
    const load = async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 1500));
      inFlight -= 1;
      return replies.shift() ?? { n: 2 };
    };
    let tableRenders = 0;
    function Table({ n }: { n: number }) {
      tableRenders += 1;
      return <p>value {n}</p>;
    }
    function Page() {
      const { state } = useResource("probe", load, { poll: () => 1000 });
      return state.kind === "ready" ? <Table n={state.data.n} /> : null;
    }

    render(<Page />);
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(screen.getByText("value 1")).toBeInTheDocument();
    expect(tableRenders).toBe(1);

    await act(() => vi.advanceTimersByTimeAsync(2 * 2500));
    expect(tableRenders).toBe(1);

    await act(() => vi.advanceTimersByTimeAsync(2500));
    expect(screen.getByText("value 2")).toBeInTheDocument();
    expect(tableRenders).toBe(2);
    expect(maxInFlight).toBe(1);
  });
});
