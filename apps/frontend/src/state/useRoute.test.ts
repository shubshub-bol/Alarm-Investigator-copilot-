import { act, renderHook } from "@testing-library/react";
import { useRoute } from "./useRoute";

afterEach(() => window.history.replaceState(null, "", "/"));

test("reads the chat id from the address", () => {
  window.history.replaceState(null, "", "/c/conv-a14c5ce4373f");
  expect(renderHook(() => useRoute()).result.current.conversationId).toBe("conv-a14c5ce4373f");
});

test("'/' and other paths are a new chat", () => {
  window.history.replaceState(null, "", "/somewhere/else");
  expect(renderHook(() => useRoute()).result.current.conversationId).toBeNull();
});

test("go() pushes or replaces the address", () => {
  const { result } = renderHook(() => useRoute());
  const before = window.history.length;
  act(() => result.current.go("conv-a14c5ce4373f"));
  expect(window.location.pathname).toBe("/c/conv-a14c5ce4373f");
  expect(window.history.length).toBe(before + 1);
  act(() => result.current.go(null, "replace"));
  expect(window.location.pathname).toBe("/");
  expect(window.history.length).toBe(before + 1);
  expect(result.current.conversationId).toBeNull();
});

test("Back and Forward change the open chat", () => {
  const { result } = renderHook(() => useRoute());
  act(() => {
    window.history.pushState(null, "", "/c/conv-bbbbbbbbbbbb");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(result.current.conversationId).toBe("conv-bbbbbbbbbbbb");
});
