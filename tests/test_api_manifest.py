"""Offline invariants checked against every vendored operation."""
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from gmi.api_commands import manifest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gen_api", ROOT / "tools/gen_api.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def resolve_top(value, doc):
    """Independent reference walker for the spec comparisons below."""
    seen = set()
    while "$ref" in value:
        ref = value["$ref"]
        assert ref not in seen, f"Cyclic spec reference: {ref}"
        seen.add(ref)
        target = doc
        for part in ref.removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        value = {**target, **{k: v for k, v in value.items() if k != "$ref"}}
    return value


def test_every_manifest_path_has_matching_parameters():
    for op in manifest()["operations"]:
        names = re.findall(r"\{([^{}]+)\}", op["path"])
        assert Counter(names) == Counter(p["name"] for p in op["path_parameters"]), op


def test_manifest_has_no_references():
    assert "$ref" not in json.dumps(manifest())


def test_all_parameters_and_required_flags_match_vendored_specs():
    actual = {(o["service"], o["path"], o["method"].lower()): o for o in manifest()["operations"]}
    visited = set()
    for service, filename in generator.FILES.items():
        doc = yaml.safe_load((ROOT / "specs" / filename).read_text())
        for path, path_item in doc["paths"].items():
            for method, operation in path_item.items():
                if method not in METHODS:
                    continue
                key = service, path, method
                visited.add(key)
                op = actual[key]
                expected = {}
                for raw in path_item.get("parameters", []) + operation.get("parameters", []):
                    p = resolve_top(raw, doc)
                    expected[p["name"], p["in"]] = p
                parameters = [p for field in ("path_parameters", "query_parameters", "header_parameters", "cookie_parameters") for p in op[field]]
                assert len(parameters) == len(expected), key
                assert {(p["name"], p["in"]) for p in parameters} == set(expected), key
                for p in parameters:
                    source = expected[p["name"], p["in"]]
                    schema = resolve_top(source.get("schema", {}), doc)
                    assert p["name"] and p["in"] in {"path", "query", "header", "cookie"}
                    assert p["type"] == schema.get("type", "string"), (key, p)
                    assert p["required"] == bool(source.get("required", False)), (key, p)
                body = resolve_top(operation.get("requestBody", {}), doc)
                if body.get("content"):
                    assert op["request_body"]["required"] == bool(body.get("required", False)), key
                    content = body["content"][sorted(body["content"])[0]]
                    schema = resolve_top(content.get("schema", {}), doc)
                    props = op["request_body"]["properties"]
                    assert {p["name"] for p in props} == set(schema.get("properties", {})), key
                    for p in props:
                        assert p["required"] == (p["name"] in schema.get("required", [])), (key, p)
    assert visited == set(actual)
    assert len(actual) == manifest()["total"] == 108


def test_manifest_is_current():
    assert generator.generate() == manifest()


def test_recursive_references_and_parameter_overrides(tmp_path, monkeypatch):
    doc = {
        "components": {
            "parameters": {
                "ID": {"name": "id", "in": "path", "required": True, "schema": {"$ref": "#/components/schemas/ID"}},
                "Query": {"name": "filter", "in": "query", "required": True},
                "Alias": {"$ref": "#/components/parameters/ID"},
                "Header": {"name": "trace", "in": "header", "schema": {"type": "integer"}},
                "Cookie": {"name": "session", "in": "cookie", "required": True},
            },
            "schemas": {
                "ID": {"type": "integer"},
                "Node": {"type": "object", "properties": {"child": {"$ref": "#/components/schemas/Node"}}},
                "Payload": {"type": "object", "required": ["node"], "properties": {"node": {"$ref": "#/components/schemas/Node"}}},
            },
            "requestBodies": {"Body": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Payload"}}}}},
            "responses": {"Download": {"content": {"application/octet-stream": {"schema": {"type": "string"}}}}},
        },
        "paths": {"/things/{id}": {
            "parameters": [{"$ref": "#/components/parameters/Alias"}, {"$ref": "#/components/parameters/Query"}],
            "get": {
                "operationId": "getThing",
                "parameters": [{"name": "filter", "in": "query", "required": False}, {"$ref": "#/components/parameters/Header"}, {"$ref": "#/components/parameters/Cookie"}],
                "requestBody": {"$ref": "#/components/requestBodies/Body"},
                "responses": {"200": {"$ref": "#/components/responses/Download"}},
            },
        }},
    }
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/test.yaml").write_text(yaml.safe_dump(doc))
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "FILES", {"test": "test.yaml"})
    op = generator.generate()["operations"][0]
    assert op["path_parameters"] == [{"name": "id", "in": "path", "type": "integer", "required": True}]
    assert op["query_parameters"] == [{"name": "filter", "in": "query", "type": "string", "required": False}]
    assert op["header_parameters"][0]["type"] == "integer"
    assert op["cookie_parameters"][0]["required"] is True
    assert op["request_body"] == {"content_type": "application/json", "required": True, "properties": [{"name": "node", "type": "object", "required": True}], "schema": {"type": "object", "required": ["node"], "properties": {"node": {"type": "object", "properties": {"child": {}}}}}}
    assert op["returns_binary"] is True
    resolved = generator.deref({"$ref": "#/components/schemas/Node"}, doc)
    assert resolved == {"type": "object", "properties": {"child": {}}}
    assert "$ref" not in json.dumps(op)


def test_reference_json_pointer_escaping_and_invalid_targets():
    doc = {"a/b": {"~value": {"type": "boolean"}}}
    assert generator.deref({"$ref": "#/a~1b/~0value", "default": False}, doc) == {"type": "boolean", "default": False}
    with pytest.raises(ValueError, match="Only local references"):
        generator.deref({"$ref": "other.yaml#/schema"}, doc)
    with pytest.raises(KeyError):
        generator.deref({"$ref": "#/missing"}, doc)
