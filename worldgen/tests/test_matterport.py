import importlib

import pytest

from worldgen.sources.matterport import MutationRejected, assert_read_only


@pytest.mark.parametrize(
    "q",
    [
        "mutation { purchase(id: 1) { ok } }",
        "  MUTATION Buy { x }",
        "query { a } mutation { b }",
        "{ a }\nsubscription S { b }",
    ],
)
def test_rejects_mutations(q):
    with pytest.raises(MutationRejected):
        assert_read_only(q)


@pytest.mark.parametrize(
    "q",
    [
        'query { model(id: "x") { name } }',
        '{ model(id: "mutation") { name } }',
        "# mutation in a comment\nquery { a }",
    ],
)
def test_allows_queries(q):
    assert_read_only(q)


STEPS = ["costar", "build_block", "build_house", "build_catalog", "audit", "publish"]


@pytest.mark.parametrize("step", STEPS)
def test_step_modules_import(step):
    assert callable(importlib.import_module(f"worldgen.{step}").main)
