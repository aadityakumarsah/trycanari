import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CONTENTS, Docs } from "./Docs.tsx";

afterEach(cleanup);

function page() {
  return render(
    <MemoryRouter initialEntries={["/docs"]}>
      <Docs />
    </MemoryRouter>,
  );
}

describe("Docs", () => {
  test("every entry in the sidebar leads to a section on the page", () => {
    const view = page();
    const ids = CONTENTS.flatMap((group) => group.entries.map((entry) => entry.id));
    expect(ids.length).toBe(16);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) {
      expect(view.container.querySelector(`section#${id} h2`)).not.toBeNull();
      expect(view.container.querySelector(`.docs-nav a[href="#${id}"]`)).not.toBeNull();
    }
    expect(view.container.querySelectorAll("section.doc-section").length).toBe(ids.length);
  });

  test("it documents every endpoint the app itself calls", () => {
    const text = page().container.textContent ?? "";
    for (const path of [
      "/v1/bounties",
      "/v1/bounties/{id}/attempts",
      "/v1/attempts/{id}",
      "/v1/sponsors",
      "/v1/bounties/{id}/config",
      "/v1/bounties/{id}/canaries",
      "/v1/bounties/{id}/fund",
      "/v1/bounties/{id}/activate",
      "/v1/bounties/{id}/pause",
      "/v1/bounties/{id}/rearm",
      "/v1/bounties/{id}/close",
      "/v1/sponsor/bounties",
      "/v1/bounties/{id}/findings",
      "/v1/bounties/{id}/ledger",
      "/v1/session",
      "/agent.md",
    ]) {
      expect(text).toContain(path);
    }
  });

  test("examples point at the server the page came from, and can be copied", () => {
    const view = page();
    const first = view.container.querySelector(".code-block pre")?.textContent;
    expect(first).toBe("curl http://localhost/v1/bounties");
    expect(view.container.querySelectorAll(".code-block button").length).toBeGreaterThan(8);
  });

  test("the machine-readable references are linked", () => {
    const hrefs = [...page().container.querySelectorAll(".docs-nav a")].map((a) => a.getAttribute("href"));
    for (const href of ["/api-docs", "/openapi.json", "/agent.md"]) expect(hrefs).toContain(href);
  });
});
