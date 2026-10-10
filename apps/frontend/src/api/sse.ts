// Minimal Server-Sent Events parser for a fetch() body stream (EventSource cannot POST).

export interface SseEvent {
  event: string;
  data: string;
}

/** Returns a function to feed text chunks into; complete events are passed to onEvent. */
export function createSseParser(onEvent: (event: SseEvent) => void): (chunk: string) => void {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length > 0) onEvent({ event, data: data.join("\n") });
      boundary = buffer.indexOf("\n\n");
    }
  };
}
