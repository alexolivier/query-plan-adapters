# Copyright 2021-2026 Zenauth Ltd.
# SPDX-License-Identifier: Apache-2.0

"""``get_query`` contracts for caller options and plans the planner cannot produce.

The corpus cannot vary caller arguments such as operator overrides or model styles,
so they are tested here. Policy-reachable shapes belong in the corpus. No PDP needed.
"""

import math

import pytest
from cerbos.sdk.model import (
    PlanResourcesFilter,
    PlanResourcesFilterKind,
    PlanResourcesResponse,
)
from sqlalchemy import Boolean, DateTime, String, column, create_engine, literal, table
from sqlalchemy.dialects import postgresql

from cerbos_sqlalchemy import get_query


def _default_resp_params():
    return {
        "request_id": "1",
        "action": "action",
        "resource_kind": "resource",
        "policy_version": "default",
    }


def _conditional_plan(expression):
    return PlanResourcesResponse(
        filter=PlanResourcesFilter.from_dict(
            {
                "kind": PlanResourcesFilterKind.CONDITIONAL,
                "condition": {"expression": expression},
            }
        ),
        **_default_resp_params(),
    )


class TestNullAttributeRepresentation:
    """The call-level NULL convention (#302). The plan cannot tell the two apart.

    Under "omitted", CEL errors on the missing attribute and denies the row, so
    ``IS NULL`` would return exactly the rows the PDP refuses.
    """

    @staticmethod
    def _null_eq_plan():
        return _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.name"},
                    {"value": None},
                ],
            }
        )

    def test_explicit_is_the_default_and_keeps_is_null(self, resource_table):
        attr = {"request.resource.attr.name": resource_table.name}
        default = get_query(self._null_eq_plan(), resource_table, attr)
        explicit = get_query(
            self._null_eq_plan(),
            resource_table,
            attr,
            null_attribute_representation="explicit",
        )

        compiled = str(default.compile(compile_kwargs={"literal_binds": True}))
        assert " IS NULL" in compiled
        assert compiled == str(explicit.compile(compile_kwargs={"literal_binds": True}))

    def test_omitted_leaves_null_free_comparisons_untouched(self, resource_table, conn):
        plan = _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.name"},
                    {"value": "resource1"},
                ],
            }
        )
        query = get_query(
            plan,
            resource_table,
            {"request.resource.attr.name": resource_table.name},
            null_attribute_representation="omitted",
        )
        assert [row.name for row in conn.execute(query)] == ["resource1"]

    def test_unknown_representation_is_rejected(self, resource_table):
        with pytest.raises(ValueError, match="must be 'explicit' or 'omitted'"):
            get_query(
                self._null_eq_plan(),
                resource_table,
                {"request.resource.attr.name": resource_table.name},
                null_attribute_representation="sometimes",
            )


class TestAttributeNullRepresentation:
    """The per-attribute NULL convention (#308), for callers that mix both.

    The call-level option is only the default for undeclared attributes.
    """

    @staticmethod
    def _attr_map(resource_table):
        return {
            "request.resource.attr.owner": resource_table.name,
            "request.resource.attr.coOwner": resource_table.aString,
            "request.resource.attr.plain": resource_table.name,
        }

    @staticmethod
    def _declared():
        return {
            "request.resource.attr.owner": "explicit",
            "request.resource.attr.coOwner": "explicit",
        }

    @staticmethod
    def _comparison(operator, variable, value):
        return {
            "operator": operator,
            "operands": [{"variable": variable}, {"value": value}],
        }

    @pytest.mark.parametrize("operator, expected", [("eq", []), ("ne", [1, 2])])
    def test_explicit_null_does_not_enable_string_number_coercion(
        self, resource_table, operator, expected
    ):
        engine = create_engine("sqlite://")
        resource_table.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                resource_table.__table__.insert(),
                [{"id": 1, "name": "0"}, {"id": 2, "name": None}],
            )
            query = get_query(
                _conditional_plan(
                    self._comparison(operator, "request.resource.attr.owner", 0)
                ),
                resource_table,
                self._attr_map(resource_table),
                attribute_null_representation=self._declared(),
            )
            assert [row.id for row in connection.execute(query)] == expected
        engine.dispose()

    def test_an_unmapped_attribute_is_rejected(self, resource_table):
        with pytest.raises(ValueError, match="not in the attribute column map"):
            get_query(
                _conditional_plan(
                    self._comparison("eq", "request.resource.attr.owner", "x")
                ),
                resource_table,
                self._attr_map(resource_table),
                attribute_null_representation={
                    "request.resource.attr.absent": "explicit"
                },
            )

    def test_an_unknown_convention_is_rejected(self, resource_table):
        with pytest.raises(ValueError, match="must be 'explicit' or 'omitted'"):
            get_query(
                _conditional_plan(
                    self._comparison("eq", "request.resource.attr.owner", "x")
                ),
                resource_table,
                self._attr_map(resource_table),
                attribute_null_representation={
                    "request.resource.attr.owner": "sometimes"
                },
            )


