"""Gemini's generateContent format: paths and usage."""

from omnigent_layer import gemini_wire


def test_model_calls_are_read_from_both_path_shapes():
    assert gemini_wire.model_call("/v1beta1/publishers/google/models/gemini-3.6-flash"
                                  ":streamGenerateContent") == ("gemini-3.6-flash",
                                                                "streamGenerateContent")
    assert gemini_wire.model_call("/v1beta/models/gemini-3.6-flash:generateContent?alt=sse") == \
        ("gemini-3.6-flash", "generateContent")
    assert gemini_wire.model_call("/v1beta1/projects/p/locations/global/publishers/google/"
                                  "models/m:countTokens") == ("m", "countTokens")
    assert gemini_wire.model_call("/v1beta1/something") == (None, None)


def test_usage_from_the_vertex_probe():
    # The real reply of 2026-09-30: thoughts outside candidates, nothing cached.
    usage = gemini_wire.to_usage({"promptTokenCount": 7, "candidatesTokenCount": 1,
                                  "totalTokenCount": 99, "thoughtsTokenCount": 91})
    assert (usage.uncached_input, usage.cache_read, usage.output, usage.reasoning) == (7, 0, 92, 91)
    assert usage.cache_write is None and not usage.cache_write_applicable


def test_cached_and_tool_prompt_tokens():
    usage = gemini_wire.to_usage({"promptTokenCount": 1000, "toolUsePromptTokenCount": 50,
                                  "cachedContentTokenCount": 600, "candidatesTokenCount": 20,
                                  "totalTokenCount": 1070})
    assert (usage.uncached_input, usage.cache_read, usage.output, usage.reasoning) == (450, 600, 20, 0)


def test_missing_counts_are_unknown_unless_the_total_confirms_zero():
    confirmed = gemini_wire.to_usage({"promptTokenCount": 10, "candidatesTokenCount": 5,
                                      "totalTokenCount": 15})
    assert confirmed.output == 5 and confirmed.reasoning == 0
    unconfirmed = gemini_wire.to_usage({"promptTokenCount": 10, "candidatesTokenCount": 5})
    assert unconfirmed.output is None
    nothing = gemini_wire.to_usage(None)
    assert nothing.uncached_input is None and nothing.output is None


def test_the_last_complete_usage_of_a_stream_wins():
    chunks = [{"usageMetadata": {"trafficType": "ON_DEMAND"}},
              {"usageMetadata": {"promptTokenCount": 3, "totalTokenCount": 3}}, {}, "junk"]
    assert gemini_wire.extract_usage(chunks) == {"promptTokenCount": 3, "totalTokenCount": 3}
    assert gemini_wire.extract_usage([]) is None
