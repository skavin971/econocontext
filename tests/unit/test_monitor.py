from econocontext.monitor.cache_belief import CacheBelief
from econocontext.types import SegmentKind
from tests.unit.conftest import seg


def big(agent, native, n, char="x"):
    return seg(agent, native, SegmentKind.MESSAGE, char * (4 * n))


def test_predicts_the_shared_prefix_within_the_ttl_and_above_the_minimum():
    belief = CacheBelief(ttl_seconds=300, min_cacheable_tokens=4096)
    a = [big("A", "1", 3000), big("A", "2", 2000)]
    assert belief.predict("A", a, now=0) == 0            # nothing sent yet
    belief.sent("A", a, now=0)
    b = a + [big("A", "3", 500)]
    assert belief.predict("A", b, now=10) == 5000         # the whole previous request
    assert belief.predict("A", b, now=1000) == 0          # past the TTL
    c = [a[0], big("A", "9", 2000, char="y")]  # different content after 3000 tokens
    assert belief.predict("A", c, now=10) == 0            # 3000 shared < 4096 minimum


def test_prediction_is_corrected_from_reported_usage():
    belief = CacheBelief(ttl_seconds=300, min_cacheable_tokens=0)
    belief.correct("A", predicted=1000, reported=600)
    belief.correct("A", predicted=1000, reported=1000)
    assert belief.state("A").observed_hit_ratio == 0.8