class TestSemanticEdgeTranslations:
    @pytest.mark.parametrize("field_first", (True, False))
    def test_direct_field_nan_ordering_is_folded_in_both_orders(
        self, field_first, resource_table, conn
    ):
        field = {"variable": "request.resource.attr.number"}
        nan = {
            "expression": {
                "operator": "div",
                "operands": [{"value": 0}, {"value": 0}],
            }
        }
        plan = _conditional_plan(
            {
                "operator": "gt" if field_first else "lt",
                "operands": [field, nan] if field_first else [nan, field],
            }
        )
        query = get_query(
            plan,
            resource_table,
            {"request.resource.attr.number": resource_table.aNumber},
        )

        assert conn.execute(query).fetchall() == []
        compiled = query.compile(dialect=postgresql.dialect())
        assert not any(
            isinstance(value, float) and not math.isfinite(value)
            for value in compiled.params.values()
        )

    @pytest.mark.parametrize(
        "value",
        [
            "2024-01-01",
            "2024-W01-1T00:00:00Z",
            "2024-01-01 00:00:00Z",
            "0000-01-01T00:00:00Z",
            "2024-02-30T00:00:00Z",
            "9999-12-31T23:00:00-02:00",
        ],
    )
    def test_timestamp_rejects_non_rfc3339_or_out_of_range_literals(self, value):
        temporal_table = table("events", column("created_at", DateTime(timezone=True)))
        plan = _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {
                        "expression": {
                            "operator": "timestamp",
                            "operands": [
                                {"variable": "request.resource.attr.createdAt"}
                            ],
                        }
                    },
                    {
                        "expression": {
                            "operator": "timestamp",
                            "operands": [{"value": value}],
                        }
                    },
                ],
            }
        )

        with pytest.raises(ValueError, match="RFC-3339|instant range"):
            get_query(
                plan,
                temporal_table,
                {"request.resource.attr.createdAt": temporal_table.c.created_at},
            )


