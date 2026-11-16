import { authHeaders, BASE, type RunEvent } from "./api";

/**
 * Authenticated Server-Sent Events over fetch (EventSource cannot send an Authorization
 * header). Reconnects with Last-Event-ID, so no event is lost or delivered twice.
 */
export function streamRunEvents(
  runId: string,
  handlers: { onEvent: (event: RunEvent) => void; onEnd: () => void; onState?: (live: boolean) => void },
  after = 0,
): () => void {
  const controller = new AbortController();
  let cursor = after;
  let ended = false;

  const dispatch = (block: string) => {
    let id = 0, type = "message", data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("id:")) id = Number(line.slice(3).trim());
      else if (line.startsWith("event:")) type = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (type === "stream.end") { ended = true; handlers.onEnd(); return; }
    if (!id || !data) return; // keep-alive comment
    cursor = id;
    handlers.onEvent({ id, type, data: JSON.parse(data), created_at: new Date().toISOString() });
  };

  const connect = async () => {
    let attempt = 0;
    while (!controller.signal.aborted && !ended) {
      try {
        const response = await fetch(`${BASE}/runs/${runId}/events`, {
          headers: { ...authHeaders(), Accept: "text/event-stream", "Last-Event-ID": String(cursor) },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(String(response.status));
        handlers.onState?.(true);
        attempt = 0;
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value.replace(/\r/g, "");
          let cut: number;
          while ((cut = buffer.indexOf("\n\n")) >= 0) {
            dispatch(buffer.slice(0, cut));
            buffer = buffer.slice(cut + 2);
          }
        }
      } catch {
        if (controller.signal.aborted) return;
      }
      if (ended) return;
      handlers.onState?.(false);
      await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * 2 ** attempt++, 10_000)));
    }
  };
  void connect();
  return () => controller.abort();
}
