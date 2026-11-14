"""Generate Pydantic models from contracts/*.schema.json into contracts_gen/.

uv run python gen_models.py   (from apps/server)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACTS = HERE.parents[1] / "contracts"
OUT = HERE / "contracts_gen"

OUT.mkdir(exist_ok=True)
(OUT / "__init__.py").write_text('"""Generated from contracts/. Do not edit; run gen_models.py."""\n')
for schema in sorted(CONTRACTS.glob("*.schema.json")):
    name = schema.name.removesuffix(".schema.json")
    subprocess.run(
        [
            "datamodel-codegen",
            "--input", str(schema),
            "--input-file-type", "jsonschema",
            "--output", str(OUT / f"{name}.py"),
            "--output-model-type", "pydantic_v2.BaseModel",
            "--target-python-version", "3.11",
            "--disable-timestamp",
            "--use-annotated",
            "--formatters", "ruff-format", "ruff-check",
        ],
        check=True,
    )
    print(f"generated contracts_gen/{name}.py")
