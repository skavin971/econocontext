"""measure/flops.py without a tokenizer: Eq. 9 per call, run totals, summary calls in the headline and
in CLM's convention, and the vLLM 0.30 message parsing."""

import pytest

from measure.flops import eq9, run_flops, vllm_messages

C_TOKEN, C_ATTN = 48_701_112_320, 393_216


def ids(n, start=0):
    return list(range(start, start + n))


def test_eq9_by_hand():
    lin, attn = eq9(P=100, R=32, G=10, c_token=2.0, c_attn=4.0)
    assert lin == 2.0 * (100 - 32 + 10)
    assert attn == 4.0 * (0.5 * (100 ** 2 - 32 ** 2) + 10 * 100 + 0.5 * 10 ** 2)


def test_a_run_sums_its_calls_and_keeps_summary_calls_in_the_cache_sequence():
    conversation = ids(64)
    calls = [{"call_no": 1, "kind": "agent", "ids": conversation, "server_ids": None, "G": 5},
             {"call_no": 2, "kind": "summary", "ids": conversation + ids(16, 900), "server_ids": None, "G": 50},
             {"call_no": 3, "kind": "agent", "ids": conversation + ids(16, 900) + ids(16, 500), "server_ids": None,
              "G": 7}]
    result = run_flops(calls, C_TOKEN, C_ATTN)
    rows, t = result["rows"], result["totals"]
    assert [r["R"] for r in rows] == [0, 64, 80]                 # the summary prompt's blocks are reused by call 3
    assert t["P"] == 64 + 80 + 96 and t["R"] == 144 and t["U"] == t["P"] - t["R"] and t["G"] == 62
    assert t["F"] == pytest.approx(sum(sum(eq9(r["P"], r["R"], c["G"], C_TOKEN, C_ATTN)) for r, c in zip(rows, calls)))
    assert t["F_summary"] == pytest.approx(rows[1]["F"]) and t["summary_calls"] == 1
    assert t["hit_share"] == pytest.approx(144 / 240)
    # CLM's convention: the summary call is fully prefilled and leaves nothing in the cache.
    assert [r["R_clm_aux"] for r in rows] == [0, 0, 64]
    assert t["F_clm_aux"] == pytest.approx(sum(eq9(64, 0, 5, C_TOKEN, C_ATTN)) + sum(eq9(80, 0, 50, C_TOKEN, C_ATTN))
                                           + sum(eq9(96, 64, 7, C_TOKEN, C_ATTN)))
    assert t["F_clm_aux"] > t["F"] and t["F_server"] is None


def test_the_server_variant_uses_the_servers_own_ids():
    a, b = ids(48), ids(48)
    b_server = list(b)
    b_server[20] = 99_999                                        # the server's prompt differs inside block 2
    calls = [{"call_no": 1, "kind": "agent", "ids": a, "server_ids": a, "G": 1},
             {"call_no": 2, "kind": "agent", "ids": b, "server_ids": b_server, "G": 1}]
    rows = run_flops(calls, C_TOKEN, C_ATTN)["rows"]
    assert (rows[1]["R"], rows[1]["R_server"]) == (32, 16)      # 48 tokens repeated: the last block recomputed


def test_messages_are_parsed_as_vllm_030_parses_them():
    messages = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": None, "reasoning_content": "dropped by vLLM",
                 "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}},
                                {"id": "c2", "type": "function", "function": {"name": "bash", "arguments": "not json"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "a.txt", "extra": "ignored"},
                {"role": "assistant", "content": "done", "reasoning": "kept by vLLM"}]
    parsed = vllm_messages(messages)
    assert "reasoning_content" not in parsed[1] and "reasoning" not in parsed[1]
    assert [c["function"]["arguments"] for c in parsed[1]["tool_calls"]] == [{"command": "ls"}, {}]
    assert parsed[2] == {"role": "tool", "content": "a.txt", "tool_call_id": "c1"}
    assert parsed[3]["reasoning"] == parsed[3]["reasoning_content"] == "kept by vLLM"
