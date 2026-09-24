/*
 * Copyright 2021-2026 Zenauth Ltd.
 * SPDX-License-Identifier: Apache-2.0
 */

package dev.cerbos.queryplan.springdata;

import dev.cerbos.queryplan.springdata.testmodel.ResourceEntity;

import jakarta.persistence.EntityManager;
import jakarta.persistence.EntityManagerFactory;
import jakarta.persistence.Persistence;
import jakarta.persistence.criteria.CriteriaBuilder;
import jakarta.persistence.criteria.CriteriaQuery;
import jakarta.persistence.criteria.From;
import jakarta.persistence.criteria.Join;
import jakarta.persistence.criteria.Root;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertSame;

/**
 * Unit tests for {@link Scope}: which scope owns a collection. A wrong owner fails silently,
 * because an element entity can have a collection with the same name. Runs offline on H2.
 */
class ScopeTest {

    private static final String TAGS = "request.resource.attr.tags";
    private static final String CATEGORIES = "request.resource.attr.categories";

    private static final AttributeMapping.Relation SUB_CATEGORIES =
            AttributeMapping.relation("subCategories", "name",
                    Map.of("name", AttributeMapping.field("name")));

    private static final Map<String, AttributeMapping> MAPPER = Map.of(
            "request.resource.attr.aString", AttributeMapping.field("aString"),
            TAGS, AttributeMapping.relation("tags", Map.of(
                    "name", AttributeMapping.field("name"))),
            CATEGORIES, AttributeMapping.relation("categories", Map.of(
                    "name", AttributeMapping.field("name"),
                    "subCategories", SUB_CATEGORIES)));

    private static EntityManagerFactory emf;

    private EntityManager em;
    private CriteriaBuilder cb;
    private CriteriaQuery<ResourceEntity> query;
    private Root<ResourceEntity> root;
    private Scope rootScope;

    @BeforeAll
    static void setUp() {
        emf = Persistence.createEntityManagerFactory("test-pu");
    }

    @AfterAll
    static void tearDown() {
        if (emf != null) emf.close();
    }

    @BeforeEach
    void newQuery() {
        em = emf.createEntityManager();
        cb = em.getCriteriaBuilder();
        query = cb.createQuery(ResourceEntity.class);
        root = query.from(ResourceEntity.class);
        rootScope = Scope.root(root, query, MAPPER);
    }

    @AfterEach
    void closeEm() {
        em.close();
    }

    /** The scope {@code categories.exists(c, ...)} builds. */
    private Scope categoriesLambda(Scope outer, From<?, ?> from) {
        return Scope.lambda(from, query,
                (AttributeMapping.Relation) MAPPER.get(CATEGORIES), "c", outer);
    }

    @Nested
    class LambdaResolution {

        private Join<?, ?> categoryJoin;
        private Scope lambdaScope;

        @BeforeEach
        void enterLambda() {
            categoryJoin = root.join("categories");
            lambdaScope = categoriesLambda(rootScope, categoryJoin);
        }

        /**
         * A sub-category also has a {@code categories} collection. Anchoring to the inner lambda
         * would still build a query, but over the wrong collection.
         */
        @Test
        void outerRelationIsNotCapturedByASameNamedCollectionOnTheElement() {
            Join<?, ?> subCategoryJoin = categoryJoin.join("subCategories");
            Scope subLambda = Scope.lambda(subCategoryJoin, query, SUB_CATEGORIES, "s", lambdaScope);

            Scope.ResolvedRelation rel = assertInstanceOf(Scope.ResolvedRelation.class,
                    subLambda.resolve(CATEGORIES));
            assertSame(rootScope, rel.owner());
            assertSame(root, rel.owner().from());
        }
    }
}
