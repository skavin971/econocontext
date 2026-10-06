"""One real Jev call through the engine (same as the live test), printing the raw reply
minus nothing secret, so we can see what `usage` holds and what the request weighed."""
import json
import sys
import tempfile
import time

sys.path.insert(0, ".")
from econocontext.engine import EconoContext
from econocontext.planner import jev_planner
from econocontext.types import HostRequest
from tests.test_planner_jev import admit
from tests.unit.conftest import CONFIG_DIR, FakeHost, conversation

real = jev_planner.urlopen
seen = {}


def spy(request, timeout):
    seen["request_bytes"] = len(request.data)
    t = time.time()
    with real(request, timeout=timeout) as response:
        body = response.read()
    seen["seconds"] = round(time.time() - t, 3)
    seen["reply"] = json.loads(body)
    from io import BytesIO
    return BytesIO(body)


jev_planner.urlopen = spy
with tempfile.TemporaryDirectory() as tmp:
    eco = EconoContext(str(CONFIG_DIR), FakeHost(), "run-1", host_name="test", arm="econo",
                       db_path=f"{tmp}/db.sqlite3", mode="observe", jev=True)
    eco.plan_prompt("run-1:root", HostRequest("run-1:root", conversation()))
    pred = json.loads(admit(eco)["prediction"])
print(json.dumps({"prediction": pred, **seen}, indent=2))
