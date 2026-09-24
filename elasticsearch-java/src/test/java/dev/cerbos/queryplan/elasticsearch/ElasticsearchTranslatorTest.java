/*
 * Copyright 2021-2026 Zenauth Ltd.
 * SPDX-License-Identifier: Apache-2.0
 */

package dev.cerbos.queryplan.elasticsearch;

import dev.cerbos.queryplan.elasticsearch.ElasticsearchQueryPlanAdapter.Result;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Translator unit test: what this adapter can be asked without a store. Which rows a translated
 * case returns is the conformance harness's job ({@link ElasticsearchAdversarialConformanceTest});
 * this suite pins the caller-supplied options the corpus cannot vary and the rule every emitted
 * query must follow. Plans come from {@code conformance/golden/<current PDP>/}. Needs no Docker,
 * PDP or Elasticsearch.
 */
class ElasticsearchTranslatorTest {

    /**
     * The query emitted for every current-PDP case this adapter translates to a condition, keyed
     * by case id, as the JSON a caller sends. Refused cases are the harness's to assert.
     */
    private static final Map<String, Map<String, Object>> CONDITIONAL = new TreeMap<>();

    static {
        for (Corpus.Golden golden : Corpus.goldens(Corpus.CURRENT_TAG)) {
            Result result;
            try {
                result = ElasticsearchQueryPlanAdapter.toElasticsearchQuery(golden.plan(), Corpus.OPTIONS);
            } catch (UnsupportedPlanShapeException refused) {
                continue;
            }
            if (result instanceof Result.Conditional conditional) {
                CONDITIONAL.put(golden.id(), conditional.query());
            }
        }
    }

    private static final String NULL_ON_MISSING_ATTRIBUTE = "null/equals/null-literal-on-missing-attribute";

    /**
     * {@code R.attr.aOptionalString == null} plans to the same node whichever null convention the
     * attribute follows, so other adapters take an option choosing one. Elasticsearch does not
     * index an explicit null, so this adapter refuses the probe whether or not the attribute is
     * declared explicit-null: the corpus translates it one way only.
     */
    @Test
    void theNullOnMissingAttributeProbeIsRefusedUnderEitherNullConvention() {
        assertThrows(UnsupportedPlanShapeException.class,
                () -> Corpus.translate(NULL_ON_MISSING_ATTRIBUTE));
        assertThrows(UnsupportedPlanShapeException.class,
                () -> ElasticsearchQueryPlanAdapter.toElasticsearchQuery(
                        Corpus.plan(NULL_ON_MISSING_ATTRIBUTE),
                        Corpus.OPTIONS.withExplicitNullAttributes(
                                Set.of("request.resource.attr.aOptionalString"))));
    }

    /**
     * The relative-window cases compare against a folded {@code now()} that the goldens store as a
     * placeholder. At the PDP's nanosecond precision they are refused, since an Elasticsearch
     * {@code date} field holds milliseconds; at millisecond precision they translate. This pins
     * the refusal to the precision, not to the shape.
     */
    @ParameterizedTest(name = "{0}")
    @ValueSource(strings = {"timestamp/less-than/relative-window",
            "timestamp/greater-than/relative-window-value-first"})
    void theRelativeWindowCasesAreRefusedForTheirPrecision(String caseId) {
        UnsupportedPlanShapeException ex = assertThrows(UnsupportedPlanShapeException.class,
                () -> ElasticsearchQueryPlanAdapter.toElasticsearchQuery(
                        Corpus.plan(caseId, "2026-08-11T09:13:39.123456789Z"), Corpus.OPTIONS));
        assertTrue(ex.getMessage().contains("Sub-millisecond"), ex.getMessage());

        assertInstanceOf(Result.Conditional.class,
                ElasticsearchQueryPlanAdapter.toElasticsearchQuery(
                        Corpus.plan(caseId, "2026-08-11T09:13:39.123Z"), Corpus.OPTIONS),
                caseId + " no longer translates at millisecond precision, so the nanoseconds are"
                        + " not what refuses it");
    }

    /**
     * Every emitted value is a plain JDK type, so a caller can serialise the query with any
     * JSON library and no client library is needed on the classpath.
     */
    @Test
    void everyEmittedValueIsAPlainJdkType() {
        List<String> exotic = new ArrayList<>();
        CONDITIONAL.forEach((action, query) -> assertPlain(action, query, exotic));
        assertEquals(List.of(), exotic);
        assertFalse(CONDITIONAL.isEmpty());
    }

    private void assertPlain(String action, Object value, List<String> exotic) {
        if (value instanceof Map<?, ?> map) {
            map.forEach((key, child) -> {
                if (!(key instanceof String)) {
                    exotic.add(action + ": non-string key "
                            + (key == null ? "null" : key.getClass().getName()));
                }
                assertPlain(action, child, exotic);
            });
        } else if (value instanceof List<?> list) {
            list.forEach(child -> assertPlain(action, child, exotic));
        } else if (!(value instanceof String || value instanceof Boolean
                || value instanceof Long || value instanceof Integer
                || value instanceof Double)) {
            exotic.add(action + ": " + (value == null ? "null" : value.getClass().getName()));
        }
    }
}
