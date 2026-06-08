# AGENT_RETROSPECTIVE.md — Personal Finance Agent
## Project Retrospective

**Michel Guillon | Week 2 AI Learning Track | Completed: 2026-06-02**

---

## What I Thought I Was Building

A personal finance chatbot. Classify my bank transactions automatically, ask it questions about my spending, get answers. Maybe model a "what if" scenario or two.

The framing in my head at the start:

> *"An AI agent that handles the boring parts of personal finance management."*

---

## What I Actually Built

A **multi-agent decision-support system** that combines retrieval, evaluation, grounding, orchestration, and human review — running against 15 years of real financial behaviour.

More precisely:

> *"A self-improving classification engine that learns from human-approved rules, layered with a grounded scenario modelling system, connected through an orchestration loop that accumulates knowledge across sessions, deployed with a public demo and a private real-data mode behind a cost-capped web UI."*

The difference between those two descriptions is the project.

---

## The Real Framing

The most important thing I didn't write down at the start, but should have:

```
Problem
   ↓
System
   ↓
   AI
```

Not:

```
   AI
   ↓
Find a problem
```

The problem came first: 15 years of transaction data locked in Excel, brittle manual categorisation that broke on unknown merchants, no way to model financial scenarios grounded in real spending patterns, no memory between sessions.

The system came second: a classification engine that self-improves through human-approved rules, a state store that accumulates facts across sessions, a scenario modelling layer that queries real history rather than producing generic financial advice.

The AI came third: a reasoning and natural language interface layer that decides which tools to call, handles uncertainty honestly, and synthesises data into answers a human can act on.

**The product is the system. The AI is the interface to the system.**

This distinction sounds obvious in retrospect. It wasn't obvious at the start. Every week the planning frame was "what AI patterns will I learn?" rather than "what problem does this solve and what does the solution look like?" The projects where this was reversed — where the problem drove the system design, which then identified where AI adds value — produced substantially better results.

---

## Top 10 Biggest Learning Shifts

### 1. Models are tools, not the product

The most reliable classification in the system is regex rules against transaction memos — not LLM inference. The LLM's job was to *suggest* rules when it encountered unknown patterns. On approval, the rule goes into the database. The system gets smarter permanently; the model didn't change.

The insight: intelligence was accumulated in the rules table, not in the model weights. The model was the reasoning interface to a knowledge base. This reframing changed how I think about every subsequent project.

### 2. Grounded optimisation beats raw optimisation

A generic financial advisor is useless. An advisor with 15 years of your actual spending history is useful. Every answer that was genuinely actionable — "cut groceries from £1,150 to £574 to close the mortgage gap" — came from the data layer, not from the model's pretraining. The model could only be as useful as the data it reasoned over.

This generalises: AI systems built on top of real, domain-specific data substantially outperform systems that rely on model pretraining for domain knowledge.

### 3. Human review is not a bottleneck — it is the product

The HITL gate on rule addition was the most important architectural decision in the project. Not because the model was often wrong (it was usually right). Because every rule in the `classification_rules` table was explicitly reviewed and approved. The database was intentional, not probabilistic.

When the system was exposed publicly (C4), the code gate (B1) was added. But the deeper protection was always the approval loop. The public URL made B1 urgent; the approval loop made the system trustworthy.

### 4. Better narratives increase fabrication pressure

The more fluently the model presented financial analysis, the harder it was to notice when numbers were wrong. The mortgage balance inference in the production session — £852k derived from a £1,420 monthly payment — was presented with exactly the same confidence as the correct answer. Calibration, not capability, is the safety problem in production financial AI.

The lesson: the ability to say "I don't know the balance, please confirm" is a feature, not a limitation. Designing the system prompt to surface uncertainty rather than suppress it was the right call.

### 5. The Renderer protocol was the most important abstraction

It wasn't in the original spec. It emerged during Step 5 as a way to separate display logic from agent logic. The agent loop calls `renderer.show_response()` without knowing whether it's talking to a terminal, a web SSE stream, or a test fixture. This is the reason the web UI (C4), transcript replay (D2), and every subsequent renderer required zero changes to the core loop.

The unplanned abstraction was more load-bearing than most planned ones. The spec was right about the problems; the code discovered the solutions.

### 6. Frameworks hide problems; understanding the problem is what matters first

Not using LangChain meant implementing the tool_use / tool_result cycle manually. That forced a concrete understanding of what an agent loop actually is: build context, call API, execute tools if present, loop until text response. About 40 lines of Python. Once you've written it, any framework becomes a known set of trade-offs rather than a black box. You know what LangGraph is abstracting, and therefore when the abstraction is worth it.

