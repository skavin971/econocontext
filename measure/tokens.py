"""A prompt's size as the server will count it, for an agent to check before it sends.

Why it exists: a token budget (step 10c) is enforced on the prompt the server will see. This is the
same rebuild as measure/flops.py (vLLM's message parsing, the model's own chat template and the
request's template options), so the count is the server's prompt_tokens, apart from Purdue's random
reordering of tool-schema keys (a few tokens, inside the tools block; see docs/tier-a-decisions.md).
count_text is the same tokenizer on a piece of text (no template): EconoContext's token counts (step 12a),
so the method and the measure count with one function. Agents and the method may import this module;
measure/ never imports the method.

Use: count_prompt(messages, tools, "qwen3.8:27b", reasoning_effort="medium",
                  chat_template_kwargs={"enable_thinking": True})
     count_text("some text", "qwen3.8:27b")
"""

import functools

from .flops import prompt_ids
from .models import SERVED, load


@functools.cache
def tokenizer(hf_id: str):
    """The model's own tokenizer and chat template, at the revision measured in model_constants.json."""
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(hf_id, revision=load(hf_id)["revision"])


def resolve(model: str) -> str:
    """The Hugging Face id of a provider's model id (or of a Hugging Face id) that measure/ has measured."""
    hf_id = SERVED.get(model, model)
    try:
        load(hf_id)
    except KeyError:
        raise ValueError(f"no measured constants for model {model!r}: add it to measure/ first") from None
    return hf_id


def count_text(text: str, model: str) -> int:
    """Tokens of a text by the model's own tokenizer, without special tokens or a chat template."""
    return len(tokenizer(resolve(model)).encode(text, add_special_tokens=False)) if text else 0


def count_prompt(messages: list[dict], tools: list[dict] | None, model: str, **template) -> int:
    """Prompt tokens of a request. `model` is the provider's id (e.g. qwen3.8:27b) or a Hugging Face id;
    `template` holds the request's template options (reasoning_effort, chat_template_kwargs)."""
    return len(prompt_ids(tokenizer(resolve(model)), {"messages": messages, "tools": tools, **template}))
