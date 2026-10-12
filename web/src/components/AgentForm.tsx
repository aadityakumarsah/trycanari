import { useState, type FormEvent } from "react";

import { api, messageOf } from "../api.ts";
import { useAction, useLoad } from "../hooks.ts";
import {
  ARG_TYPES,
  TEMPLATES,
  blankTool,
  freeName,
  numberIn,
  problemIn,
  rowsOf,
  toolsOf,
  type ArgRow,
  type ArgType,
  type ToolRow,
} from "../tools.ts";
import type { EntryPoint, ModelTest, PlatformModels, SponsorBountyDetail } from "../types.ts";
import { Icon } from "./Brand.tsx";
import { ErrorNote } from "./ui.tsx";

interface Props {
  bounty: SponsorBountyDetail;
  act: (path: string, body?: unknown, method?: string) => Promise<void>;
}

const SLOTS: [EntryPoint, string, string][] = [
  ["user_message", "The user's message", "Hunters type what a user would say to your agent."],
  ["document", "A document it reads", "Hunters supply a file or page your agent is given before the user speaks."],
];

/** The agent a sponsor lists: what it is told, where it runs, what it can do. */
export function AgentForm({ bounty, act }: Props) {
  const config = bounty.config;
  const saving = useAction();

  const [prompt, setPrompt] = useState(config?.system_prompt ?? "");
  const [own, setOwn] = useState(Boolean(config?.endpoint_base));
  const [model, setModel] = useState(config?.model ?? "");
  const [base, setBase] = useState(config?.endpoint_base ?? "");
  const [key, setKey] = useState("");
  const [slots, setSlots] = useState<EntryPoint[]>(config?.entry_points ?? ["user_message"]);

  const saved = rowsOf(config?.tools ?? []);
  const [asJson, setAsJson] = useState(saved === null);
  const [rows, setRows] = useState<ToolRow[]>(saved ?? []);
  const [json, setJson] = useState(JSON.stringify(config?.tools ?? [], null, 2));
  const [toolsError, setToolsError] = useState("");

  const params = config?.params ?? {};
  const [temperature, setTemperature] = useState(typeof params.temperature === "number" ? String(params.temperature) : "");
  const [maxTokens, setMaxTokens] = useState(typeof params.max_tokens === "number" ? String(params.max_tokens) : "");

  const ours = useLoad((signal) => api<PlatformModels>("GET", "/v1/models", { signal }), "models", "models").data;
  const [tested, setTested] = useState<ModelTest | null>(null);
  const [testing, setTesting] = useState(false);

  // A saved key goes on being used while the URL it was saved for is unchanged.
  const keyKept = Boolean(config?.endpoint_key_set) && base.trim() === (config?.endpoint_base ?? "");
  const offered = own ? (tested?.ok ? tested.models : []) : (ours?.models ?? []);

  function changed() {
    setTested(null);
  }

  async function test() {
    setTesting(true);
    setTested(null);
    try {
      const body: Record<string, string> = { model: model.trim() };
      if (own) {
        body.endpoint_base = base.trim();
        if (key) body.endpoint_key = key;
      }
      setTested(await api<ModelTest>("POST", `/v1/bounties/${bounty.id}/model-test`, { body }));
    } catch (caught) {
      setTested({ ok: false, detail: messageOf(caught) || "The test could not be run.", milliseconds: 0, models: [] });
    } finally {
      setTesting(false);
    }
  }

  function toJson() {
    setJson(JSON.stringify(toolsOf(rows), null, 2));
    setToolsError("");
    setAsJson(true);
  }

  function toForm() {
    try {
      const parsed = rowsOf(JSON.parse(json || "[]") as never);
      if (!parsed) {
        setToolsError("These tools use schema features the form cannot show. Keep editing them as JSON.");
        return;
      }
      setRows(parsed);
      setToolsError("");
      setAsJson(false);
    } catch {
      setToolsError("That is not valid JSON yet.");
    }
  }

  function setTool(index: number, change: Partial<ToolRow>) {
    setRows((current) => current.map((row, at) => (at === index ? { ...row, ...change } : row)));
  }

  function setArg(tool: number, index: number, change: Partial<ArgRow>) {
    setRows((current) =>
      current.map((row, at) =>
        at === tool ? { ...row, args: row.args.map((arg, i) => (i === index ? { ...arg, ...change } : arg)) } : row,
      ),
    );
  }

  async function save() {
    let tools: unknown;
    if (asJson) {
      try {
        tools = JSON.parse(json || "[]");
      } catch {
        throw new Error("The tools are not valid JSON.");
      }
    } else {
      const problem = problemIn(rows);
      if (problem) throw new Error(problem);
      tools = toolsOf(rows);
    }
    // Options this form has no box for (top_p, stop, seed) are kept as they were.
    const options: Record<string, unknown> = { ...params };
    delete options.temperature;
    delete options.max_tokens;
    const warmth = numberIn(temperature);
    const length = numberIn(maxTokens);
    if (warmth !== undefined) options.temperature = warmth;
    if (length !== undefined) options.max_tokens = Math.round(length);

    const body: Record<string, unknown> = {
      system_prompt: prompt,
      model: model.trim(),
      tools,
      params: options,
      entry_points: slots,
    };
    if (own) {
      body.endpoint_base = base.trim();
      if (key) body.endpoint_key = key;
    }
    await act("config", body, "PUT");
  }

  return (
    <form
      className="agent-form"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void saving.run(save);
      }}
    >
      <div className="step">
        <h3>
          <span>1</span> What your agent is told
        </h3>
        <label>
          System prompt
          <textarea
            rows={8}
            required
            spellCheck={false}
            placeholder="You are the support assistant for…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <small>Paste your agent's instructions. Encrypted at rest and never shown to hunters.</small>
        </label>
      </div>

      <div className="step">
        <h3>
          <span>2</span> Where it runs
        </h3>
        <div className="where" role="radiogroup" aria-label="Where the agent runs">
          <button
            type="button"
            role="radio"
            aria-checked={!own}
            className={own ? "where-card" : "where-card on"}
            onClick={() => {
              setOwn(false);
              changed();
            }}
          >
            <Icon name="zap" size={18} />
            <b>Quick start</b>
            <small>Run it on one of our models. Nothing to set up.</small>
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={own}
            className={own ? "where-card on" : "where-card"}
            onClick={() => {
              setOwn(true);
              changed();
            }}
          >
            <Icon name="link" size={18} />
            <b>Connect your own model</b>
            <small>Hunters attack the model you host: self-built, fine-tuned or any provider.</small>
          </button>
        </div>

        {own && (
          <div className="own">
            <label>
              Endpoint URL
              <input
                required
                type="url"
                spellCheck={false}
                className="code"
                placeholder="https://api.yourcompany.com/v1"
                value={base}
                onChange={(e) => {
                  setBase(e.target.value);
                  changed();
                }}
              />
              <small>
                The base URL of an OpenAI-compatible API: we call <code>/chat/completions</code> under it. It must be
                reachable from the internet over https.
              </small>
            </label>
            <label>
              API key
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                className="code"
                required={!keyKept}
                placeholder={keyKept ? "Saved. Leave blank to keep it" : "The key we send as a Bearer token"}
                value={key}
                onChange={(e) => {
                  setKey(e.target.value);
                  changed();
                }}
              />
              <small>Stored encrypted and never shown again, not even to you. Use a key made for this bounty.</small>
            </label>
          </div>
        )}

        <div className="field-block">
          <label htmlFor="agent-model">Model</label>
          <span className="with-button">
            <input
              id="agent-model"
              required
              spellCheck={false}
              className="code"
              list="models-offered"
              placeholder={own ? "The model name your endpoint expects" : "Pick a model or type its name"}
              value={model}
              onChange={(e) => {
                setModel(e.target.value);
                changed();
              }}
            />
            <button
              type="button"
              className="secondary"
              disabled={testing || !model.trim() || (own && (!base.trim() || (!key && !keyKept)))}
              onClick={() => void test()}
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
          </span>
          <datalist id="models-offered">
            {offered.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          {!own && ours?.provider && (
            <small>
              {ours.models.length} models from {ours.provider}. Start typing to search them.
            </small>
          )}
        </div>
        {tested && (
          <p className={tested.ok ? "test-line good" : "test-line bad"} role="status">
            <Icon name={tested.ok ? "check" : "ban"} size={16} />
            <span>
              {tested.ok ? "Connected. " : "It did not work. "}
              {tested.detail}
              {tested.ok && tested.milliseconds > 0 && ` (${(tested.milliseconds / 1000).toFixed(1)} s)`}
            </span>
          </p>
        )}
      </div>

      <div className="step">
        <h3>
          <span>3</span> What it can do
          <button type="button" className="link" onClick={asJson ? toForm : toJson}>
            {asJson ? "Use the form" : "Edit as JSON"}
          </button>
        </h3>
        <p className="muted small">
          List the tools your agent can call. They are mocks: nothing real runs, the tool just answers with the reply
          you write here. Leave this empty for an agent that only talks.
        </p>
        {asJson ? (
          <label>
            Tools (JSON)
            <textarea rows={10} spellCheck={false} value={json} onChange={(e) => setJson(e.target.value)} />
            <small>
              A list of objects with <code>name</code>, <code>description</code>, <code>parameters</code> (JSON
              Schema) and <code>result</code>.
            </small>
          </label>
        ) : (
          <>
            {rows.map((row, index) => (
              <div key={index} className="tool-card">
                <div className="tool-head">
                  <label>
                    Tool name
                    <input
                      required
                      spellCheck={false}
                      className="code"
                      placeholder="transfer_funds"
                      value={row.name}
                      onChange={(e) => setTool(index, { name: e.target.value })}
                    />
                  </label>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Remove ${row.name || "this tool"}`}
                    onClick={() => setRows((current) => current.filter((_, at) => at !== index))}
                  >
                    <Icon name="close" size={16} />
                  </button>
                </div>
                <label>
                  What it does
                  <input
                    placeholder="Transfer funds to a recipient."
                    value={row.description}
                    onChange={(e) => setTool(index, { description: e.target.value })}
                  />
                  <small>Your agent reads this to decide when to use the tool.</small>
                </label>
                <div className="args">
                  <span className="args-title">What your agent passes to it</span>
                  {row.args.map((arg, at) => (
                    <div key={at} className="arg-row">
                      <input
                        required
                        spellCheck={false}
                        className="code"
                        aria-label="Argument name"
                        placeholder="recipient"
                        value={arg.name}
                        onChange={(e) => setArg(index, at, { name: e.target.value })}
                      />
                      <select
                        aria-label="Argument type"
                        value={arg.type}
                        onChange={(e) => setArg(index, at, { type: e.target.value as ArgType })}
                      >
                        {ARG_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {type === "string" ? "text" : type === "boolean" ? "yes / no" : type}
                          </option>
                        ))}
                      </select>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={arg.required}
                          onChange={(e) => setArg(index, at, { required: e.target.checked })}
                        />
                        required
                      </label>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`Remove ${arg.name || "this argument"}`}
                        onClick={() => setTool(index, { args: row.args.filter((_, i) => i !== at) })}
                      >
                        <Icon name="close" size={14} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="link add"
                    onClick={() =>
                      setTool(index, { args: [...row.args, { name: "", type: "string", required: true, description: "" }] })
                    }
                  >
                    <Icon name="plus" size={14} /> Add an argument
                  </button>
                </div>
                <label>
                  What the mock answers
                  <input
                    spellCheck={false}
                    placeholder="Transfer submitted."
                    value={row.result}
                    onChange={(e) => setTool(index, { result: e.target.value })}
                  />
                  <small>Your agent gets this back whenever it calls the tool.</small>
                </label>
              </div>
            ))}
            <div className="tool-add">
              <button type="button" className="secondary" onClick={() => setRows((current) => [...current, blankTool()])}>
                <Icon name="plus" size={15} /> Add a tool
              </button>
              <span className="muted small">or start from:</span>
              {TEMPLATES.map(({ label, tool }) => (
                <button
                  key={label}
                  type="button"
                  className="chip"
                  onClick={() => setRows((current) => [...current, { ...tool, name: freeName(tool.name, current) }])}
                >
                  {label}
                </button>
              ))}
            </div>
          </>
        )}
        {toolsError && <p className="test-line bad">{toolsError}</p>}
      </div>

      <div className="step">
        <h3>
          <span>4</span> Where hunters can attack
        </h3>
        <div className="slots">
          {SLOTS.map(([slot, title, hint]) => (
            <label key={slot} className={slots.includes(slot) ? "slot on" : "slot"}>
              <input
                type="checkbox"
                checked={slots.includes(slot)}
                onChange={() =>
                  setSlots((current) => (current.includes(slot) ? current.filter((s) => s !== slot) : [...current, slot]))
                }
              />
              <span>
                <b>{title}</b>
                <small>{hint}</small>
              </span>
            </label>
          ))}
        </div>
      </div>

      <details className="advanced">
        <summary>Advanced settings</summary>
        <div className="field-pair">
          <label>
            Creativity (temperature)
            <input
              type="number"
              min={0}
              max={2}
              step={0.1}
              placeholder="The model's default"
              value={temperature}
              onChange={(e) => setTemperature(e.target.value)}
            />
            <small>0 is steady, 1 and above is more varied. Leave blank if unsure.</small>
          </label>
          <label>
            Longest reply (tokens)
            <input
              type="number"
              min={1}
              step={1}
              placeholder="1024"
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
            />
            <small>About four characters a token.</small>
          </label>
        </div>
      </details>

      <ErrorNote>{saving.error}</ErrorNote>
      <button disabled={saving.busy || slots.length === 0}>{saving.busy ? "Saving…" : "Save agent"}</button>
    </form>
  );
}
