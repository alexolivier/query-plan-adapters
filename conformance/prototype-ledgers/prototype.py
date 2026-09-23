#!/usr/bin/env python3
"""PROTOTYPE, throwaway. Not wired into any harness or CI.

Question: if actions.json's adapterUnsupported were (2) split into per-adapter ledgers and
(1) collapsed into refusal *mechanisms* keyed on shape features extracted mechanically from
wire-fixtures/, how much of today's hand-authored classification would the join reproduce --
and, crucially, how often would a NEW action be auto-classified correctly?

Run:  python3 conformance/prototype-ledgers/prototype.py
Writes out/ (ledgers, features, mechanisms, results.json) and report.html next to this file.
"""
import json, os, random, collections, itertools

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out")
D = json.load(open(os.path.join(CONF, "actions.json")))
ADAPTERS = D["adapters"]
UNIVERSE = sorted(D["conformance"])
IDX = {a: i for i, a in enumerate(UNIVERSE)}

# ---------------------------------------------------------------- (1a) feature extraction
def vtype(v):
    if v is None: return "null"
    if isinstance(v, bool): return "bool"
    if isinstance(v, int): return "int"
    if isinstance(v, float): return "float" if v != int(v) else "floatint"
    if isinstance(v, str): return "str"
    if isinstance(v, list): return "emptylist" if not v else "list"
    if isinstance(v, dict): return "map"
    return "other"

def attr_of(path):
    p = path.replace("request.resource.attr.", "R.").replace("request.principal.attr.", "P.")
    return p

def features(fixture):
    f = set()
    flt = fixture["filter"]
    f.add("kind:" + flt["kind"])
    cond = flt.get("condition")
    if cond is None:
        return f
    maxdepth = [0]

    def kind_of(n):
        if "expression" in n: return "expr:" + n["expression"]["operator"]
        if "variable" in n: return "var"
        return "val:" + vtype(n.get("value"))

    def walk(n, parent, depth):
        maxdepth[0] = max(maxdepth[0], depth)
        if "variable" in n:
            a = attr_of(n["variable"])
            f.add("attr:" + a)
            if a.startswith("R.") and a.count(".") >= 2: f.add("hop")
            if a.startswith("P."): f.add("principal-var")
            if parent: f.add(f"var-under:{parent}")
            return
        if "value" in n:
            f.add("val:" + vtype(n["value"]))
            if isinstance(n["value"], list):
                for t in {vtype(x) for x in n["value"]}: f.add("listof:" + t)
                if len({vtype(x) for x in n["value"]}) > 1: f.add("list-mixed")
            return
        e = n["expression"]; op = e["operator"]; ops = e.get("operands", [])
        f.add("op:" + op)
        if parent: f.add(f"nest:{parent}>{op}")
        else: f.add("root:" + op)
        kinds = [kind_of(o) for o in ops]
        f.add(f"sig:{op}({','.join(k if not k.startswith('expr:') else 'expr' for k in kinds)})")
        if len(ops) == 2 and all(k == "var" for k in kinds): f.add("f2f:" + op); f.add("f2f")
        if len(ops) == 2 and kinds[0].startswith("val:") and kinds[1] == "var": f.add("valfirst:" + op)
        # iteration 2: name the mechanism adapters actually refuse on -- "an operand is itself a computation"
        if any(k.startswith("expr:") for k in kinds) and op not in ("and", "or", "not"):
            f.add("exprarg:" + op)
            if op in ("eq", "ne", "lt", "le", "gt", "ge", "in"): f.add("cmp-exprarg")
        for o, k in zip(ops, kinds):
            if k == "var": f.add(f"opattr:{op}:{attr_of(o['variable'])}")
        for o in ops: walk(o, op, depth + 1)

    if "variable" in cond and "expression" not in cond:
        f.add("root:var")
    walk(cond, None, 0)
    for k in (2, 3, 4, 5):
        if maxdepth[0] >= k: f.add(f"depth>={k}")
    return f

