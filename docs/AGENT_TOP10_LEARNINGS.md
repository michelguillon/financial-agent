# Interview Learnings — AI Learning Track
## Top 15 insights worth saying out loud

**Michel Guillon | Weeks 1–2 + Bonus project**  
*Use this before interviews. Each entry: the headline, where it came from, what to say.*

---

## Theme 1 — What AI projects actually are

---

### 1. The product is the system. AI is the interface.

**Where:** Finance Agent — the realisation after building it  
**The pattern:**
```
Problem → System → AI
not
AI → Find a problem
```

**Say it like this:**  
"The mistake I see in most AI projects is starting with the model. I started with a problem — 15 years of transaction data I couldn't query, no way to model scenarios. The system design came second: a self-improving classification engine, a scenario modelling layer, a state store. The AI came third — it's the reasoning interface to the system, not the system itself. Once you think of it that way, model choice becomes a much smaller decision."

---

### 2. Models are tools, not the product.

**Where:** Finance Agent — the classification engine gets smarter via approved rules, not model updates  
**The pattern:** The intelligence accumulated in the `classification_rules` table. The model suggested rules. Humans approved them. The system improved permanently. The weights never changed.

**Say it like this:**  
"The most reliable classification in my system is regex rules, not LLM inference. The LLM's job is to propose a new rule when it encounters something unknown. Once a human approves that rule, it lives in a database forever. The system gets smarter; the model doesn't change. That's a very different mental model from 'better model = better system.'"

---

### 3. Grounded optimisation beats raw optimisation.

**Where:** Finance Agent — every useful answer came from 15 years of real transaction data, not model pretraining  
**The pattern:** Generic financial advice is useless. "Cut your grocery spend by 35%" is only actionable when it's based on your actual £1,150/month, not a statistical average.

**Say it like this:**  
"There's a meaningful difference between a system that can answer financial questions and a system that can answer *your* financial questions, based on *your* actual spending history. Every enterprise AI deployment faces this. General capability is not the same as relevant capability. The grounding is what makes the output trustworthy."

---

### 4. Business value comes from workflow transformation, not model choice.

**Where:** Finance Agent — the system became useful when it replaced a manual workflow  
**The pattern:** The actual workflow was: export CSVs → classify manually in Excel → model scenarios in a spreadsheet. The agent replaced that. Model selection (Sonnet vs Haiku) mattered far less than having the right workflow abstraction.

**Say it like this:**  
"I spent more time thinking about model selection than it deserved. The business value came from replacing a specific workflow — CSV exports, manual classification, spreadsheet scenarios — with something that runs in a conversation. The model choice was a cost optimisation, not a value driver. That's probably true for most enterprise deployments too."

---

## Theme 2 — Production reality

---

### 5. Measure, don't assume. The expected model lost.

**Where:** RFI Answer Builder — semantic retrieval beat hybrid retrieval across all 36 configurations  
**The pattern:** The assumption was that hybrid retrieval (semantic + BM25) would outperform pure semantic on a mixed corpus. It didn't. Running a 36-configuration evaluation matrix found the counter-intuitive result. Without the eval, the wrong architecture would have shipped.

**Say it like this:**  
"My assumption going in was that hybrid retrieval — combining semantic and keyword search — would beat pure semantic on a mixed document corpus. I was wrong. The systematic eval found that semantic retrieval won on this corpus. That's the kind of finding that only happens if you measure. It changed the architecture. The instinct was reasonable; the data was different."

---

### 6. Better narratives increase fabrication pressure.

**Where:** Finance Agent — the mortgage balance inference (£852k from a £1,420/month payment)  
**The pattern:** The model presented the inferred balance with exactly the same confidence as the correct answer in the same conversation. The more fluently AI presents analysis, the harder it is to notice when numbers are wrong.

