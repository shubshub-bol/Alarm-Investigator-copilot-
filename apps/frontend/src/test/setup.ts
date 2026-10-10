import "@testing-library/jest-dom/vitest";

// jsdom has no layout engine, so it does not implement scrolling; the app calls it to follow new content.
Element.prototype.scrollIntoView = function scrollIntoView() {};
