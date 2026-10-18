"""scripts/check_secrets.py: Purdue's access-grant fields are caught as JSON keys, and the same words
inside a call log's strings or in code are not."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_secrets", ROOT / "scripts" / "check_secrets.py")
check_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_secrets)


def test_access_grant_fields_are_caught():
    # The shape of an Open WebUI model entry (GET /api/models), with made-up ids. The keys are written
    # with single quotes and serialized here, so this file holds no such JSON key itself.
    entry = json.dumps({'info': {'user_id': 'u-1', 'access_control': {'read': {'group_ids': ['g-1'], 'user_ids': []}}}})
    assert len(check_secrets.GRANTS.findall(entry)) == 4


def test_the_same_words_elsewhere_are_not():
    # A call log holds tool output and code as JSON strings, where the keys' quotes are escaped.
    log = '{"content": "{\\"user_id\\": 7, \\"group_ids\\": []}"}'
    code = "user_id = request.user_id  # access_control is checked elsewhere"
    assert not check_secrets.GRANTS.findall(log) and not check_secrets.GRANTS.findall(code)
