# PROTOTYPE: per-adapter ledgers + refusal mechanisms

Throwaway. Nothing reads this directory; no harness, script or workflow depends on it. Delete it freely.

**Question.** If `actions.json`'s `adapterUnsupported` were (2) split into one generated ledger per
adapter, and then (1) collapsed into *refusal mechanisms* — `{message, when: [features]}` — joined
against features extracted mechanically from `wire-fixtures/`, how much of today's hand-authored
classification does the join reproduce, and how often is a **new** action auto-classified correctly?

```bash
python3 conformance/prototype-ledgers/prototype.py   # prints the table, writes out/ and report.html
```

Open `report.html` (self-contained) to toggle mechanisms per adapter and follow the walkthroughs.
Mechanisms are *induced* greedily (rules of ≤2 features that match no action the adapter translates),
so they stand in for what an author would write; they are not hand-curated.

## Findings (main @ 79d6f32, 317 actions, 11 adapters)

| | |
|---|---|
| `actions.json` | 344,864 B |
| shared part left after the split (roster, action list, degenerate oracles, divergences, `expectedUnsupported` without `messages`) | 23,008 B |
| per-adapter ledgers, verbatim split | 268,913 B total, largest 50,898 B (elasticsearch-java) |
| same ledgers as mechanisms + pins | 1,006 entries → 140 mechanisms + 252 pins, 90,592 B |
| new-action decisions right (5-fold CV) | 87.0% vs 71.1% for "always translate" |
| …of refusals only | 502/1,006 exact, 573 if any matching mechanism's message is accepted |
| natural experiment: #515's 6 new actions × 11 adapters | 48/66 right, 18 missed, 0 false refusals |

1. **The split (2) works as-is.** It's a mechanical move: the shared file drops to ~7% of today's size,
   and each adapter owns a file of 7–51 KB. Nothing in the data needed reshaping.
2. **Mechanisms (1) compress well where an adapter has one big limitation.** Chroma goes from 255
   entries to 20 rules + 10 pins, and one rule (`cmp-exprarg`: a comparison with a computed operand)
   covers 100 entries. Convex, ent, pgx and sqlalchemy come down to 3–7 rules.
3. **They don't remove per-action pins.** 252 refusals stay pinned, mostly messages that occur once.
   Some are genuine one-off limitations. Others are one mechanism whose message *includes the runtime
   value* (prisma: `Unsupported size comparison: size(...) eq 1.5`), so each action looks like a new
   message. Pinning a message pattern instead of a literal substring would merge them.
4. **The features matter more than the join.** Adding one feature named after the real mechanism
   (`cmp-exprarg`) cut Chroma from 29 rules to 20 and its false refusals from 5 to 1. The misses on
   #515 all depend on things the extractor doesn't see: a constant's magnitude or fractionality, and
   whether the mapping treats a column as a collection. The second is an adapter *mapping* fact, not
   a plan fact, so mapping-dependent refusals need adapter-declared features.
5. **Message precedence is real.** 71 held-out refusals match several mechanisms and are right only
   if the harness accepts any matching message. The thrown message depends on walk order, which a
   flat feature join can't see.
6. **Every error is loud.** A false refusal or missed refusal fails the harness, as a wrong hand
   classification does today. So a derived ledger can't silently cost safety, only CI round trips.

**Verdict.** Do (2) now: it's safe, mechanical, and removes the fan-out. Do (1) as an *authoring aid*,
not the source of truth. `ledger:update` should record each adapter's refusals and group them by
mechanism, and a derived pre-classification can pre-fill new actions (~87% right). The recorded
ledger stays authoritative, because features that capture both value facts and mapping facts are
the unsolved part.
