import { isToolKey, splitCitations } from "./citations";

test("splits text into text and citation segments", () => {
  expect(splitCitations("Rising [T2] per SOP [S1].")).toEqual([
    { kind: "text", text: "Rising " },
    { kind: "cite", key: "T2" },
    { kind: "text", text: " per SOP " },
    { kind: "cite", key: "S1" },
    { kind: "text", text: "." },
  ]);
});

test("leaves text without citations alone", () => {
  expect(splitCitations("No evidence.")).toEqual([{ kind: "text", text: "No evidence." }]);
});

test("ignores bracketed text that is not a citation key", () => {
  expect(splitCitations("value [mm/s]")).toEqual([{ kind: "text", text: "value [mm/s]" }]);
});

test("tells tool keys from document keys", () => {
  expect(isToolKey("T12")).toBe(true);
  expect(isToolKey("S3")).toBe(false);
});
