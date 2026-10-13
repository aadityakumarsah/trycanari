import { describe, expect, test } from "bun:test";

import { TEMPLATES, argsOf, blankTool, freeName, numberIn, problemIn, rowsOf, schemaOf, toolsOf } from "./tools.ts";
import type { Tool } from "./types.ts";

const TRANSFER: Tool = {
  name: "transfer_funds",
  description: "Transfer funds from the treasury to a recipient.",
  parameters: {
    type: "object",
    properties: { recipient: { type: "string" }, amount: { type: "number" } },
    required: ["recipient", "amount"],
  },
  result: "Transfer submitted.",
};

describe("the tools form", () => {
  test("a saved tool survives a trip through the form unchanged", () => {
    const rows = rowsOf([TRANSFER]);
    expect(rows?.[0]?.args).toEqual([
      { name: "recipient", type: "string", required: true, description: "" },
      { name: "amount", type: "number", required: true, description: "" },
    ]);
    expect(toolsOf(rows ?? [])).toEqual([TRANSFER]);
    // A tool that reads untrusted content keeps that mark both ways; one that does not has none.
    const reader = { ...TRANSFER, name: "fetch_page", untrusted: true };
    expect(toolsOf(rowsOf([reader]) ?? [])).toEqual([reader]);
    expect("untrusted" in (toolsOf(rows ?? [])[0] ?? {})).toBe(false);
  });

  test("a tool with no arguments keeps the plain schema", () => {
    expect(argsOf({ type: "object" })).toEqual([]);
    expect(argsOf({})).toEqual([]);
    expect(schemaOf([])).toEqual({ type: "object" });
  });

  test("descriptions and optional arguments are kept", () => {
    const schema = schemaOf([
      { name: " to ", type: "string", required: true, description: " Recipient " },
      { name: "urgent", type: "boolean", required: false, description: "" },
      { name: "  ", type: "string", required: true, description: "ignored" },
    ]);
    expect(schema).toEqual({
      type: "object",
      properties: { to: { type: "string", description: "Recipient" }, urgent: { type: "boolean" } },
      required: ["to"],
    });
    expect(argsOf(schema)?.map((arg) => arg.required)).toEqual([true, false]);
  });

  test("a schema the form cannot show is left to the JSON editor", () => {
    const nested = { type: "object", properties: { to: { type: "array", items: { type: "string" } } } };
    const choice = { type: "object", properties: { size: { type: "string", enum: ["s", "m"] } } };
    for (const parameters of [nested, choice, { type: "string" }, { type: "object", additionalProperties: false }, []]) {
      expect(argsOf(parameters)).toBeNull();
    }
    expect(argsOf({ type: "object", properties: {}, required: ["ghost"] })).toBeNull();
    expect(rowsOf([TRANSFER, { ...TRANSFER, name: "other", parameters: nested }])).toBeNull();
  });

  test("problems are named before anything is sent", () => {
    const good = { ...blankTool(), name: "send_email" };
    expect(problemIn([good])).toBe("");
    expect(problemIn([blankTool()])).toBe("Tool 1 needs a name.");
    expect(problemIn([{ ...good, name: "send email" }])).toContain("not a usable tool name");
    expect(problemIn([good, { ...good }])).toBe('Two tools are both called "send_email".');
    const arg = { name: "to", type: "string" as const, required: true, description: "" };
    expect(problemIn([{ ...good, args: [arg, { ...arg }] }])).toBe('send_email has two arguments called "to".');
    expect(problemIn([{ ...good, args: [{ ...arg, name: " " }] }])).toBe("send_email has an argument without a name.");
  });

  test("every template is a tool that can be saved as it is", () => {
    for (const { tool } of TEMPLATES) {
      expect(problemIn([tool])).toBe("");
      expect(rowsOf(toolsOf([tool]))).toEqual([tool]);
    }
  });

  test("adding a template twice does not make two tools with one name", () => {
    const first = TEMPLATES[0]?.tool ?? blankTool();
    expect(freeName("transfer_funds", [])).toBe("transfer_funds");
    expect(freeName("transfer_funds", [first])).toBe("transfer_funds_2");
    expect(freeName("transfer_funds", [first, { ...first, name: "transfer_funds_2" }])).toBe("transfer_funds_3");
  });

  test("numbers typed into a box", () => {
    expect(numberIn("0.2")).toBe(0.2);
    expect(numberIn(" 512 ")).toBe(512);
    expect(numberIn("")).toBeUndefined();
    expect(numberIn("warm")).toBeUndefined();
  });
});