**Say it like this:**  
"One of the most important things I observed: as the model got better at presenting financial analysis confidently, the risk of confident-but-wrong output increased. In one session, it inferred a mortgage balance of £852k from a payment amount — presented as a reasonable assumption, clearly flagged, but still wrong by a factor of two. The user corrected it. Calibration — knowing what you don't know — is the safety problem in production AI, not capability."

---

### 7. Real workflows surface different failures than test suites.

**Where:** Finance Agent — first production session revealed `get_spending_summary` returns totals, not year-by-year breakdowns  
**The pattern:** The user asked for year-by-year car spend trends. The tool couldn't produce it. This limitation was invisible in unit tests because unit tests don't ask natural questions; they test cases you thought of.

**Say it like this:**  
"The first real session found a failure mode I never anticipated in testing: the spending summary tool returns totals across the window, not year-by-year breakdowns. A real user asking a real question immediately surfaced it. Test suites test the cases you thought of. Real workflows test the cases you didn't. Getting to production faster is often the highest-value testing strategy."

---

### 8. On small, clean corpora, tuning knobs move cost more than quality.

**Where:** RAG Pipeline — 112-cell stress test across 16 embedding configurations  
**The pattern:** Varying chunk size, distance metric, and retrieval parameters had large effects on cost and latency. Quality differences were much smaller. The 100% hallucination refusal rate held across almost all configurations.

**Say it like this:**  
"I ran a 112-cell evaluation across 16 configurations of my RAG pipeline — different chunk sizes, distance metrics, retrieval parameters. On a small, clean corpus, the quality variation was surprisingly small. The cost and latency variation was large. The lesson: on focused domain corpora with good grounding, you're usually tuning for efficiency, not quality. That changes what 'evaluation' means."

---

## Theme 3 — Safety and trust

---

### 9. Human review is the strongest safety mechanism — not because models are wrong, but because approval makes outputs intentional.

**Where:** Finance Agent — every rule in `classification_rules` was human-approved  
**The pattern:** The model was usually right. The approval loop wasn't there to catch errors — it was there to make every database entry explicitly intentional. When the system was deployed publicly, this was the deeper protection. The code gate (B1) was defence-in-depth.

**Say it like this:**  
"My HITL design wasn't primarily about catching model errors — the model got the category right most of the time. It was about making every rule in the database intentional. A system where humans approved every persistent state change is a different class of trustworthy than one where outputs are probabilistically correct. That distinction matters a lot in financial or regulated contexts."

---

### 10. Privacy is an architecture decision, not a security layer.

**Where:** Finance Agent — the `data_source` column made the public demo safe without any auth system  
**The pattern:** Real data arrives via Docker volume mount, gitignored. Synthetic data is committed to the repo. The demo is safe to share publicly. One column, designed in 20 minutes, eliminated an entire class of privacy risk. Auth was explicitly out of scope by design.

**Say it like this:**  
"I separated real and synthetic data at the data layer — a single column on the transactions table — rather than building an auth system. Real data arrives as a Docker volume mount that's never in the container image. The public demo uses synthetic data automatically. Privacy was an architecture decision, not a feature. That framing changes how early you have to think about it."

---

### 11. Preview-before-apply is the right default for any state mutation in an agentic system.

**Where:** Finance Agent — started as a classification pattern, became a project-wide default  
**The pattern:** Any tool that writes to a database has a paired read-only preview: "this rule would match 12 rows, here are 3 examples." No write without a prior preview. Eventually enforced at the dispatch layer (B1) not just by prompt instruction.

**Say it like this:**  
"Any tool that mutates persistent state should have a corresponding preview tool. The preview is cheap — it's a read query. The protection it provides is significant — the user sees the blast radius before committing. I started with this for classification rules and generalised it. In production agentic systems, this pattern is worth having as a default, not a case-by-case decision."

---

## Theme 4 — Technical craft

---

### 12. Frameworks hide problems. Build once without them to understand the problem first.

