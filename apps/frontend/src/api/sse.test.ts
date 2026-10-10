import { createSseParser, type SseEvent } from "./sse";

function collect(chunks: string[]): SseEvent[] {
  const events: SseEvent[] = [];
  const feed = createSseParser((e) => events.push(e));
  chunks.forEach(feed);
  return events;
}

test("parses complete events", () => {
  expect(collect(['event: step\ndata: {"step":1}\n\nevent: answer\ndata: {}\n\n'])).toEqual([
    { event: "step", data: '{"step":1}' },
    { event: "answer", data: "{}" },
  ]);
});

test("joins events split across network chunks", () => {
  expect(collect(["event: st", 'ep\ndata: {"st', 'ep":2}\n', "\n"])).toEqual([{ event: "step", data: '{"step":2}' }]);
});

test("accepts CRLF line endings", () => {
  expect(collect(["event: started\r\ndata: {}\r\n\r\n"])).toEqual([{ event: "started", data: "{}" }]);
});

test("keeps an incomplete event until it ends", () => {
  expect(collect(["event: step\ndata: {}\n"])).toEqual([]);
});
