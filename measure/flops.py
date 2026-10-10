"""Prefix-reuse FLOPs of a run, from the gateway's call log (CLM, arXiv 2609.37725, App. C, Eq. 9).

Why it exists: our cost measure for open models, computed the same way for every arm and agent from
calls.jsonl alone. For each call of a run:
  1. Rebuild the prompt's token ids from the logged request, using the model's own tokenizer and
     chat template. Messages are parsed the way vLLM 0.30 parses them:
     - tool-call arguments become dicts;
     - reasoning is read only from a `reasoning` field;
     - the template options are the request's reasoning_effort and chat_template_kwargs.
  2. Validate the rebuild against the server's own prompt ids (the gateway asks for them). It
     passes only if identical, or if the difference is confined to the tools block before the
     first user message (Purdue reorders tool-schema keys) with |ΔP| <= 1. Anything else is
     reported as a mismatch, and the run must be reviewed before its numbers are used.
  3. R_t: the prefix cache over the run's rebuilt prompts (cache_sim.py). G_t: the call's
     completion_tokens, thinking included.
  4. F_t = C_token·(U_t + G_t) + C_attn·[½(P_t² − R_t²) + G_t·P_t + ½·G_t²], with U_t = P_t − R_t.

Per run (docs/tier-a-decisions.md):
- headline: summary calls in the run's cache sequence;
- the summary calls' own FLOPs;
- a CLM-convention variant: summary calls fully prefilled and kept out of the cache;
- the headline computed on Purdue's actual prompt ids.
"""

import json
from pathlib import Path

from .cache_sim import cached_prefix


def vllm_messages(messages: list[dict]) -> list[dict]:
    """The messages as vLLM 0.30's chat parser hands them to the template."""
    out = []
    for m in messages:
        message = {"role": m["role"], "content": m.get("content")}
        if m.get("name") is not None:
            message["name"] = m["name"]
        if m["role"] == "assistant":
            calls = []
            for call in m.get("tool_calls") or []:
                function = dict(call.get("function") or {})
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments.strip() else {}
                    except ValueError:
                        arguments = {}
                function["arguments"] = arguments if isinstance(arguments, dict) else {}
                calls.append({**call, "function": function})
            if calls:
                message["tool_calls"] = calls
            if m.get("reasoning") is not None:          # vLLM reads `reasoning`; `reasoning_content` is dropped
                message["reasoning"] = message["reasoning_content"] = m["reasoning"]
        if m["role"] == "tool":
            message["tool_call_id"] = m.get("tool_call_id")
        out.append(message)
    return out


def prompt_ids(tokenizer, request: dict) -> list[int]:
    options = dict(request.get("chat_template_kwargs") or {})
    if "reasoning_effort" in request:
        options["reasoning_effort"] = request["reasoning_effort"]
    out = tokenizer.apply_chat_template(vllm_messages(request["messages"]), tools=request.get("tools") or None,
                                        add_generation_prompt=True, tokenize=True, **options)
    return list(out["input_ids"] if hasattr(out, "keys") else out)


def validate(tokenizer, local: list[int], server: list[int] | None) -> tuple[bool, str]:
    """Kavin's rule: identical, or different only inside the tools block before the first user message
    (the same tools, keys in another order) with |ΔP| <= 1."""
    if server is None:
        return False, "no server ids"
    if local == server:
        return True, "identical"
    marker = tokenizer.encode("<|im_start|>user\n", add_special_tokens=False)

    def first_user(ids):
        return next((i for i in range(len(ids) - len(marker) + 1) if ids[i:i + len(marker)] == marker), None)
    s, l = first_user(server), first_user(local)
    if s is None or l is None or server[s:] != local[l:]:
        at = next((k for k, (x, y) in enumerate(zip(server, local)) if x != y), min(len(server), len(local)))
        return False, (f"mismatch at token {at} (P server {len(server)}, local {len(local)}): "
                       f"server {tokenizer.decode(server[at:at + 16])!r} vs local {tokenizer.decode(local[at:at + 16])!r}")

    def split(ids):
        text = tokenizer.decode(ids)
        i, j = text.find("<tools>"), text.find("</tools>")
        if not 0 <= i < j:
            return None
        lines = text[i + len("<tools>"):j].strip().splitlines()
        return text[:i], [json.loads(line) for line in lines], text[j:]
    a, b = split(server[:s]), split(local[:l])
    delta = len(server) - len(local)
    if a and b and a[0] == b[0] and a[2] == b[2] and a[1] == b[1]:
        if abs(delta) <= 1:
            return True, f"tools block only (dP {delta:+d})"
        return False, f"tools block only (same tools, keys reordered), but dP {delta:+d} exceeds the allowed 1"
    return False, f"mismatch before the first user message (dP {delta:+d})"