FX = {}
for a in UNIVERSE:
    FX[a] = features(json.load(open(os.path.join(CONF, "wire-fixtures", a + ".json"))))
ALLF = sorted(set().union(*FX.values()))
FBITS = {feat: 0 for feat in ALLF}
for a, fs in FX.items():
    for feat in fs: FBITS[feat] |= 1 << IDX[a]

def bits(actions): 
    b = 0
    for a in actions: b |= 1 << IDX[a]
    return b
def members(b): return [UNIVERSE[i] for i in range(len(UNIVERSE)) if b >> i & 1]
pc = int.bit_count

# ---------------------------------------------------------------- (1b) mechanism induction
def induce(refused, translated, train_mask):
    """refused: {action: message} (training only). translated: bitset (training only).
    Returns mechanisms [{message, when:[features], matches:bitset}] + residual per-action pins.
    A rule is SAFE when it matches no training action the adapter translates."""
    groups = collections.defaultdict(list)
    for a, m in refused.items(): groups[m].append(a)
    mechs, residual = [], {}
    for msg, acts in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        uncovered = bits(acts)
        cand_feats = set().union(*(FX[a] for a in acts))
        singles = [((f,), FBITS[f]) for f in sorted(cand_feats)]
        pairs = set()
        for a in acts:
            for p in itertools.combinations(sorted(FX[a]), 2): pairs.add(p)
        cands = [c for c in singles if not (c[1] & train_mask & translated)]
        cands += [(p, FBITS[p[0]] & FBITS[p[1]]) for p in sorted(pairs)
                  if not (FBITS[p[0]] & FBITS[p[1]] & train_mask & translated)]
        while uncovered and cands:
            best = max(cands, key=lambda c: (pc(c[1] & uncovered), -len(c[0]), -pc(c[1] & train_mask)))
            gain = pc(best[1] & uncovered)
            if gain < 2:  # a rule that explains one action is just a pin with extra steps
                break
            mechs.append({"message": msg, "when": list(best[0]), "matches": best[1]})
            uncovered &= ~best[1]
        for a in members(uncovered): residual[a] = msg
    return mechs, residual

def derive(mechs, residual, action):
    """The join: which message (if any) does this adapter refuse `action` with?"""
    if action in residual: return residual[action]
    hits = [m for m in mechs if m["matches"] >> IDX[action] & 1]
    if not hits: return None
    # precedence = most specific-in-training first (fewest matches), a stand-in for author order
    return min(hits, key=lambda m: (pc(m["matches"]), -len(m["when"])))["message"]

# ---------------------------------------------------------------- run
os.makedirs(os.path.join(OUT, "ledgers"), exist_ok=True)
results = {"adapters": {}, "features": len(ALLF)}
sizes = {"actions.json": os.path.getsize(os.path.join(CONF, "actions.json"))}

shared = {k: v for k, v in D.items() if k != "adapterUnsupported"}
shared["expectedUnsupported"] = [{k: v for k, v in e.items() if k != "messages"} for e in D["expectedUnsupported"]]
shared["nullRepresentationOmitted"] = [{k: v for k, v in e.items() if k != "messages"} for e in D["nullRepresentationOmitted"]]
shared.pop("adapterSupportedExpected")
json.dump(shared, open(os.path.join(OUT, "shared-actions.json"), "w"), indent=2)
sizes["shared-actions.json"] = os.path.getsize(os.path.join(OUT, "shared-actions.json"))

random.seed(7)
folds = list(UNIVERSE); random.shuffle(folds)
K = 5
FOLD = {a: i % K for i, a in enumerate(folds)}
FULL = (1 << len(UNIVERSE)) - 1

