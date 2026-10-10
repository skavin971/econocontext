"""CLM Appendix C reproduced: the Qwen3.6-27B constants (Eq. 7) and the worked example (Eq. 9).
Offline: the config is the saved one in measure/configs/."""

import pytest

from measure.flops import eq9
from measure.models import constants, saved

QWEN36 = constants(saved("Qwen/Qwen3.6-27B")["config"])


def test_the_qwen36_constants():
    assert QWEN36["C_token"] == pytest.approx(48.70e9, rel=1e-3)
    terms = QWEN36["terms"]
    assert round(terms["mlp"] / 1e9, 2) == 34.23
    assert round(terms["full_attention_projections"] / 1e9, 2) == 3.36
    assert round(terms["gated_deltanet_projections"] / 1e9, 2) == 11.12
    assert QWEN36["C_attn"] == 393_216


def test_qwen38_has_the_same_architecture_and_constants():
    qwen38 = constants(saved("Qwen/Qwen3.8-27B-FP8")["config"])
    assert (qwen38["C_token"], qwen38["C_attn"], qwen38["terms"]) == (QWEN36["C_token"], QWEN36["C_attn"], QWEN36["terms"])


@pytest.mark.parametrize("R,flops,avoided", [(18_000, 1.41e14, 0.87), (10_000, 5.74e14, 0.47), (0, 10.81e14, 0.0)])
def test_the_worked_example(R, flops, avoided):
    # P = 20,000 prompt tokens, G = 500 generated, three cache states (CLM Appendix C).
    total = sum(eq9(20_000, R, 500, QWEN36["C_token"], QWEN36["C_attn"]))
    none = sum(eq9(20_000, 0, 500, QWEN36["C_token"], QWEN36["C_attn"]))
    assert float(f"{total:.3g}") == float(f"{flops:.3g}")      # 3 significant figures (10.81e14 -> 10.8e14)
    assert round(1 - total / none, 2) == avoided


def test_unmodelled_architectures_are_refused():
    base = saved("Qwen/Qwen3.6-27B")["config"]["text_config"]
    with pytest.raises(ValueError, match="mixture-of-experts"):
        constants({**base, "num_experts": 64})
    with pytest.raises(ValueError, match="layer types"):
        constants({**base, "layer_types": ["sliding_attention"] * 64})
    with pytest.raises(ValueError, match="biases"):
        constants({**base, "attention_bias": True})
