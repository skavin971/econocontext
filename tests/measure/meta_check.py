"""Independent check of C_token: count the model's real linear layers instead of using Eq. 7.

Why it exists: Eq. 7 (measure/models.py) is a formula written from the paper. This builds the text
model from each saved config on PyTorch's meta device (shapes only, no weights downloaded), adds up
the parameters of every linear layer in the text decoder except the embeddings and lm_head, and
checks that 2 × that sum equals C_token exactly. It also lists the body parameters that are not
matmuls (norms, the Gated DeltaNet's short convolutions and gates), which Eq. 7 leaves out.

The text model is built from text_config, without the FP8 quantization_config (FP8 changes storage,
not FLOPs) and without the vision tower (our runs send no images). The multi-token-prediction layer
is not built by transformers' text model, so it is not counted either.

Run (needs torch, accelerate, transformers): .venv-clm/bin/python tests/measure/meta_check.py
Exit code 0 when every model matches; it skips (exit 0) without torch.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from measure.models import constants  # noqa: E402

try:
    import torch
    from accelerate import init_empty_weights
    from transformers import AutoConfig, AutoModelForCausalLM
except ImportError as exc:
    print(f"skipped: {exc}")
    sys.exit(0)


def main() -> int:
    failures = 0
    for path in sorted((ROOT / "measure" / "configs").glob("*.json")):
        entry = json.loads(path.read_text())
        config = dict(entry["config"])
        config.pop("quantization_config", None)
        text = AutoConfig.for_model(**{**config["text_config"], "model_type": config["text_config"]["model_type"]})
        with init_empty_weights():
            model = AutoModelForCausalLM.from_config(text)
        linear = other = embeddings = 0
        kinds = {}
        for name, module in model.named_modules():
            own = sum(p.numel() for p in module.parameters(recurse=False))
            if not own:
                continue
            if "embed_tokens" in name or name.endswith("lm_head"):
                embeddings += own
            elif isinstance(module, torch.nn.Linear):
                linear += own
                if module.bias is not None:
                    kinds["linear biases"] = kinds.get("linear biases", 0) + module.bias.numel()
            else:
                other += own
                kind = type(module).__name__
                kinds[kind] = kinds.get(kind, 0) + own
        expected = constants(entry["config"])["C_token"]
        ok = 2 * linear == expected
        failures += not ok
        print(f"{entry['hf_id']} @ {entry['revision'][:7]} ({type(model).__name__}):")
        print(f"  linear-layer parameters in the body: {linear:,}; x2 = {2 * linear:,}; Eq. 7 C_token = {expected:,}; "
              f"{'EQUAL' if ok else f'DIFFERENT by {2 * linear - expected:,}'}")
        print(f"  other body parameters (not matmuls, left out of C_token): {other:,} -> {kinds}")
        print(f"  embeddings + lm_head (excluded): {embeddings:,}")
        print(f"  all body parameters (linear + other): {linear + other:,}; x2 = {2 * (linear + other):,}")
        mtp = [n for n, _ in model.named_modules() if "mtp" in n.lower()]
        print(f"  multi-token-prediction modules built: {len(mtp)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
