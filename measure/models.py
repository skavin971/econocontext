"""Model constants for prefix-reuse FLOPs (CLM, Shao et al., arXiv 2609.37725, Appendix C, Eq. 7).

Why it exists: Eq. 9 needs two numbers per model, both from its architecture fields:
  C_token  FLOPs per token for the weights: 2 per weight of every matmul in the body (the MLP, the
           full-attention projections, the Gated DeltaNet projections). Embeddings, lm_head, norms,
           the short convolutions and the multi-token-prediction head are excluded, as in CLM.
  C_attn   FLOPs per (query, key) pair of full attention: 4 · L_attn · h_q · d_h.
It covers the Qwen3.5/3.6/3.8 hybrid text model: full attention every few layers (query gated:
2·h_q·d_h), Gated DeltaNet in the others, a dense gated MLP. A field it does not model (experts,
another layer type, attention biases) raises instead of guessing. FP8 weights don't change the
counts. The saved configs (measure/configs/, config.json only, no weights) make this offline.

Run: .venv/bin/python -m measure.models    (rewrites measure/model_constants.json)
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEASURED = "Qwen/Qwen3.8-27B-FP8"   # qwen3.8:27b on Purdue GenAI Studio (step 1)


def constants(config: dict) -> dict:
    c = config.get("text_config") or config
    moe = [k for k in c if any(word in k for word in ("expert", "moe", "router"))]
    if moe:
        raise ValueError(f"mixture-of-experts fields {moe}: only dense MLPs are modelled")
    if c.get("attention_bias"):
        raise ValueError("attention biases are not modelled")
    L, d, d_ff = c["num_hidden_layers"], c["hidden_size"], c["intermediate_size"]
    types = c.get("layer_types") or ["full_attention"] * L
    if len(types) != L or set(types) - {"full_attention", "linear_attention"}:
        raise ValueError(f"layer types {sorted(set(types))} for {L} layers: not modelled")
    L_attn, L_lin = types.count("full_attention"), types.count("linear_attention")
    h_q, h_kv = c["num_attention_heads"], c["num_key_value_heads"]
    d_h = c.get("head_dim") or d // h_q
    q_width = (2 if c.get("attn_output_gate") else 1) * h_q * d_h      # the gate doubles q_proj
    terms = {"mlp": 6 * L * d * d_ff,                                  # gate, up and down projections
             "full_attention_projections": L_attn * 2 * d * (q_width + 2 * h_kv * d_h) + L_attn * 2 * h_q * d_h * d,
             "gated_deltanet_projections": 0}
    if L_lin:
        h_k, d_k = c["linear_num_key_heads"], c["linear_key_head_dim"]
        h_v, d_v = c["linear_num_value_heads"], c["linear_value_head_dim"]
        terms["gated_deltanet_projections"] = (L_lin * 2 * d * (2 * h_k * d_k + 2 * h_v * d_v + 2 * h_v)
                                               + L_lin * 2 * h_v * d_v * d)
    fields = {"L": L, "d": d, "d_ff": d_ff, "L_attn": L_attn, "L_lin": L_lin, "h_q": h_q, "h_kv": h_kv, "d_h": d_h,
              "attn_output_gate": bool(c.get("attn_output_gate"))}
    if L_lin:
        fields.update(h_k=h_k, h_v=h_v, d_k=d_k, d_v=d_v)
    return {"C_token": sum(terms.values()), "C_attn": 4 * L_attn * h_q * d_h, "terms": terms, "fields": fields}


def saved(hf_id: str) -> dict:
    """A saved config: {"hf_id", "revision", "config"}."""
    return json.loads((HERE / "configs" / f"{hf_id.replace('/', '__')}.json").read_text())


def load(hf_id: str = MEASURED) -> dict:
    """The resolved constants of a model, from measure/model_constants.json."""
    return json.loads((HERE / "model_constants.json").read_text())["models"][hf_id]


def main() -> None:
    models = {}
    for path in sorted((HERE / "configs").glob("*.json")):
        entry = json.loads(path.read_text())
        models[entry["hf_id"]] = {"revision": entry["revision"], **constants(entry["config"])}
    out = {"measured_model": MEASURED, "source": "Eq. 7 of CLM Appendix C, from each model's config.json",
           "models": models}
    (HERE / "model_constants.json").write_text(json.dumps(out, indent=1) + "\n")
    for hf_id, m in models.items():
        terms = ", ".join(f"{k} {v / 1e9:.2f}e9" for k, v in m["terms"].items())
        print(f"{hf_id} @ {m['revision'][:7]}: C_token {m['C_token']:,} ({m['C_token'] / 1e9:.2f}e9; {terms}), "
              f"C_attn {m['C_attn']:,}")


if __name__ == "__main__":
    main()
