# Copyright 2021-2026 Zenauth Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Structural tests for ``require_hops``; the adversarial suite proves its semantics.

The corpus only ever passes one hop predicate, so this pins that a caller's several
are all required.
"""

from sqlalchemy import Column, Integer, MetaData, String, Table, literal

from cerbos_sqlalchemy import require_hops

_metadata = MetaData()
resource = Table(
    "hazard_resource",
    _metadata,
    Column("id", Integer, primary_key=True),
)
category = Table(
    "hazard_category",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("resource_id", Integer),
    Column("kind", String),
)


def _sql(expression) -> str:
    return str(expression.compile(compile_kwargs={"literal_binds": True}))


def test_every_hop_predicate_is_required():
    # All predicates go in one EXISTS, so the chain is required as a whole.
    guarded = require_hops(
        literal(True),
        [
            category.c.resource_id == resource.c.id,
            category.c.kind == "main",
        ],
    )
    sql = _sql(guarded)
    assert sql.count("EXISTS") == 1
    assert "hazard_category.resource_id = hazard_resource.id" in sql
    assert "hazard_category.kind = 'main'" in sql