for ad in ADAPTERS:
    ent = D["adapterUnsupported"][ad]
    refused = {e["action"]: e["message"] for e in ent}
    translated = FULL & ~bits(refused)

    # (2) the split ledger: verbatim, reasons deduplicated by message
    reasons = collections.defaultdict(list)
    for e in ent:
        if e["reason"] not in reasons[e["message"]]: reasons[e["message"]].append(e["reason"])
    ledger = {
        "adapter": ad,
        "generatedBy": "ledger:update (PROTOTYPE: split verbatim from actions.json)",
        "expectedUnsupportedMessages": {e["action"]: e["messages"][ad] for e in D["expectedUnsupported"] if ad in e["messages"]},
        "nullRepresentationOmittedMessages": {e["action"]: e["messages"][ad] for e in D["nullRepresentationOmitted"]},
        "adapterSupportedExpected": D["adapterSupportedExpected"].get(ad, []),
        "reasons": reasons,
        "refuses": {e["action"]: e["message"] for e in sorted(ent, key=lambda e: e["action"])},
    }
    lp = os.path.join(OUT, "ledgers", f"{ad}.json"); json.dump(ledger, open(lp, "w"), indent=2)

    # (1) mechanisms on the full data
    mechs, residual = induce(refused, translated, FULL)
    mech_doc = {
        "adapter": ad,
        "mechanisms": [{"message": m["message"], "when": m["when"], "reason": reasons[m["message"]][0],
                        "matchesToday": len(members(m["matches"]))} for m in mechs],
        "pins": [{"action": a, "message": msg} for a, msg in sorted(residual.items())],
    }
    mp = os.path.join(OUT, "ledgers", f"{ad}.mechanisms.json"); json.dump(mech_doc, open(mp, "w"), indent=2)
    full_msg_ok = sum(derive(mechs, residual, a) == refused.get(a) for a in UNIVERSE)

    # generalisation: K-fold, each held-out action plays "a brand-new corpus action"
    cv = collections.Counter(); cv_cases = []
    for k in range(K):
        train = bits([a for a in UNIVERSE if FOLD[a] != k])
        tr_ref = {a: m for a, m in refused.items() if FOLD[a] != k}
        m_k, r_k = induce(tr_ref, translated, train)
        for a in UNIVERSE:
            if FOLD[a] != k: continue
            got, want = derive(m_k, {}, a), refused.get(a)
            if got == want: o = "exact" if want else "exact-translate"
            elif want is None: o = "false-refuse"
            elif got is None: o = "missed-refusal"
            elif want in {m["message"] for m in m_k if m["matches"] >> IDX[a] & 1}: o = "message-in-matching-set"
            else: o = "wrong-message"
            cv[o] += 1
            if o not in ("exact", "exact-translate", "message-in-matching-set"): cv_cases.append({"action": a, "outcome": o, "derived": got, "actual": want})

    results["adapters"][ad] = {
        "entriesToday": len(ent), "messages": len(reasons),
        "mechanisms": len(mechs), "pins": len(residual),
        "fullReproduction": {"correct": full_msg_ok, "of": len(UNIVERSE)},
        "ledgerBytes": os.path.getsize(lp), "mechanismBytes": os.path.getsize(mp),
        "newActionCV": dict(cv), "newActionMisses": cv_cases,
        "mechanismsDoc": mech_doc,
    }

results["sizes"] = sizes
results["actionFeatures"] = {a: sorted(FX[a]) for a in UNIVERSE}
results["refusedToday"] = {ad: {e["action"]: e["message"] for e in D["adapterUnsupported"][ad]} for ad in ADAPTERS}
json.dump(results, open(os.path.join(OUT, "results.json"), "w"), indent=1)

# ---------------------------------------------------------------- summary
print(f"features extracted: {len(ALLF)} over {len(UNIVERSE)} actions")
print(f"actions.json today: {sizes['actions.json']:,} B   shared part after split: {sizes['shared-actions.json']:,} B")
print(f"{'adapter':20}{'entries':>8}{'msgs':>6}{'mechs':>7}{'pins':>6}{'ledgerB':>9}{'mechB':>8}  new-action CV: exact / false-refuse / missed / wrong-msg")
T = collections.Counter()
for ad, r in results["adapters"].items():
    c = r["newActionCV"]; T.update(c)
    ex = c.get("exact", 0) + c.get("exact-translate", 0) + c.get("message-in-matching-set", 0)
    print(f"{ad:20}{r['entriesToday']:>8}{r['messages']:>6}{r['mechanisms']:>7}{r['pins']:>6}{r['ledgerBytes']:>9,}{r['mechanismBytes']:>8,}"
          f"  {ex:>4} / {c.get('false-refuse',0):>3} / {c.get('missed-refusal',0):>3} / {c.get('wrong-message',0):>3}")
