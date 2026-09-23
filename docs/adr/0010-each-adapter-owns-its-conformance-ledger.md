# Each adapter owns its conformance ledger

Accepted. Amends [ADR 0007](0007-adapters-share-data-not-code.md).

## Context

`conformance/actions.json` used to hold two different kinds of data in one file:

- **What every adapter shares:** the roster, and which group each corpus action is in
  (`conformance`, `expectedUnsupported`, `nullRepresentationOmitted`, `knownDivergences`,
  `degenerateOracles`).
- **How each adapter is classified against that:** `adapterUnsupported` and
  `adapterSupportedExpected` keyed by adapter, plus a `messages` map naming every adapter on each
  `expectedUnsupported` and `nullRepresentationOmitted` entry.

The second kind was 86% of the file: 337 KB, 1,006 refusal entries across 11 adapters, of which the
`reason` prose alone was 159 KB. It grows with actions × adapters, and at least five more adapters
are queued, each of which has to classify all 330 actions.

Three costs followed from the location rather than the size:

- **Every reclassification ran every workflow.** Every adapter workflow triggers on
  `conformance/**`, so re-pinning one adapter's refusal message re-ran every other adapter's CI.
  ADR 0007 already rejected exactly this for golden expectations ("Per-adapter expectations must not
  live under `conformance/`"); the classification was the one per-adapter asset it left behind.
- **Adapters landing in parallel conflicted** on one file.
- **Every loader could read every other adapter's classification**, and some validated it, so a
  roster-wide invariant was checked in several places in several languages.

## Considered options

Recorded in the prototype on branch `ai/festive-brahmagupta-7h3upj`
(`conformance/prototype-ledgers/`), which measured three directions against the real data.

### Derive each adapter's classification from refusal mechanisms × plan features

Each adapter declares a short list of mechanisms (`{message, when: [features]}`) and each action's
features are extracted mechanically from its wire fixture. Measured: the 1,006 entries become 140
induced rules plus 252 per-action pins; a held-out action is classified correctly 87% of the time,
against 71% for "assume it translates". Deferred, not rejected. The features that decide the
remaining misses are not in the plan: a constant's magnitude, and whether an adapter's mapping
treats a column as a collection. And when an action matches more than one mechanism, which message
fires depends on the adapter's walk order. It is worth revisiting as an authoring aid over the
ledgers this ADR creates, never as their source of truth.

### Tiered conformance levels with a safety-only default

Adapters declare a level; outside it they only have to be oracle-exact-or-throw, with no per-action
pinned message. Deferred: it weakens #326's per-action proof that a throw came from the declared
mechanism, and that is a policy decision, not a storage one.

### Split the per-adapter data into a file each adapter owns

Chosen. A mechanical move with no change in meaning.

## Decision

- **`conformance/actions.json` holds only what every adapter shares:** `adapters`, `conformance`,
  `expectedUnsupported` (without `messages`), `nullRepresentationOmitted` (without `messages`),
  `knownDivergences`, `degenerateOracles`.
- **Each adapter's classification is `<adapter>/conformance-ledger.json`**, with exactly six keys:
  `description`, `adapter` (its directory name), `adapterUnsupported` (`{action, reason, message}`),
  `adapterSupportedExpected` (`{action, reason}`), `expectedUnsupportedMessages` and
  `nullRepresentationOmittedMessages` (`{action: message}`).
- **Each harness reads its own ledger and nothing else's.** The derivation is unchanged; only its
  sources moved.
- **`validate-corpus.sh` owns every roster-wide invariant**: every rostered adapter has a ledger and
  every ledger belongs to a rostered adapter; closed schemas; no duplicates; `adapterUnsupported`
  names only `conformance` actions and `adapterSupportedExpected` only `expectedUnsupported` ones;
  the two message maps have exactly the right key sets. It already runs in every adapter workflow.

## Consequences

**A reclassification runs one adapter's CI.** A ledger lives in its adapter's directory, which that
adapter's workflow alone triggers on, and that workflow runs `validate-corpus.sh`, so a ledger
still cannot drift from the corpus.

**A corpus change still lands in every adapter at once, and must.** A new `expectedUnsupported` or
`nullRepresentationOmitted` action needs a message in every ledger, and `validate-corpus.sh` fails
until it has one. That PR touches `conformance/**`, so it runs every workflow anyway.

**Ledgers are still written by hand.** The classification is an output of the harness run and a
person transcribes it. A `ledger:update` command per adapter, recording what the harness observed
the way `golden:update` records filters, is the natural next step and is not part of this change.

**The roster stays in `actions.json`.** `demo/` and `validate-demo.sh` read it from there. A ledger
file is not a registration; the roster entry is.

**The Go module zips now carry a ledger.** `ent/` and `pgx/` are module roots, so their
`conformance-ledger.json` ships in the module zip, as their test files already do. It is inert
data. The npm packages and the gem use allowlists that exclude it.
