Read [](file:///d%3A/git/sp-workspace/saferplaces-multiagent/src/saferplaces_multiagent/ma/chat/request_parser.py)

The code **already uses** OpenAI Structured Outputs — that's exactly what this line does:

```python
self.llm = _base_llm.with_structured_output(ParsedRequest)
```

LangChain's `.with_structured_output(ParsedRequest)` converts the `ParsedRequest` Pydantic model into the `response_format` JSON Schema parameter sent to the OpenAI API, enforcing the schema at the API level.

The natural-language system prompt is **supplementary guidance** — it tells the model *how to populate* the fields (e.g., "don't hallucinate", "extract only explicit parameters"). The schema enforces *shape*, the prompt guides *behavior*. They serve different roles:

| | System Prompt | `with_structured_output` |
|---|---|---|
| **Role** | Semantic guidance | Schema enforcement |
| **Enforces structure?** | No | Yes (API-level) |
| **Guides field meaning?** | Yes | Only via `Field(description=...)` |

If you want to reduce prompt verbosity, you can move guidance into `Field` descriptions on `ParsedRequest` and simplify the system prompt — the structured output guarantee stays regardless. For example, the request_parser.py line `self.llm = _base_llm.with_structured_output(ParsedRequest)` is the actual Structured Outputs call; the prompt text shown in T002.md is just the human-readable instructions layered on top.

---

Fetched https://raw.githubusercontent.com/openai/skills/refs/heads/main/skills/.curated/doc/SKILL.md

That SKILL.md format is a **skill routing/documentation file** for an OpenAI agent orchestrator — it tells the *orchestrator* when to invoke the skill and what it does. It's not a system prompt pattern.

For the `RequestParser`, the comparison breaks down like this:

| SKILL.md concept | RequestParser equivalent |
|---|---|
| `name` / `description` (front-matter) | Already implicit in the node name `RequestParser` |
| `## When to use` | Not needed — node is always called first in the graph |
| `## Workflow` | The bullet list in `SYSTEM_REQUEST_PROMPT` |
| Schema enforcement | `with_structured_output(ParsedRequest)` |

The SKILL.md format would be **useful in a different place** in your codebase — specifically in the **SupervisorAgent**, where the LLM needs to decide *which agent to route to*. That's exactly the "when to use" problem SKILL.md solves.

In `request_parser.py`, the current prompt is already well-suited: it's purely behavioral instructions for a single-purpose node. The SKILL.md format would add overhead without benefit there.

Where it **would** help: if you defined each specialized agent (layers, models, safercast) using a SKILL.md-style descriptor, the SupervisorAgent's prompt in supervisor.py could inject those descriptors as the "agent registry", giving the LLM richer "when to use" context per agent rather than just names.