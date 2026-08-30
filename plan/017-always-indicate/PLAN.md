# 017 — always indicate (core 0.88.x, chat-ui 0.22.0)

Owner decision (2026-08-30, after 016 shipped): "the worst thing is a silent
agent when it's actually working". Reality check from code + a hosted turn:
the 016 row works — chips shimmer — but tools finish in milliseconds and
90% of a turn is the model thinking between tool calls, for which the only
signal was the composer's static `⟳ working…`. Also found: the wrapper emits
`tool.called` only AFTER pre-gate / approval / policy / vault-ref checks
(luna/agent/runtime.py ≈L2105), so a tool that is skipped, rejected, blocked
or waiting for approval never gets a chip — the UI drops `tool.completed`
for unknown ids.

GPT reasoning: NOT in scope. Verified in code that the current Chat
Completions path already surfaces whatever `reasoning` field OpenAI streams
(pydantic-ai `_map_thinking_delta` → runtime.py L3698); gpt-5.5 shows a
"Thought Ns" pill today. No Responses-API switch.

## Target (owner-picked)

1. **Composer shows nothing while streaming** except the Stop button. The
   word "working" is gone.
2. **Spinner + elapsed time at the END of the tool row**, under the message,
   for the whole live turn: `⟳ 1m 40s`. After 20 s without any frame
   (tool frame, delta, reasoning) the label becomes `still working · 1m 40s`
   (amber). Renders alone when the turn has no tools yet. Disappears when the
   turn ends; the grey chips stay as the receipt. No round counter.
3. **Argument hint on the chip**: `Monday list boards · "Sales Q3"` —
   the server picks ONE safe short scalar from the tool's arguments
   (never secrets, never vault refs, ≤ 40 chars) and sends it as `hint` on
   the `tool.called` frame. Chip label = humanised name · hint.
4. **Truth fixes (D1–D3)**:
   - D1 `tool.called` is emitted the moment the wrapper is entered (status
     `pending`), again with status `awaiting_approval` right before an
     approval prompt, and again with status `running` right before the
     handler runs. `tool.called` is therefore an UPSERT by `call_id` in the
     UI. Completed statuses stay: `ok | skipped | rejected | error`.
     Chip rendering: pending/running → shimmer; awaiting_approval → amber
     static `· waiting for approval` (the end-of-row spinner keeps spinning —
     the turn is alive, the human is the blocker); skipped → grey
     `· skipped`; rejected → grey `· rejected`; error → rose.
   - D2 red chip: click toggles the error text inline under the row
     (title tooltip stays).
   - D3 the `still working` silence guard above.

## Data

- Bus: `tool.called {call_id, name, plugin, status, arguments}` (now up to
  three per call), `tool.completed {call_id, name, status, error|reason,
  duration_ms}`. Consumers other than the chat SSE: none (grep'd — the
  debug timeline reads the audit table, not the bus).
- API frame (`plugins/plugin_api/app.py::_project_tool_frame`) adds `hint`
  computed by `_tool_hint(arguments)`; still never forwards `arguments`.
- Hint rule: iterate arguments in order; skip keys matching
  `token|secret|password|passwd|key|credential|auth|cookie|value|content|body|text`;
  skip values starting with `vault:`; take the first `str` (non-empty, no
  newline) or `int/float`; strings truncated to 40 chars + `…`. Preferred
  keys checked first: `name, title, query, q, path, file, filename, url,
  board, board_id, id, skill, tool, table, channel, to`.

## Phases

- **phase-1-core-tool-lifecycle** (luna core): D1 emits, `hint` projection,
  tests in tests/090-one-tool-row (extend) — version bump, commit, push.
- **phase-2-ui-row-spinner** (plugin_chat_ui 0.22.0): ToolLogEntry upsert +
  statuses + hint; end-of-row spinner/elapsed/still-working; remove
  WorkingLine; click-for-error; unit tests; browser verification on an
  isolated QA Luna with the phase-1 core.
- **phase-3-publish**: package (rsync copy, no ui-src) + publish 0.22.0 to
  marketplace "official"; verify index.json. Folded into phase 2 if it
  ships in the same sitting.

## Non-goals

Round counter, model name on the spinner, retry/fallback chips, subagent
progress in-row, "read your message" flash, GPT Responses API, raw argument
dumps, token/cost counters.
