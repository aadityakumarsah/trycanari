import type { Evidence, Message, Reply } from "../types.ts";

const ROLES: Record<string, string> = { user: "Hunter", assistant: "Agent", tool: "Tool result" };

/**
 * A conversation. Everything in it was written by a hunter or by the agent under
 * attack, so it is only ever rendered as text: React escapes it, and nothing here
 * turns a string into markup.
 */
export function Transcript({ messages, evidence = [] }: { messages: Message[]; evidence?: Evidence[] }) {
  return (
    <ol className="transcript">
      {messages.map((message, index) => {
        const hits = evidence.filter((item) => item.message_index === index);
        return (
          <li key={index} className={`message message-${message.role}${hits.length ? " tripped" : ""}`}>
            <span className="role">{ROLES[message.role] ?? message.role}</span>
            {message.content && <p className="text">{message.content}</p>}
            {message.tool_calls.map((call, callIndex) => (
              <pre key={callIndex} className="call">
                {call.name}({JSON.stringify(call.arguments, null, 2)})
              </pre>
            ))}
            {hits.map((hit, hitIndex) => (
              <p key={hitIndex} className="hit">
                Tripped {hit.canary?.public_label ?? "a canary"}
                {hit.representation ? ` (${hit.representation})` : ""}
              </p>
            ))}
          </li>
        );
      })}
    </ol>
  );
}

/** The agent's side of one run, as a hunter is shown it. */
export function Replies({ replies }: { replies: Reply[] }) {
  const messages: Message[] = replies.map((reply) => ({
    role: "assistant",
    content: reply.text,
    tool_calls: reply.tool_calls,
  }));
  return <Transcript messages={messages} />;
}
