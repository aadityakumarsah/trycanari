/**
 * The tools form: a sponsor describes a mock tool by filling in boxes, and this turns
 * the boxes into the JSON Schema the API stores, and back.
 */
import type { Tool } from "./types.ts";

export const ARG_TYPES = ["string", "number", "integer", "boolean"] as const;
export type ArgType = (typeof ARG_TYPES)[number];

export interface ArgRow {
  name: string;
  type: ArgType;
  required: boolean;
  description: string;
}

export interface ToolRow {
  name: string;
  description: string;
  result: string;
  args: ArgRow[];
  /** It reads from somewhere a stranger can write: a web page, an inbox, a shared file. */
  untrusted?: boolean;
}

const NAME = /^[A-Za-z0-9_.-]{1,64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function only(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}

/** A tool's arguments as form rows, or null when its schema says more than the form can. */
export function argsOf(parameters: unknown): ArgRow[] | null {
  if (!isRecord(parameters) || !only(parameters, ["type", "properties", "required"])) return null;
  if (parameters.type !== undefined && parameters.type !== "object") return null;
  const properties = parameters.properties ?? {};
  const required = parameters.required ?? [];
  if (!isRecord(properties) || !Array.isArray(required)) return null;
  const rows: ArgRow[] = [];
  for (const [name, schema] of Object.entries(properties)) {
    if (!isRecord(schema) || !only(schema, ["type", "description"])) return null;
    const type = ARG_TYPES.find((known) => known === schema.type);
    if (!type) return null;
    if (schema.description !== undefined && typeof schema.description !== "string") return null;
    rows.push({ name, type, required: required.includes(name), description: schema.description ?? "" });
  }
  // A required argument the schema never describes cannot be shown as a row.
  if (required.some((name) => !rows.some((row) => row.name === name))) return null;
  return rows;
}

/** The JSON Schema for a tool's arguments. */
export function schemaOf(args: ArgRow[]): Record<string, unknown> {
  const named = args.filter((arg) => arg.name.trim());
  if (named.length === 0) return { type: "object" };
  const properties: Record<string, unknown> = {};
  for (const arg of named) {
    const description = arg.description.trim();
    properties[arg.name.trim()] = description ? { type: arg.type, description } : { type: arg.type };
  }
  const required = named.filter((arg) => arg.required).map((arg) => arg.name.trim());
  return required.length ? { type: "object", properties, required } : { type: "object", properties };
}

/** Saved tools as form rows, or null when any of them needs the JSON editor. */
export function rowsOf(tools: Tool[]): ToolRow[] | null {
  const rows: ToolRow[] = [];
  for (const tool of tools) {
    const args = argsOf(tool.parameters);
    if (!args) return null;
    rows.push({
      name: tool.name,
      description: tool.description,
      result: tool.result,
      args,
      ...(tool.untrusted ? { untrusted: true } : {}),
    });
  }
  return rows;
}

export function toolsOf(rows: ToolRow[]): Tool[] {
  return rows.map((row) => ({
    name: row.name.trim(),
    description: row.description.trim(),
    parameters: schemaOf(row.args),
    result: row.result,
    ...(row.untrusted ? { untrusted: true } : {}),
  }));
}

/** What stops these tools being saved, in words for the sponsor. Empty when nothing does. */
export function problemIn(rows: ToolRow[]): string {
  const seen = new Set<string>();
  for (const [index, row] of rows.entries()) {
    const name = row.name.trim();
    const which = name || `Tool ${index + 1}`;
    if (!name) return `Tool ${index + 1} needs a name.`;
    if (!NAME.test(name)) return `"${name}" is not a usable tool name: use letters, digits, _ . and - only.`;
    if (seen.has(name)) return `Two tools are both called "${name}".`;
    seen.add(name);
    const args = new Set<string>();
    for (const arg of row.args) {
      const argument = arg.name.trim();
      if (!argument) return `${which} has an argument without a name.`;
      if (args.has(argument)) return `${which} has two arguments called "${argument}".`;
      args.add(argument);
    }
  }
  return "";
}

/** Tools most agents with something to protect have, ready to add in one click. */
export const TEMPLATES: { label: string; tool: ToolRow }[] = [
  {
    label: "Send money",
    tool: {
      name: "transfer_funds",
      description: "Transfer funds to a recipient.",
      result: "Transfer submitted.",
      args: [
        { name: "recipient", type: "string", required: true, description: "Who receives the funds" },
        { name: "amount", type: "number", required: true, description: "" },
      ],
    },
  },
  {
    label: "Send an email",
    tool: {
      name: "send_email",
      description: "Send an email on the user's behalf.",
      result: "Email sent.",
      args: [
        { name: "to", type: "string", required: true, description: "" },
        { name: "subject", type: "string", required: true, description: "" },
        { name: "body", type: "string", required: true, description: "" },
      ],
    },
  },
  {
    label: "Look up a record",
    tool: {
      name: "lookup_customer",
      description: "Look up a customer record by ID.",
      result: '{"id": "C-1042", "name": "Dana Reyes", "plan": "pro"}',
      args: [{ name: "customer_id", type: "string", required: true, description: "" }],
    },
  },
  {
    label: "Read a web page",
    tool: {
      name: "fetch_page",
      description: "Fetch a web page and return its text.",
      result: "Our opening hours are 9 to 5 on weekdays.",
      untrusted: true,
      args: [{ name: "url", type: "string", required: true, description: "" }],
    },
  },
  {
    label: "Issue a refund",
    tool: {
      name: "issue_refund",
      description: "Refund an order.",
      result: "Refund issued.",
      args: [
        { name: "order_id", type: "string", required: true, description: "" },
        { name: "amount", type: "number", required: true, description: "" },
      ],
    },
  },
];

export function blankTool(): ToolRow {
  return { name: "", description: "", result: "ok", args: [] };
}

/** A name for a template that no tool in the list has yet. */
export function freeName(wanted: string, rows: ToolRow[]): string {
  const taken = new Set(rows.map((row) => row.name.trim()));
  if (!taken.has(wanted)) return wanted;
  for (let number = 2; ; number++) {
    if (!taken.has(`${wanted}_${number}`)) return `${wanted}_${number}`;
  }
}

/** A number typed into a box, or undefined when the box is empty or not a number. */
export function numberIn(raw: string): number | undefined {
  if (!raw.trim()) return undefined;
  const value = Number(raw);
  return Number.isFinite(value) ? value : undefined;
}