def eq9(P: int, R: int, G: int, c_token: float, c_attn: float) -> tuple[float, float]:
    """(linear FLOPs, attention FLOPs) of one call."""
    return c_token * (P - R + G), c_attn * (0.5 * (P * P - R * R) + G * P + 0.5 * G * G)


def run_flops(calls: list[dict], c_token: float, c_attn: float) -> dict:
    """calls: in order, each {"call_no", "kind", "ids", "server_ids", "G"}. Per-call rows and run totals."""
    R = cached_prefix([c["ids"] for c in calls])
    agent_only = cached_prefix([c["ids"] for c in calls if c["kind"] != "summary"])
    R_clm = [0 if c["kind"] == "summary" else agent_only.pop(0) for c in calls]   # CLM's aux convention
    server = [c["server_ids"] for c in calls]
    R_srv = cached_prefix(server) if all(ids is not None for ids in server) else None
    rows, totals = [], {"calls": len(calls), "summary_calls": 0, "P": 0, "R": 0, "U": 0, "G": 0, "F_lin": 0.0,
                        "F_attn": 0.0, "F": 0.0, "F_summary": 0.0, "F_clm_aux": 0.0, "F_server": 0.0 if R_srv else None}
    for i, c in enumerate(calls):
        P, G = len(c["ids"]), c["G"]
        lin, attn = eq9(P, R[i], G, c_token, c_attn)
        clm = sum(eq9(P, R_clm[i], G, c_token, c_attn))
        row = {"call_no": c["call_no"], "kind": c["kind"], "P": P, "R": R[i], "U": P - R[i], "G": G,
               "F_lin": lin, "F_attn": attn, "F": lin + attn, "R_clm_aux": R_clm[i]}
        row["P_server"], row["R_server"] = (len(server[i]), R_srv[i]) if R_srv else (None, None)
        if R_srv:
            totals["F_server"] += sum(eq9(len(server[i]), R_srv[i], G, c_token, c_attn))
        rows.append(row)
        for key in ("P", "R", "U", "G", "F_lin", "F_attn", "F"):
            totals[key] += row[key]
        totals["F_clm_aux"] += clm
        if c["kind"] == "summary":
            totals["summary_calls"] += 1
            totals["F_summary"] += lin + attn
    totals["hit_share"] = totals["R"] / totals["P"] if totals["P"] else 0.0
    return {"rows": rows, "totals": totals}


def measure_log(calls_log: Path, tokenizer, c_token: float, c_attn: float) -> dict:
    """Read a run's calls.jsonl, rebuild and validate every answered call, and compute its FLOPs."""
    calls, checks, retries, skipped = [], [], 0, []
    for line in Path(calls_log).read_text().splitlines():
        row = json.loads(line)
        if not row.get("call_no"):
            continue                                       # a cap refusal: nothing was computed
        retries += row.get("retries") or 0
        response, usage = row.get("response"), row.get("usage") or {}
        if row.get("status") != 200 or not isinstance(response, dict) or "completion_tokens" not in usage:
            skipped.append(row["call_no"])
            continue
        ids = prompt_ids(tokenizer, row["request"])
        server = response.get("prompt_token_ids")
        ok, note = validate(tokenizer, ids, server)
        checks.append({"call_no": row["call_no"], "ok": ok, "note": note, "P_local": len(ids),
                       "P_server": len(server) if server else None, "prompt_tokens": usage.get("prompt_tokens")})
        calls.append({"call_no": row["call_no"], "kind": row.get("kind", "agent"), "ids": ids, "server_ids": server,
                      "G": usage["completion_tokens"]})
    result = run_flops(calls, c_token, c_attn)
    result["validation"] = checks
    result["totals"].update(retries=retries, unanswered=skipped,
                            valid=sum(c["ok"] for c in checks), invalid=sum(not c["ok"] for c in checks))
    return result