class TestGetQueryOverrides:
    @pytest.mark.parametrize("overrides", [{}, {"eq": None, "add": None}])
    def test_none_override_uses_default_for_nested_expression(
        self, resource_table, conn, overrides
    ):
        plan = _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {
                        "expression": {
                            "operator": "add",
                            "operands": [
                                {"variable": "request.resource.attr.aNumber"},
                                {"value": 0},
                            ],
                        }
                    },
                    {"value": 1},
                ],
            }
        )
        query = get_query(
            plan,
            resource_table,
            {"request.resource.attr.aNumber": resource_table.aNumber},
            operator_override_fns=overrides,
        )
        expected = get_query(
            plan,
            resource_table,
            {"request.resource.attr.aNumber": resource_table.aNumber},
        )
        assert conn.execute(query).fetchall() == conn.execute(expected).fetchall()
        assert str(query) == str(expected)

    def test_unrelated_override_does_not_bypass_table_mapping_validation(
        self, resource_table, user_table
    ):
        plan = _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.externalOwner"},
                    {"value": 1},
                ],
            }
        )

        with pytest.raises(TypeError, match="table_mapping"):
            get_query(
                plan,
                resource_table,
                {"request.resource.attr.externalOwner": user_table.id},
                operator_override_fns={"size": lambda *_: literal(0)},
            )

    def test_used_override_owns_foreign_operand_without_flat_mapping(
        self, resource_table, user_table, conn
    ):
        plan = _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.externalOwner"},
                    {"value": 1},
                ],
            }
        )
        query = get_query(
            plan,
            resource_table,
            {"request.resource.attr.externalOwner": user_table.id},
            operator_override_fns={
                # Rewrites the foreign column to a predicate on the root table.
                "eq": lambda _column, value: resource_table.ownedBy == str(value)
            },
        )

        assert {row.name for row in conn.execute(query)} == {
            "resource1",
            "resource2",
        }

    def test_in_single_query(self, resource_table, conn):
        plan_resources_filter = PlanResourcesFilter.from_dict(
            {
                "kind": PlanResourcesFilterKind.CONDITIONAL,
                "condition": {
                    "expression": {
                        "operator": "in",
                        "operands": [
                            {"variable": "request.resource.attr.name"},
                            {"value": "resource1"},
                        ],
                    },
                },
            }
        )
        plan_resource_resp = PlanResourcesResponse(
            filter=plan_resources_filter,
            **_default_resp_params(),
        )
        attr = {
            "request.resource.attr.name": resource_table.name,
        }
        query = get_query(plan_resource_resp, resource_table, attr)
        res = conn.execute(query).fetchall()
        assert len(res) == 1
        assert res[0].name == "resource1"

    def test_unrecognised_filter(self, resource_table):
        unknown_op = "unknown"
        plan_resources_filter = PlanResourcesFilter.from_dict(
            {
                "kind": PlanResourcesFilterKind.CONDITIONAL,
                "condition": {
                    "expression": {
                        "operator": unknown_op,
                        "operands": [
                            {"variable": "request.resource.attr.ownedBy"},
                            {"value": "1"},
                        ],
                    },
                },
            }
        )
        plan_resource_resp = PlanResourcesResponse(
            filter=plan_resources_filter,
            **_default_resp_params(),
        )
        attr = {
            "request.resource.attr.ownedBy": resource_table.ownedBy,
        }
        with pytest.raises(ValueError) as exc_info:
            get_query(plan_resource_resp, resource_table, attr)
        assert exc_info.value.args[0] == f"Unrecognised operator: {unknown_op}"


class TestKnownValueCollections:
    """Edge cases of folding `exists`/`all` over a literal value list.

    The planner unrolls up to 10 elements and sends a value-list lambda above that
    (cerbos/cerbos#2570). The `principal/*` corpus cases cover both sides. These
    cover degenerate or malformed plans the corpus cannot.
    """

    @staticmethod
    def _value_list_plan(operator, elements, body, variable="t"):
        return _conditional_plan(
            {
                "operator": operator,
                "operands": [
                    {"value": elements},
                    {
                        "expression": {
                            "operator": "lambda",
                            "operands": [body, {"variable": variable}],
                        }
                    },
                ],
            }
        )

    @staticmethod
    def _eq_body(variable="t"):
        return {
            "expression": {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.aString"},
                    {"variable": variable},
                ],
            }
        }

    def test_variable_path_drills_into_element_fields(self, resource_table, conn):
        plan = self._value_list_plan(
            "exists",
            [{"name": "string", "meta": {"rank": 1}}, {"name": "nope"}],
            {
                "expression": {
                    "operator": "eq",
                    "operands": [
                        {"variable": "request.resource.attr.aString"},
                        {"variable": "t.name"},
                    ],
                }
            },
        )
        query = get_query(
            plan,
            resource_table,
            {"request.resource.attr.aString": resource_table.aString},
        )
        assert [row.name for row in conn.execute(query)] == ["resource1"]

    def test_empty_value_list_keeps_cel_identity_semantics(self, resource_table, conn):
        # exists over [] matches nothing; all over [] matches everything.
        exists_query = get_query(
            self._value_list_plan("exists", [], self._eq_body()),
            resource_table,
            {"request.resource.attr.aString": resource_table.aString},
        )
        assert conn.execute(exists_query).fetchall() == []

        all_query = get_query(
            self._value_list_plan("all", [], self._eq_body()),
            resource_table,
            {"request.resource.attr.aString": resource_table.aString},
        )
        assert len(conn.execute(all_query).fetchall()) == 3

    def test_nested_lambda_rebinding_the_variable_shadows_substitution(
        self, resource_table
    ):
        # The inner lambda rebinds `t`, so only the inner collection operand is
        # substituted, not the inner body.
        plan = self._value_list_plan(
            "exists",
            [["a"], ["b"]],
            {
                "expression": {
                    "operator": "exists",
                    "operands": [
                        {"variable": "t"},
                        {
                            "expression": {
                                "operator": "lambda",
                                "operands": [
                                    {
                                        "expression": {
                                            "operator": "eq",
                                            "operands": [
                                                {
                                                    "variable": (
                                                        "request.resource.attr.aString"
                                                    )
                                                },
                                                {"variable": "t"},
                                            ],
                                        }
                                    },
                                    {"variable": "t"},
                                ],
                            }
                        },
                    ],
                }
            },
        )
        query = get_query(
            plan,
            resource_table,
            {"request.resource.attr.aString": resource_table.aString},
        )
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "'a'" in compiled and "'b'" in compiled

    def test_missing_element_field_fails_closed(self, resource_table):
        plan = self._value_list_plan(
            "exists",
            [{"name": "string"}],
            {
                "expression": {
                    "operator": "eq",
                    "operands": [
                        {"variable": "request.resource.attr.aString"},
                        {"variable": "t.missing"},
                    ],
                }
            },
        )
        with pytest.raises(ValueError) as exc_info:
            get_query(
                plan,
                resource_table,
                {"request.resource.attr.aString": resource_table.aString},
            )
        assert 'Cannot resolve "t.missing"' in exc_info.value.args[0]

    def test_non_list_collection_value_fails_closed(self, resource_table):
        plan = self._value_list_plan("exists", {"not": "a list"}, self._eq_body())
        with pytest.raises(ValueError) as exc_info:
            get_query(
                plan,
                resource_table,
                {"request.resource.attr.aString": resource_table.aString},
            )
        assert (
            "exists over a literal collection requires a list value"
            in exc_info.value.args[0]
        )


