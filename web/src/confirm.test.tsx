import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { ConfirmProvider, useConfirm } from "./confirm.tsx";

describe("the confirm dialog", () => {
  test("shows nothing until something asks", () => {
    function Asker() {
      useConfirm();
      return <p>page</p>;
    }
    const html = renderToStaticMarkup(
      <ConfirmProvider>
        <Asker />
      </ConfirmProvider>,
    );
    expect(html).toBe("<p>page</p>");
  });

  test("outside a provider the answer is no", async () => {
    let ask: ReturnType<typeof useConfirm> | undefined;
    function Asker() {
      ask = useConfirm();
      return null;
    }
    renderToStaticMarkup(<Asker />);
    expect(await ask?.({ title: "Sure?" })).toBe(false);
  });
});