**Where:** Finance Agent — built the agent loop manually before considering LangChain/LangGraph  
**The pattern:** The agent loop is ~40 lines of Python: build context, call API, execute tools if present, loop. Once you've written it, any framework becomes a known set of trade-offs. Before you've written it, frameworks are magic boxes.

**Say it like this:**  
"I deliberately built the agent loop without LangChain or LangGraph. The core loop is about 40 lines — build context, call the API, execute tools if there are tool_use blocks, inject results, loop until there aren't. Once you've done that manually, you can evaluate LangGraph from a position of understanding what it's abstracting. That's very different from adopting a framework because it's in the tutorial."

---

### 13. Session memory and cross-session memory are different problems that need different solutions.

**Where:** Finance Agent — messages array vs SQLite agent_state  
**The pattern:** The messages array handles within-session continuity (trivial, cheap, full replay). The `agent_state` table handles cross-session facts (explicit key-value store, survives restarts, correctable). Conflating them — treating long conversation history as a proxy for memory — is a common mistake.

**Say it like this:**  
"A lot of agent implementations treat memory as 'replay the full conversation history.' That works within a session. It breaks down across sessions. My finance agent uses two separate mechanisms: the messages array for within-session continuity — cheap because sessions are short — and an explicit SQLite state store for cross-session facts like mortgage rate dates and income figures. The distinction sounds obvious, but conflating them is one of the most common agent design mistakes I've seen."

---

### 14. The unplanned abstraction was more load-bearing than most planned ones.

**Where:** Finance Agent — the Renderer protocol emerged during Step 5, not in the spec  
**The pattern:** The agent loop calls `renderer.show_response()` without knowing whether it's writing to a terminal, an SSE stream, or a test fixture. This is why the web UI, transcript replay tool, and every subsequent renderer required zero changes to the core loop.

**Say it like this:**  
"The most important abstraction in my finance agent wasn't in the original spec. The Renderer protocol — separating display logic from agent logic — emerged when I was building the terminal interface. It's the reason the web UI required zero changes to the agent core, and the transcript replay tool was a two-hour job. Good architecture creates the space for discoveries like that. Over-specified architecture prevents them."

---

### 15. The tests you don't write cost more than the ones you do — just later.

**Where:** Finance Agent — retrofitting 116 tests in Phase 2 after building in Phase 1 without them  
**The pattern:** Phase 1 used verify-by-running. Phase 2 required writing tests before every feature could be safely extended. The total cost of the retrofit was higher than building incrementally would have been. Each Phase 2 feature required reasoning about test coverage from scratch rather than having it evolve naturally.

**Say it like this:**  
"I shipped Phase 1 of the finance agent without a real test suite — just manual verification. By Phase 2, every new feature required retrofitting tests before it could be extended safely. The total cost was higher than writing tests incrementally would have been. This is true at every scale. 'We'll add tests later' is a statement about future complexity, not future optionality."

---

## Quick-reference table

| # | Headline | Best for |
|---|----------|----------|
| 1 | Product is the system; AI is the interface | Opening framing, any AI strategy conversation |
| 2 | Models are tools, not the product | When asked "what model did you use?" |
| 3 | Grounded beats generic | Enterprise deployment conversations |
| 4 | Workflow transformation, not model choice | Business value / ROI questions |
| 5 | Measure, don't assume | Architecture / evaluation questions |
| 6 | Better narratives = more fabrication pressure | AI risk / safety questions |
| 7 | Real workflows surface different failures | Production readiness questions |
| 8 | Small clean corpora: tune for cost, not quality | RAG / retrieval questions |
| 9 | Human review makes outputs intentional | Governance / trust questions |
| 10 | Privacy by architecture, not security layer | Data privacy / compliance questions |
| 11 | Preview-before-apply as default | Agentic system design questions |
| 12 | Build without frameworks first | Technical depth questions |
| 13 | Session vs cross-session memory | Agent memory / state questions |
| 14 | Unplanned abstractions | System design / architecture questions |
| 15 | Late tests cost more | Engineering quality questions |
