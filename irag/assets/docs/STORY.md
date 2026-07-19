# The Story

*How a Database Systems assignment became irag — by Karan Garg.*

I had a Database Systems assignment coming up and honestly had no idea
what to build. Around then, Andrej Karpathy put out his LLM Wiki, so I
started tinkering with it and found the issues fast: it was all just
files — nothing versioned or checked, nothing stopping it from quietly
going stale while people kept trusting it. I figured I'd try fixing
that for the assignment.

I picked a law firm as the domain because it sounded fun to build, and
it turned out to be the right kind of hard too. Law firms don't forgive
mistakes — a wrong court date or a missed conflict of interest is a
real failure, not a bad chatbot answer. Building under that pressure
got me to the actual idea:

> **It should be a database system with an AI layer, not an AI system
> with a database attached.**

Five tables did the real work underneath — pages, revisions, links,
events, contradictions — while the database handled the bookkeeping and
the AI only ever wrote prose. I tested it by imagining the AI ripped
out completely: a full working law-firm system was still standing
there.

That's when it clicked that those five tables were never about law
firms at all. They were an answer to something bigger: **how do you let
an AI write things you can actually trust?** So I stripped the law-firm
parts out and pointed the same core at a problem that had been bugging
me the whole time — coding agents that forget everything the second you
close the terminal.

Around then I noticed tools like Graphify and claude-mem popping up
too — other people circling the same problem from different angles. It
felt like confirmation I was onto something real.

That became irag. One SQLite file in your repo — no service, no cloud,
no keys. The agent stops grepping around; its map, search, and context
are SQL now, not more model calls. Memory that can't quietly lie —
every claim gets checked against the real code and gated in CI. Reads
cost next to nothing: a few thousand tokens of briefing instead of
re-exploring everything. A new chat resumes an old project in about a
hundred and fifty tokens, like no time passed.

---

Where to go from here:

- [Quickstart](https://karang1908.github.io/iRag/docs/quickstart/) —
  running in five minutes
- [Architecture](ARCHITECTURE.md) — the five tables, all grown up
- [Comparison](COMPARISON.md) — vs Graphify and claude-mem, honestly