tot = sum(T.values()); ex = T["exact"] + T["exact-translate"] + T["message-in-matching-set"]
print(f"  (of which {T['message-in-matching-set']} are right only if the harness accepts any matching mechanism's message)")
print(f"TOTAL new-action decisions {tot}: auto-correct {ex} ({100*ex/tot:.1f}%), "
      f"false-refuse {T['false-refuse']}, missed-refusal {T['missed-refusal']}, wrong-message {T['wrong-message']}")
R = T['exact']+T['message-in-matching-set']+T['missed-refusal']+T['wrong-message']
print(f"of refusals only: {T['exact']} exact, {T['exact']+T['message-in-matching-set']} with set-of-messages rule, of {R}")
print(f"baseline 'always translate': {tot-R}/{tot} ({100*(tot-R)/tot:.1f}%)")

# ---------------------------------------------------------------- natural experiment
# The six actions origin/main added in 79d6f32 (#515), after this prototype was first run. Train on
# everything else and predict them for every adapter: the exact situation of a new action landing.
NEW = ["size-frac-eq-not", "size-frac-ne-not", "size-huge-gt-not", "size-huge-lt-not", "size-huge-neg-gt-not", "size-huge-neg-lt-not"]
if all(a in IDX for a in NEW):
    train = FULL & ~bits(NEW); nat = collections.Counter(); natrows = []
    for ad in ADAPTERS:
        refused = {e["action"]: e["message"] for e in D["adapterUnsupported"][ad]}
        m_k, _ = induce({a: m for a, m in refused.items() if a not in NEW}, FULL & ~bits(refused), train)
        for a in NEW:
            got, want = derive(m_k, {}, a), refused.get(a)
            hit = {m["message"] for m in m_k if m["matches"] >> IDX[a] & 1}
            o = "right" if got == want or (want and want in hit) else ("false-refuse" if not want else "missed" if not got else "wrong-message")
            nat[o] += 1; natrows.append((ad, a, o, want, got))
    print(f"natural experiment (#515's 6 actions x {len(ADAPTERS)} adapters): {dict(nat)}")
    for r in natrows:
        if r[2] != "right": print("   ", r[0], r[1], r[2], "| actual:", (r[3] or "translates")[:70], "| derived:", (r[4] or "translates")[:50])
    results["natural"] = {"counts": dict(nat), "rows": natrows}

# ---------------------------------------------------------------- report.html (thin shell over results)
novel = sum(1 for ad in ADAPTERS for a, m in results["refusedToday"][ad].items()
            if collections.Counter(results["refusedToday"][ad].values())[m] == 1)
results["totals"] = {
    "entries": sum(r["entriesToday"] for r in results["adapters"].values()),
    "mechs": sum(r["mechanisms"] for r in results["adapters"].values()),
    "pins": sum(r["pins"] for r in results["adapters"].values()),
    "autoPct": round(100 * ex / tot, 1), "basePct": round(100 * (tot - R) / tot, 1),
    "refExact": T["exact"], "refSet": T["exact"] + T["message-in-matching-set"], "refTotal": R, "novel": novel,
}
page = {k: results[k] for k in ("adapters", "sizes", "actionFeatures", "refusedToday", "totals")}
page["adapters"] = {a: {k: v for k, v in r.items() if k != "newActionMisses"} for a, r in results["adapters"].items()}
tpl = open(os.path.join(HERE, "report.template.html")).read()
open(os.path.join(HERE, "report.html"), "w").write(tpl.replace("/*DATA*/null", json.dumps(page, separators=(",", ":"))))
print("wrote", os.path.join(HERE, "report.html"))
