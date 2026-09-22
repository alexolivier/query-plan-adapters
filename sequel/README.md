# Cerbos Sequel Query Plan Adapter

> [!WARNING]
> **A work-in-progress prototype. Do not use this to enforce access control in a live system.**
>
> - **Not released.** No version of this gem is on RubyGems. Version `0.1.0` is a placeholder.
> - **No real-world use.** Nobody runs this in production. Every result below comes from the
>   test corpus in this repository, and a corpus is not a deployment: it cannot find the shapes
>   of policy, schema and mapping that real applications have and this one has never seen.
> - **The interface can change without warning.** Method names, arguments and the shapes that
>   the adapter accepts or refuses can all still change, and there is no deprecation cycle
>   until a first release.
> - **The mapping is yours to get right.** The conformance results below prove the
>   *translation*. They cannot prove that your attribute map points at the rows your
>   application put into the Cerbos attributes — see [Mapping hazards](#mapping-hazards). A
>   mistake there is an authorization bug that no test in this repository can see.
>
> Read it, try it, and report what breaks. Do not put it in front of your data yet.

An adapter that changes a [Cerbos](https://cerbos.dev) query plan (`PlanResources`) into a
filtered [Sequel](https://sequel.jeremyevans.net) `Dataset`. Thus the database applies the
authorization rules from your Cerbos policies, and your application code does not.

The result is a usual dataset of your model. Thus you can add filters, an order, pagination
and eager loading to it:

```ruby
documents = Cerbos::Sequel.query_plan_to_dataset(
  plan: plan, model: Document, attributes: MAPPING
)

documents.where(archived: false).order(:created_at).limit(20).all
```

## The adapter is fail-closed

If the adapter cannot translate a shape of plan correctly, it **raises an error**. It does not
give a filter that is only approximately correct. This is the primary guarantee of the adapter.
An incorrect filter is an authorization bug, because it gives rows that the PDP denies. An
error is a bug report.

The adapter never changes an operator into a weaker operator. It never changes `exists_one`
into `exists`. If it cannot escape a `LIKE` needle, it never lets the wildcards stay.

### Conformance contract

**Compatibility:** constant NaN ordering follows Cerbos 0.55: an unordered comparison is
false, so its negation is true. Missing attributes and other evaluation errors keep the row out
under both polarities.

The corpus is verified against Cerbos 0.55.0 with strict evaluation both disabled and enabled.
The live conformance harness accepts `ADAPTER_TEST_STRICT_EVALUATION=false` (the default) or
`true`, and rejects other values. CI runs both modes against the same corpus, comparing each
plan with `check()` decisions from a PDP configured with that same mode.

The tests compare this adapter with the PDP pinned in `../conformance/CERBOS_VERSION` and
`../conformance/CERBOS_IMAGE_DIGEST`. For each action, the test makes a plan with a real PDP,
translates the plan, runs the query against 27 difficult rows, and compares the ids in the
result with the decisions of `checkResource` for each row. The PDP gives the results for both
sides. No person writes the expected results. The Spring Data adapter gives the reference
behaviour.

A second suite, `spec/translator_spec.rb`, replays the same corpus OFFLINE from
`../conformance/wire-fixtures/` and pins the SQL this adapter emits for each action in
[`golden/expectations.json`](golden/expectations.json). It needs no PDP and no database
server. Rewrite it with `./scripts/golden-update.sh` and review the diff.

| Classification | Coverage |
| --- | --- |
| Tested against the oracle | 226 corpus actions, on SQLite, PostgreSQL and MySQL |
| Fail-closed | 73 actions: 62 that this adapter cannot show, and the 11 that the reference adapter does not support either. Each one must raise an error whose message the corpus pins, so a typo or a transport error cannot pass as the refusal |
| Refused under the `omitted` NULL convention | 1 action — see [The NULL convention of the caller](#the-null-convention-of-the-caller) |
| Known difference in the planner | The Cerbos planner changes `has()` on a missing attribute into `ALWAYS_ALLOWED`, but `checkResource` denies the rows in which the attribute is missing. Until the planner has a correction, use `R.attr.x != null` and not `has(R.attr.x)` for the attributes in your database |

The fail-closed set is small, because SQL can show most of the corpus directly. The adapter
makes `LIKE` with an ESCAPE clause. It makes correlated `COUNT` subqueries for the relation
counts and for `exists_one`. The database calculates the arithmetic on columns and the lengths
of the strings; arithmetic with a fractional constant runs in doubles, as CEL's does. `string()`
over a boolean column becomes a `CASE` that spells `'true'` and
`'false'`, because `CAST(col AS TEXT)` gives `"1"` on SQLite and MySQL. These shapes stay:

| Action | Why the adapter raises an error |
| --- | --- |
| `ts-window`, `ts-vf` | The planner makes a `now()` literal with nanoseconds. Sequel puts a `Time` into SQL with microseconds. Thus the query would compare with a different instant from the instant in the policy. |
| `cr-div-other-column` | A division whose denominator is a second column. IEEE-754 keeps the sign of a zero, and `2.0 / -0.0` is -Infinity while `2.0 / 0.0` is +Infinity. SQL cannot tell `-0.0` from `0.0`, so the sign of the Infinity is unknown. A division of a value by ITSELF stays safe, and so does a constant denominator, whose sign the plan carries. |
| `cr-div-then-add`, `cr-div-then-add-ne` | More arithmetic on the result of a division that can give a value which is not finite. SQL has no NaN and no signed Infinity to bind, so the adapter keeps such a division as branches until a comparison resolves them, and an addition on those branches has no SQL form. |
| `p-matches`, `matches-alt`, `regex-*` | `matches()` uses RE2. No SQL dialect gives the behaviour of RE2, and `LIKE` cannot show a regular expression. |
| `p-index`, `index-*` | `tags[0]` selects an element of a list by its position. An association has no order of its own, so `index` has no case in the operator dispatch. A caller whose table has a deterministic ordering column can supply an operator override. |
| `p-timestamp`, `cast-not-timestamp` | `timestamp()` on a column that holds a timestamp in text. Map the attribute to a `DateTime` column. |
| `cast-int-string`, `cast-double-string`, `cast-not-int`, `cast-not-double` | `int()` and `double()` over a text column. CEL reads the WHOLE string or makes an error, but SQL reads the digits at the front: `CAST('1junk' AS INTEGER)` is `1` on SQLite. |
| `cast-int-double` | `int()` over a double column. CEL removes the fraction toward zero. PostgreSQL and MySQL round a `CAST` to the nearest whole number. |
| `filter-as-condition`, `filter-as-conjunct`, `map-as-condition` | A `filter()` or a `map()` where a boolean belongs. Only `size(filter(...))` or `hasIntersection(map(...), [...])` has a boolean meaning. |
| `temporal-raw-eq` | Two temporal columns compared without `timestamp()`. CEL compares their RFC-3339 spellings there, and SQL compares instants. |
| `type-*` | `size()`, `contains`, `startsWith`, `endsWith` and `hierarchy()` over a numeric or boolean column. CEL has no such overload, and SQL would coerce the value to text — or, on PostgreSQL, reject the `LIKE`. |
| `eq-list`, `ne-list`, `map-eq-list` | A whole collection compared with `==`. A correlated subquery has no ordered list to compare element by element. |
| `in-nested-list`, `in-list-element`, `eq-map*`, `ne-map`, `pv-structs*`, `hasint-map-element`, `pv-except`, `except-*`, `pv-filter`, `pv-map` | A list or a map as a list element, a struct built in the policy, a list difference, or `filter`/`map` over a list of constants. None has a scalar SQL form. |
| `hier-empty-delim` | A hierarchy with an empty delimiter. The adapter refuses it before it builds the prefix `LIKE`, which would also match the path itself. |
| `null-value-f2f-mixed` | A comparison between two columns under different NULL conventions — see below. |

The exact message each refusal raises is pinned in
[`conformance/actions.json`](../conformance/actions.json) under `sequel`.

The adapter also raises an error for a plan whose `and` or `or` carries no operands, and for any
operator that carries the wrong number of operands. The planner does not make those shapes, but
this adapter accepts a plan from any source, and a plan that lost or gained an operand must not
become a wider filter.

### Mapping hazards

The table above is about the **plan**. The other half of the contract is the **mapping**:

> The rows that a subquery of the adapter sees must be the same rows that your application put
> into the resource attributes.

When the two differ, the filter gives rows that the PDP denies and no action in the corpus can
see it ([#314](https://github.com/cerbos/query-plan-adapters/issues/314)). This adapter builds
each subquery from the association reflection, so it can see most of these hazards and refuses
them:

| Hazard | Position | Mechanism to check |
| --- | --- | --- |
| A filtered association | **Rejected** | `conditions:`, a block, a custom `dataset:` or `limit:` on the association. The adapter cannot put those onto the alias that it makes for the correlated subquery. |
| A target model over a filtered dataset | **Rejected** | `Sequel::Model(DB[:tags].where(visible: true))`. Every read of the application applies the filter and the subquery would not. |
| Subtype discrimination | **Rejected** | The `single_table_inheritance` plugin filters the dataset of a subclass on its key column, so an association that points at a subclass is refused by the same check. An association that points at the base class is permitted. |
| A to-one association used as a collection | **Rejected** | `one_to_one`. Nothing makes the database keep one row. Map a to-one association as a field path with dots. |
| A `one_through_one` in a field path | **Rejected** | Nothing makes the join table hold one row per owner, so a scalar subquery through it could read any of several. |
| A composite association key | **Rejected** | The adapter builds one equality for the correlated subquery and refuses the association instead of joining on the first column only. |
| An absent to-one parent | **Proved by the corpus** | Write a path such as `R.attr.parent.children` as a NESTED `association` mapping. See [A chain through a parent](#a-chain-through-a-parent). The `w1-*-chain` actions hold it under every polarity. |

### The NULL convention of the caller

There are two ways to send a NULL column to Cerbos, and the query plan looks the same for both.
You must tell the adapter which one your application uses.

| `null_attribute_representation:` | What your application sends for a NULL column | `R.attr.x == null` |
| --- | --- | --- |
| `:explicit` (the default) | An attribute whose value is null | Cerbos gives true, and `IS NULL` agrees |
| `:omitted` | No attribute at all | CEL raises a missing-attribute error, and Cerbos denies the row |

With `:omitted`, a filter that selects NULL would give exactly the rows that the PDP denies, so
the adapter refuses each null constant in the plan
([#302](https://github.com/cerbos/query-plan-adapters/issues/302)).

`null_attribute_representation:` is the fallback for the whole call. Declare the convention on
each attribute that can be NULL, with `null_representation:` on the mapping:

```ruby
MAPPING = {
  "request.resource.attr.owner" => Cerbos::Sequel.field("owner", null_representation: :explicit),
  "request.resource.attr.tag" => Cerbos::Sequel.field("tag", null_representation: :omitted),
  "request.resource.attr.title" => Cerbos::Sequel.field("title")  # NOT NULL: declares nothing
}
```

A declaration of `:explicit` changes only `eq`, `ne` and `in`, the operators that CEL
calculates to a definite boolean over a null value:

```
eq(col, c)     ->  col IS NOT NULL AND col = c
ne(col, c)     ->  NOT (col IS NOT NULL AND col = c)
in(col, [cs])  ->  col IS NOT NULL AND col IN (cs)
eq(a, b)       ->  (a IS NULL AND b IS NULL) OR (a IS NOT NULL AND b IS NOT NULL AND a = b)
```

A comparison between two columns must not mix the conventions, and the adapter refuses one
that does. Refer to [#308](https://github.com/cerbos/query-plan-adapters/issues/308) and
[ADR 0004](../docs/adr/0004-the-null-convention-is-a-property-of-the-attribute.md).

### The collation is part of the contract

CEL compares strings with attention to the case of the letters. Sequel renders the string
operators as `LIKE`, which is `LIKE BINARY` on MySQL. On SQLite, set
`PRAGMA case_sensitive_like = ON`, or `contains`, `startsWith` and `endsWith` select more rows
than the policy permits. For `=` on MySQL, use a `_bin` or `_cs` collation for the columns in
your policies.

### Timestamps are compared in the database timezone

The adapter reads each `timestamp()` literal as a UTC `Time`, and Sequel converts it into
`Sequel.database_timezone` when it writes the SQL. Set that to the zone your columns hold — on
SQLite a datetime is text, and a literal in another zone compares as the wrong instant.

The adversarial harness replays the corpus on SQLite, PostgreSQL and MySQL, so all three
dialects are covered. The offline suites record and assert SQLite's rendering only.

## Requirements

- Ruby 3.2 or a later version
- Sequel 5.60 or a later 5.x (CI tests 5.60 and the newest release)
- Cerbos after v0.40
- The official [Cerbos Ruby SDK](https://github.com/cerbos/cerbos-sdk-ruby)
  (the [`cerbos`](https://rubygems.org/gems/cerbos) gem)

This gem has no runtime dependency on `cerbos`: that SDK uses gRPC, and a dependency on it
would install a native `grpc` build in applications that speak to the PDP with REST. The
`plan:` parameter accepts a `Cerbos::Output::PlanResources` directly, the JSON of a
`PlanResources` response after a parse, or any object that has `kind` and `condition`.

## Installation

```bash
bundle add cerbos-sequel cerbos
```

## Usage

```ruby
require "cerbos"
require "cerbos/sequel"

cerbos = Cerbos::Client.new("localhost:3593", tls: false)

plan = cerbos.plan_resources(
  principal: {id: "user@example.com", roles: ["USER"]},
  resource: {kind: "document"},
  action: "view"
)

MAPPING = {
  "request.resource.attr.ownerId" => Cerbos::Sequel.field("owner_id"),
  "request.resource.attr.status" => Cerbos::Sequel.field("status"),
  "request.resource.attr.department" => Cerbos::Sequel.field("owner.department"),
  "request.resource.attr.tags" => Cerbos::Sequel.association(
    :tags,
    member_field: "name",
    fields: {"name" => Cerbos::Sequel.field("name")}
  )
}

documents = Cerbos::Sequel.query_plan_to_dataset(
  plan: plan, model: Document, attributes: MAPPING
)
```

Inside `Cerbos`, the constant `Sequel` is this adapter. Write `::Sequel` for the library in code
that is nested in a `Cerbos` module.

`model:` is a `Sequel::Model` subclass, or a dataset of one such as
`Document.where(tenant_id: tenant)`; the filter is then added to that dataset. The associations
and the column types are read from the model, so a plain `DB[:documents]` is refused.

For a runnable application that uses the published gem, refer to [`example/`](example/).

| Kind of plan | Result |
| --- | --- |
| `KIND_ALWAYS_ALLOWED` | the dataset, unfiltered |
| `KIND_ALWAYS_DENIED` | `dataset.where(false)`, which selects no row and still composes |
| `KIND_CONDITIONAL` | `dataset.where(<the condition after the translation>)` |

### The attribute map

The map must contain each plan variable. If it does not, the translation raises an error. The
adapter does not select a column from the name of an attribute.

#### `field` for scalar columns

```ruby
Cerbos::Sequel.field("status")            # a column on the model
Cerbos::Sequel.field("owner.department")  # through a many_to_one or a one_to_one
```

A path with dots goes through to-one associations, and each hop must be to-one. The adapter
makes a **correlated scalar subquery** for it, so the path cannot increase the number of rows
in the result. A hop that does not exist gives NULL, the comparison is UNKNOWN, and the row
stays out of the result — as it does in Cerbos, which denies a missing path.

The primary key is its own plan variable, so map it by name if a policy reads `R.id`:

```ruby
"request.resource.id" => Cerbos::Sequel.field("id")
```

#### `association` for collections

```ruby
Cerbos::Sequel.association(
  :tags,
  member_field: "name",
  fields: {"name" => Cerbos::Sequel.field("name")}
)
```

- `association` names a `one_to_many` or a `many_to_many`. A `many_to_many` becomes its join
  table and its target in one correlated subquery.
- `member_field` replaces the element when the policy uses the collection as a list of simple
  values. Thus `"urgent" in R.attr.tags` compares with `tag.name`.
- `fields` maps the member names in the bodies of the lambdas, and an entry can be another
  `association`. This is how the adapter resolves a chain with more than one hop.

Each subquery gets new table aliases. Thus a macro on an association inside another macro on
the same association correlates to the outer row.

#### A chain through a parent

A path that the policy writes with dots, such as `R.attr.mainCategory.subCategories`, is mapped
from its START, with each step after it in `fields`:

```ruby
"request.resource.attr.mainCategory" => Cerbos::Sequel.association(:categories, fields: {
  "subCategories" => Cerbos::Sequel.association(:sub_categories, fields: {
    "name" => Cerbos::Sequel.field("name")
  }),
  "subNames" => Cerbos::Sequel.association(:sub_categories, member_field: "name")
})
```

CEL cannot read a field from a list, so every step before the last one is a to-ONE parent. When
that parent is absent, your application sends no attribute, and Cerbos denies the row. A
subquery cannot tell an absent parent from a parent with no children, so the adapter requires
the parent hops to exist and a row without a parent stays out of the result under **both**
polarities. An association that you map directly keeps the usual meaning of an empty
collection: `!R.attr.tags.exists(...)` over zero tags is still TRUE.

#### A macro over a principal attribute

When a collection is a principal attribute, the planner sends the list itself. The adapter
evaluates the body of the lambda one time for each element and joins the results with OR for
`exists`, or with AND for `all`. You need no mapping for such a collection.

#### `operator_overrides` for translations that are specific to your schema

```ruby
Cerbos::Sequel.query_plan_to_dataset(
  plan: plan, model: Document, attributes: MAPPING,
  operator_overrides: {
    "matches" => ->(column, pattern) { Sequel.like(column, Regexp.new(pattern)) }
  }
)
```

The adapter gives the operands to an override after it resolves them, and the override gives a
Sequel expression. You cannot override the structural operators: `and`, `or`, `not`, `if`,
`lambda` and the collection macros.

## How the adapter keeps the three-valued logic

CEL denies a resource if the evaluation of its condition makes an error. The UNKNOWN value of
SQL has the same behaviour: a predicate does not select it, and the negation of that predicate
does not select it. The translation keeps UNKNOWN and does not change it into a boolean:

- **A ternary becomes a `CASE` whose `ELSE` is `NULL`.** If the condition is UNKNOWN, the `CASE`
  gives NULL, and the row stays out of the result under a `NOT` too.
- **A collection macro becomes a `CASE` expression and not only an `EXISTS` subquery**, with a
  guard for an element whose body made an error that matches what each CEL quantifier does with
  one.
- **A negation is an explicit `NOT`.** The adapter builds its nodes with their constructors and
  never with Sequel's `~`, which rewrites `NOT (a = b)` into `a != b` and pushes `NOT` through De
  Morgan. Both rewrites are sound, but the explicit form keeps the SQL in the shape of the plan.

## Development

All the components run in Docker. The version of the PDP comes from
`conformance/CERBOS_VERSION`. You do not need Ruby on your computer.

```bash
./scripts/test.sh                                   # all the suites
./scripts/test.sh spec/translator_spec.rb           # offline: no PDP, no database server
./scripts/golden-update.sh                          # rewrite golden/expectations.json
RUBY_VERSION=3.2 SEQUEL_VERSION="= 5.60.0" ./scripts/test.sh
ADAPTER_TEST_DB=postgres ./scripts/test.sh spec/adversarial_conformance_spec.rb
ADAPTER_TEST_DB=mysql ./scripts/test.sh spec/adversarial_conformance_spec.rb
./scripts/lint.sh
```

`ADAPTER_TEST_DB` chooses the store the adversarial harness runs on: `sqlite` (the default, in
memory), `postgres` or `mysql`; any other value fails. The two servers are pinned by tag and
digest in [`POSTGRES_IMAGE`](POSTGRES_IMAGE) and [`MYSQL_IMAGE`](MYSQL_IMAGE), and
`scripts/test.sh` starts the one it needs. MySQL runs with `utf8mb4_0900_bin` on the server, the
tables and the connection, because the default collation makes `=` case-insensitive and CEL's
string equality is byte-exact. CI replays the corpus on all three, under both evaluation modes.

The real stores are not a formality. The PostgreSQL leg found two filters that SQLite answered
correctly by accident: `integer_column * 0.1` computing in exact decimals (`3 * 0.1 = 0.3` is
TRUE there and FALSE in CEL, an over-grant — the adapter now casts to a double), and
`hierarchy()` over an integer column reaching a `LIKE` the server rejects (now refused at
translation time).

There are three suites:

- `spec/translator_spec.rb` is the **translator unit test**. It replays every plan in
  `../conformance/wire-fixtures/` and asserts the SQL against
  [`golden/expectations.json`](golden/expectations.json), plus a set of rules stated over the
  whole corpus that survive a regeneration — every `LIKE` carries an `ESCAPE`, no statement joins
  the resource table to itself, every identifier names a declared table. Needs no PDP.
- `spec/adapter_contract_spec.rb` covers what a **caller** supplies and the corpus therefore
  cannot vary: the mapper forms, a `many_to_many`, operator overrides, the per-call NULL
  convention, the four transports a plan can arrive over, and the association shapes the
  adapter refuses to guess at. Needs no PDP.
- `spec/adversarial_conformance_spec.rb` is the differential harness over the shared corpus
  (`../conformance/`). It obeys the oracle procedure in
  [conformance/README.md](../conformance/README.md) and starts a pinned PDP.

### The golden asset declares its renderer

`golden/expectations.json` records the emitted dataset rendered as SQL, so Sequel's own
literalizer is an input to the bytes and the file declares `"sequel": "5"`.
`golden-update.sh` refuses to run under another major. Sequel releases a minor version every
month and has kept its rendering stable across 5.x, so the header names the major and CI proves
the claim on both ends of the range: the 5.60 leg asserts the same bytes as the newest release.
A 5.x release that did change them fails the translator unit test on the Renovate pull request
that brings it.

The example application in [`example/`](example/) runs the shared demo domain. Start it with
`../demo/scripts/run-example.sh sequel` — see [example/README.md](example/README.md).