class TestDeclarativeStyles:
    """`get_query` accepts both declarative styles and a Core `Table`.

    A 2.0 `DeclarativeBase` model is not a `DeclarativeMeta` instance, so it takes
    its own arm of `GenericTable` (#181).
    """

    @staticmethod
    def _eq_bool_plan():
        return _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.aBool"},
                    {"value": True},
                ],
            }
        )

    def test_declarative_base_cross_table_mapping(
        self, modern_resource_table, modern_user_table, conn
    ):
        # Both the root and the joined model are 2.0-style.
        plan = _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.ownerId"},
                    {"value": 1},
                ],
            }
        )
        query = get_query(
            plan,
            modern_resource_table,
            {"request.resource.attr.ownerId": modern_user_table.id},
            [
                (
                    modern_user_table,
                    modern_resource_table.ownedBy == modern_user_table.id,
                )
            ],
        )
        assert {row.name for row in conn.execute(query)} == {"resource1", "resource2"}

    def test_declarative_base_missing_table_mapping_still_fails_closed(
        self, modern_resource_table, modern_user_table
    ):
        plan = _conditional_plan(
            {
                "operator": "eq",
                "operands": [
                    {"variable": "request.resource.attr.ownerId"},
                    {"value": 1},
                ],
            }
        )
        with pytest.raises(TypeError, match="table_mapping"):
            get_query(
                plan,
                modern_resource_table,
                {"request.resource.attr.ownerId": modern_user_table.id},
            )

    def test_core_table_still_supported(self, conn):
        core_resource = table(
            "resource",
            column("name", String),
            column("aBool", Boolean),
        )
        query = get_query(
            self._eq_bool_plan(),
            core_resource,
            {"request.resource.attr.aBool": core_resource.c.aBool},
        )
        assert {row.name for row in conn.execute(query)} == {"resource1", "resource3"}


class TestPlanOperandBoundary:
    @pytest.mark.parametrize(
        "operand",
        [
            {"value": False, "variable": "request.resource.attr.aBool"},
            {"expression": {"value": True}, "value": None},
            {"operator": "eq", "operands": None},
            {"variable": ["request.resource.attr.aBool"]},
        ],
    )
    def test_malformed_nodes_are_rejected_before_semantic_traversal(self, operand):
        # The planner never sends these. Reject them rather than pick a branch
        # by key order.
        from cerbos_sqlalchemy._plan import parse_operand

        with pytest.raises(ValueError, match="Unrecognised operand shape"):
            parse_operand(operand)
