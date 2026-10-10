import { useCallback, useEffect, useRef, useState } from "react";
import { type ApiFailure, toFailure } from "./client";

export type ResourceState<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  // A failed refresh keeps the last good data so polling views do not blank out.
  | { kind: "error"; error: ApiFailure; data: T | null };

export interface Resource<T> {
  state: ResourceState<T>;
  reload: () => void;
  replace: (data: T) => void;
}

interface ResourceOptions<T> {
  /** Milliseconds until the next refresh given the latest data, or null to stop polling. */
  poll?: (data: T | null) => number | null;
}

export function dataOf<T>(state: ResourceState<T>): T | null {
  return state.kind === "loading" ? null : state.data;
}

/**
 * Fetches whenever `key` changes or `reload()` is called. The request is aborted when the key
 * changes or the component unmounts, and responses from superseded requests are dropped.
 */
export function useResource<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  options: ResourceOptions<T> = {},
): Resource<T> {
  const [state, setState] = useState<ResourceState<T>>({ kind: "loading" });
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  const pollRef = useRef(options.poll);
  const dataRef = useRef<T | null>(null);
  // JSON of the data in the current "ready" state; null while loading or after a failed refresh.
  const readyJson = useRef<string | null>(null);

  useEffect(() => {
    loadRef.current = load;
    pollRef.current = options.poll;
  });

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    // A poll re-runs the load here instead of through state and sets state only when the data
    // changed, so an unchanged poll does not re-render the tables below.
    const schedule = (data: T | null) => {
      const ms = pollRef.current?.(data) ?? null;
      if (ms !== null) timer = window.setTimeout(run, ms);
    };
    const run = () =>
      loadRef.current(controller.signal).then(
        (data) => {
          if (controller.signal.aborted) return;
          const json = JSON.stringify(data);
          if (json !== readyJson.current) {
            readyJson.current = json;
            dataRef.current = data;
            setState({ kind: "ready", data });
          }
          schedule(data);
        },
        (err: unknown) => {
          if (controller.signal.aborted) return;
          readyJson.current = null;
          setState({ kind: "error", error: toFailure(err), data: dataRef.current });
          schedule(dataRef.current);
        },
      );
    void run();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const replace = useCallback((data: T) => {
    readyJson.current = JSON.stringify(data);
    dataRef.current = data;
    setState({ kind: "ready", data });
  }, []);

  return { state, reload, replace };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

export function useNow(intervalMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
