import json
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_all_route_configs_match_shared_schema() -> None:
    schema = json.loads((ROOT / "schemas/route.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    route_files = sorted((ROOT / "routes").glob("*.yaml"))
    assert {path.stem for path in route_files} == {"int_steam", "cn_steam", "int_android"}

    for path in route_files:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        validator.validate(payload)
        assert payload["translation"]["sourceRoute"] == "INT_STEAM"
        assert payload["translation"]["lang"]["target"] in payload["translation"]["lang"]["assets"]