### 7. Session memory and cross-session memory are different problems that need different solutions

Conflating them — treating long conversation history as a proxy for memory — is a common mistake. The messages array handles within-session continuity cheaply and correctly. The `agent_state` table handles cross-session facts explicitly. Each tool that writes to state gives a rationale. Keeping the two concerns separate made both cleaner and more debuggable.

### 8. Demo mode is a privacy architecture decision, not a UX decision

The `data_source` column (synthetic vs real) was designed in one sitting. It meant the public demo was safe to share without exposing real financial data. Real data arrives via volume mount; synthetic data is committed to the repo. Zero code change at runtime. Privacy was an architectural primitive, not an afterthought feature.

This generalises: "who can see what data" should be an architecture decision made at the start, not a security layer added when deployment becomes imminent.

### 9. The preview-before-apply pattern is the right default for any state mutation

Initially added only for classification rules. After C4 exposed the system publicly, promoted to a project-wide default for all `apply_*` tools via the code gate (B1). The pattern is: `preview_*` (read-only, shows what would happen) before `apply_*` (writes to database). No `apply_*` without a preceding `preview_*` in the conversation history.

This generalises beyond this project. Any agentic system that mutates persistent state should have a preview step. The implementation cost is low; the safety benefit is significant.

### 10. The tests you don't write cost more than the ones you do

The Phase 2 testing strategy flip — from verify-by-running to 116 pytest tests — was correct but painful. Retrofitting tests against a working system took more time than writing them incrementally would have. Each Phase 2 ticket (A1, B1, C4) required reasoning about what the tests needed to cover rather than having them evolve naturally alongside the code.

The discipline of building test infrastructure in parallel with feature development is worth the upfront cost. "I'll add tests later" is a statement about future complexity, not future optionality.

---

## What I'd Do Differently

*This is the section worth spending time on.*

### 1. Build the real-data pipeline first, not last

C1 (real-data ingestion) was the last major feature shipped. It found three latent bugs in `budget_importer.py` that had been silently corrupting categorisation. It should have been Step 2 — immediately after the SQLite migration — so every subsequent decision was based on validated real data rather than synthetic data that looked plausible.

The general principle: if the system is ultimately grounded in real data, validate the real-data path before building anything that depends on data quality.

### 2. Separate public visibility concerns from mode configuration at design time

The demo mode switch and the web UI cost cap were designed independently and assembled late. They address the same question — "what does a public user get?" — from different angles. That question should be an explicit first-class architecture decision: one document, one mental model, designed together. Both features worked. They just required more assembly than they should have.

### 3. Introduce session and run management before the first API call

Session identity, transcript logging, and cost tracking were retrofitted to the agent loop after the fact. Session management should be infrastructure, not a feature. Building it first means every subsequent API call is automatically instrumented. Building it last means every subsequent call needs to be touched again.

### 4. Measure classification accuracy before building scenarios

The scenario modelling was built on top of classified data before classification quality was validated on real transactions. The three bugs in C1 were found because we finally ran the real pipeline. Validating the data foundation before building reasoning layers that depend on it would have been both faster and safer.

The pattern: in any system where reasoning quality depends on data quality, measure data quality first.

### 5. The taxonomy should have been a database table from day one

The hardcoded `if/elif` chain in `bank_statement_parser.py` was technical debt at birth. Migrating it to `classification_rules` (A1), expanding the taxonomy (A2), and building agent-extensible tools (A3) were the right moves — but they were always deferred and always more expensive than they would have been if the taxonomy had been a data schema from Step 1. The original spec knew this (it planned Phase 2 / A1). It was still deferred. The lesson: "we'll migrate this later" is a tax on every subsequent feature.

---

## For the Portfolio

The interview framing that captures the project accurately:

> *"I built a personal finance system where the AI is the reasoning interface to a structured knowledge base built from 15 years of my own transaction data. The classification engine self-improves through human-approved rules. The scenario modelling is grounded in real spending patterns, not generic financial models. The project demonstrated the patterns that matter in production: human oversight, data grounding, session state management, and the discipline of building systems that get smarter over time rather than relying on model capability to compensate for weak data."*

What this project demonstrated that is worth saying out loud:
- The difference between a chatbot and a decision-support system
- That human oversight is an architecture decision, not a bolt-on
- That AI value comes from the quality of the system it's embedded in, not from the model itself
- That building without a framework is how you understand what frameworks are doing
