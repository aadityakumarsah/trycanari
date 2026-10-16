import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AssurancePage } from "./Assurance.tsx";

afterEach(cleanup);

describe("managed assurance", () => {
  test("makes the paid offer, evidence and limits explicit", () => {
    const view = render(
      <MemoryRouter>
        <AssurancePage />
      </MemoryRouter>,
    );
    const text = view.container.textContent ?? "";
    expect(text).toContain("$2,500");
    expect(text).toContain("Threat model");
    expect(text).toContain("Continuous gate");
    expect(text).toContain("not a certificate");
    expect(view.getByText("Start an assessment").closest("a")?.getAttribute("href")).toContain(
      "/pilot",
    );
  });
});
