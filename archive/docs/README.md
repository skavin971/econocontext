# EconoContext documentation: start here

EconoContext is a small layer that plugs into an agent platform (Omnigent). Before each
step, it checks whether there is a cheaper way to do that step that gives the
model exactly the same information. It keeps a log of every choice it made,
what it expected the step to cost, and, once measured, what the step really
cost.

These pages assume you know basic Python and have used an LLM API. They assume
nothing about EconoContext. Read them in order; each one takes about ten minutes.

| # | Page | The question it answers |
|---|---|---|
| 1 | [The idea](1-the-idea.md) | What problem is this solving, and what words do I need? |
| 2 | [How it fits into an agent](2-how-it-fits.md) | Where does it plug in, and what happens during one run? |
| 3 | [How a decision is made](3-how-a-decision-is-made.md) | What options does it consider, how does it predict cost, and how does it choose? |
| 4 | [Measurement and data](4-measurement-and-data.md) | How are real costs recorded, and what is in the database? |
| 5 | [Omnigent and SWE-bench](5-omnigent-and-swebench.md) | How is it tested on a real agent platform and real GitHub issues? |
| 6 | [Status and future](6-status-and-future.md) | What is real, what is a placeholder, and where is this going? |
| - | [TESTING.md](TESTING.md) | How do I run everything? |
| - | [Omnigent findings](omnigent-findings.md) | What did we check before building on Omnigent, and what did we find? |

## The whole system in one picture

```
          ┌──────────── the agent platform (Omnigent, unmodified) ──────────┐
          │    every model call              every tool call and result     │
          └──────────┬──────────────────────────────┬───────────────────────┘
                     │ via its model URL            │ via a policy
                     ▼                              ▼
          omnigent_layer/gateway.py      omnigent_layer/policy.py
                     │                              │
                     ▼                              ▼
          ┌──────────────────── EconoContext engine ────────────────────────┐
          │ 1. monitor   write down what exists (agents, windows, versions)  │
          │ 2. planner   list the options (always including "do nothing")    │
          │ 3. gates     drop options that would be wrong                    │
          │ 4. cost model  predict what each remaining option costs         │
          │ 5. optimizer   pick the cheapest; log why the others lost       │
          │ 6. assembler   build the exact request for the chosen option    │
          │ 7. guard       if anything fails, the harness proceeds unchanged │
          └──────────────────────────────┬──────────────────────────────────┘
                                         │ every decision, every real cost
                                         ▼
                          Agent DB (SQLite: data/econocontext.sqlite3)
```

## Glossary

| Term | Meaning |
|---|---|
| **Platform / harness** | The program running the agent loop. Here Omnigent runs it (with its `openai-agents` harness); it owns the tools, the history and the loop. |
| **Agent / sub-agent** | The main agent works on the task. It can start a sub-agent (a helper with its own fresh context). |
| **Context window** | Everything sent to the model on one call: system prompt, tool descriptions, task, and the conversation so far. |
| **Segment** | One piece of a window: the system prompt, the tool list, the task, one message, one tool call or one tool result. |
| **Token** | The unit the provider bills in. Here it is estimated as characters ÷ 4 and measured exactly from the provider's report. |
| **Billing categories** | The separately priced kinds of tokens: uncached input, cache read, cache write and output. |
| **Prompt cache** | The provider remembers the start (prefix) of a recent prompt and bills those tokens cheaper when they are sent again. Gemini gives 90% off. |
| **NU** | Normalized unit: the price of 1 uncached input token of the model in use. It lets costs be compared without caring about the provider. |
| **Intercept** | A moment where EconoContext gets to decide before the platform acts. There are 4 decision intercepts. |
| **Candidate / operator** | One way to carry out a step, such as `KEEP_FULL` or `POINTER`. Operators are the catalog; candidates are the ones proposed for one step. |
| **Host default** | What the harness would do without EconoContext. It is always one of the candidates. |
| **Exact / approximate** | Exact: the model sees the same information. Approximate: it sees less, for example a pointer instead of the text. |
| **Gate** | A yes/no correctness check. A candidate that fails a gate is removed, never discounted. |
| **Observe / autopilot** | Observe: decide and log, but always do the host default. Autopilot: carry out the decision. |
| **Fail-open** | If EconoContext errors, the platform does exactly what it would have done alone. |
| **Gateway** | EconoContext's local model server: agents send their model calls to it, and it forwards them to the provider. |
| **Policy** | Omnigent's hook for tool calls and results; EconoContext's policy logs them and may replace a result. |
| **Placeholder** | A deliberately simple first version, marked `# PLACEHOLDER:` in the code, to be replaced later. |
