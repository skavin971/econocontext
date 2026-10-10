from econocontext.assembler.assembler import render
from econocontext.assembler.zones import pairs_intact, zoned_order
from econocontext.guard.fail_open import guarded
from econocontext.types import SegmentKind, Zone
from tests.unit.conftest import conversation, seg


def test_zones_render_in_order_and_pairs_never_split():
    window = conversation()
    pinned = seg("run-1:root", "note", SegmentKind.MESSAGE, "Constraint: keep the API stable",
                 pinned=True)
    window.insert(2, pinned)
    ordered = zoned_order(window)
    zones = [s.zone for s in ordered]
    assert zones == sorted(zones, key=[Zone.FROZEN, Zone.SLOW, Zone.WARM, Zone.VOLATILE].index)
    assert ordered[1].kind == SegmentKind.TASK and ordered[2].id == pinned.id
    assert pairs_intact(ordered)
    broken = [window[0], window[4], window[3]]  # a result before its call
    assert not pairs_intact(broken)


def test_identical_inputs_give_an_identical_manifest():
    a = render(conversation(), "ZONED", "fp")
    b = render(conversation(), "ZONED", "fp")
    assert a.manifest == b.manifest and a.manifest.hash == b.manifest.hash
    assert render(conversation(), "ZONED", "other").manifest.hash != a.manifest.hash


def test_fail_open_returns_the_host_default():
    def boom():
        raise RuntimeError("component failed")
    result, error, ms = guarded(boom, lambda: "host default", deadline_ms=50)
    assert result == "host default" and "component failed" in error and ms >= 0
