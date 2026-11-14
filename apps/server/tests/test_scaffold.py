import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

import settings
from api.main import app
from data.matterport_client import MutationRejected, assert_read_only


def test_health():
    assert TestClient(app).get("/health").json()["ok"] is True


def test_contracts_validate():
    script = settings.CONTRACTS_DIR / "validate.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "q",
    [
        "mutation { purchase(id: 1) { ok } }",
        "  MUTATION Buy { x }",
        "query { a } mutation { b }",
        "{ a }\nsubscription S { b }",
    ],
)
def test_matterport_rejects_mutations(q):
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
def test_matterport_allows_queries(q):
    assert_read_only(q)
