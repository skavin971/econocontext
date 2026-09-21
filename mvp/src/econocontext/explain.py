"""Render one run as an ordered, readable narrative of what actually happened."""

import json

WIDTH = 96
BAR = "=" * WIDTH


def money(value):
    return "unknown" if value is None else f"${value:,.6f}"


def block(title, rows):
    out = [f"+-- {title} " + "-" * max(0, WIDTH - len(title) - 5)]
    out.extend("|  " + row for row in rows)
    out.append("+" + "-" * (WIDTH - 1))
    return out


def wrap(text, indent=6, limit=WIDTH - 10):
    text = " ".join(str(text).split())
    pad, out = " " * indent, []
    while text:
        if len(text) <= limit:
            out.append(pad + text)
            break
        cut = text.rfind(" ", 0, limit)
        cut = cut if cut > 0 else limit
        out.append(pad + text[:cut])
        text = text[cut:].lstrip()
    return out


def describe_call(payload):
    """One line per tool call the model asked for."""
    rows = []
    for call in (payload or {}).get("tool_calls") or []:
        function = call.get("function", {})
        arguments = function.get("arguments")
        rows.append(f"{function.get('name')}({arguments})")
    if not rows and (payload or {}).get("content"):
        rows.append(str(payload["content"]))
    return rows


def candidate_rows(candidate, selected_id):
    estimate = candidate.get("estimate") or {}
    breakdown = estimate.get("breakdown") or {}
    mode = candidate.get("mode")
    view = f"/{candidate['view']}" if candidate.get("view") else ""
    target = candidate.get("worker_id") or candidate.get("result_id") or "new child worker"
    mark = "  <== SELECTED" if candidate.get("id") == selected_id else ""
    latency = estimate.get("latency")
    rows = [
        f"  {mode}{view}  ->  {target}{mark}",
        f"      estimate   cost {money(estimate.get('cost'))}   latency {latency:.2f}s"
        if latency is not None
        else "      estimate",
        f"      tokens     {estimate.get('tokens')} predicted",
        f"      breakdown  prepare {money(breakdown.get('preparation'))}"
        f"  execute {money(breakdown.get('execution'))}"
        f"  integrate {money(breakdown.get('integration'))}"
        f"  extra {money(breakdown.get('additional'))}",
        f"      basis      {estimate.get('basis')} via {estimate.get('provenance')}"
        f"   samples {estimate.get('samples')}   uncertainty {estimate.get('uncertainty')}",
    ]
    for reason in candidate.get("rejections") or []:
        rows.append(f"      REJECTED   {reason}")
    return rows


def message_rows(messages, seen):
    """Show each message once: the prompt in full, then only what each turn adds."""
    rows = []
    for index, message in enumerate(messages):
        if index < seen:
            continue
        role = message.get("role", "?")
        rows.append(f"  [{role}]")
        content = message.get("content")
        if content:
            rows.extend(wrap(content, indent=6))
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            rows.extend(wrap(f"-> {function.get('name')}({function.get('arguments')})", indent=6))
    return rows


class Narrator:
    """Renders events one at a time, so batch and live stepping share a format."""

    def __init__(self, artifacts, limits, reconstruct=None):
        self.artifacts = artifacts
        self.reconstruct = reconstruct
        self.budget = (limits or {}).get("context_tokens") or 0
        self.workers, self.shown, self.step = {}, {}, 0

    def label(self, worker_id, add=False):
        if add:
            return self.workers.setdefault(
                worker_id, "root" if not self.workers else f"child-{len(self.workers)}"
            )
        return self.workers.get(worker_id, "root")

    def assembly(self, event):
        who = self.label(event["worker_id"], add=True)
        tokens = event.get("tokens") or 0
        fill = f"{100 * tokens / self.budget:.1f}%" if self.budget else "n/a"
        rows = [
            f"{tokens:,} tokens assembled   context fill {fill} of {self.budget:,}",
            f"manifest {event.get('manifest')}   took {1000 * (event.get('duration') or 0):.1f}ms",
        ]
        if self.reconstruct:
            try:
                body = self.reconstruct(event["manifest"])
                messages = (body.get("body") or body).get("messages") or []
                seen = self.shown.get(event["worker_id"], 0)
                added = message_rows(messages, seen)
                self.shown[event["worker_id"]] = len(messages)
                if added:
                    rows += ["", "full prompt:" if seen == 0 else "added to this context:"]
                    rows += added
            except Exception as exc:
                rows.append(f"(could not reconstruct request: {exc})")
        return f"ASSEMBLER  build request  [{who}]", rows

    def planning(self, event):
        candidates = event.get("candidates") or []
        rows = [
            f"trigger: {event.get('trigger')}",
            f"operation: {event.get('operation_id')}",
            f"considered {len(candidates)} candidate plan(s):",
            "",
        ]
        for candidate in candidates:
            rows.extend(candidate_rows(candidate, event.get("selected")))
            rows.append("")
        chosen = next((c for c in candidates if c.get("id") == event.get("selected")), None)
        rows.append(
            "decision: "
            + (
                f"{chosen['mode']}{'/' + chosen['view'] if chosen.get('view') else ''}"
                f" (cheapest feasible of {len(candidates)})"
                if chosen
                else "NO FEASIBLE PLAN"
            )
        )
        rows.append(f"planning took {1000 * (event.get('duration') or 0):.1f}ms")
        return "PLANNER -> CANDIDATES -> COST MODEL -> OPTIMIZER", rows

    def attempt(self, event):
        who = self.label(event["worker_id"])
        raw = event.get("raw_usage") or {}
        usage = event.get("usage") or {}
        rows = []
        if raw:
            prompt = raw.get("prompt_tokens") or 0
            cached = usage.get("cached") or 0
            reasoning = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            share = f"{100 * cached / prompt:.1f}%" if prompt else "0%"
            rows += [
                f"model {event.get('name')}   phase {event.get('phase')}",
                f"INPUT   {prompt:,} prompt tokens   {cached:,} served from cache ({share})",
                f"OUTPUT  content {raw.get('completion_tokens')}"
                f"   reasoning {reasoning}   billed {usage.get('output')}",
            ]
            for row in describe_call(self.artifacts(event.get("response"))):
                rows.append("  ->  " + row[: WIDTH - 12])
            rows.append(
                f"cost {money(event.get('cost'))}"
                f"   latency {event.get('duration', 0):.3f}s"
                f"   status {event.get('status')}"
            )
            title = f"BACKEND  model call  [{who}]"
        else:
            payload = self.artifacts(event.get("response"))
            rows.append(f"tool {event.get('name')}   status {event.get('status')}")
            rows.append(
                f"request  {json.dumps(self.artifacts(event.get('request')))[: WIDTH - 14]}"
            )
            summary = payload
            if isinstance(payload, dict):
                summary = payload.get("output") or payload.get("text") or payload
            rows.extend(wrap(json.dumps(summary)[:600], indent=2))
            title = f"ADAPTER  tool call  [{who}]"
        if event.get("error"):
            rows.append(f"ERROR {event['error']}")
        return title, rows

    def render_event(self, event):
        """Return rendered lines for one event, or [] for kinds we do not narrate."""
        kind = event.get("kind")
        if kind == "assembly":
            title, rows = self.assembly(event)
        elif kind == "planning":
            title, rows = self.planning(event)
        elif kind == "attempt":
            title, rows = self.attempt(event)
        elif kind == "selection":
            plan = event.get("plan") or {}
            estimate = plan.get("estimate") or {}
            title, rows = (
                "MANAGER  plan committed",
                [
                    f"{plan.get('mode')}{'/' + plan['view'] if plan.get('view') else ''}"
                    f"   predicted {money(estimate.get('cost'))}"
                    f"   predicted tokens {estimate.get('tokens')}",
                    f"evidence attached: {plan.get('evidence')}",
                ],
            )
        elif kind == "assembly_rejected":
            title, rows = (
                "ASSEMBLER  plan rejected",
                [f"plan {event.get('plan_id')}", f"reason: {event.get('reason')}"],
            )
        elif kind == "evidence":
            title, rows = (
                "MEMORY  store evidence",
                [
                    f"source {event.get('source')}   {event.get('bytes')} bytes",
                    f"evidence id {event.get('evidence_id')}",
                    f"version {event.get('version')}",
                ],
            )
        elif kind == "operation_outcome":
            title, rows = (
                "MANAGER  operation finished",
                [
                    f"operation {event.get('operation_id')} delivered via {event.get('mode')}",
                    f"stored result {event.get('result_id')}",
                ],
            )
        elif kind == "integration_complete":
            title, rows = (
                "MANAGER  root integrated result",
                [f"operation {event.get('operation_id')}  plan {event.get('plan_id')}"],
            )
        elif kind == "operation_abandoned":
            title, rows = (
                "MANAGER  optimisation abandoned",
                [
                    f"scope {event.get('scope')}",
                    f"reason: {event.get('reason')}",
                    "the literal result answers the call; the run is unaffected",
                ],
            )
        elif kind == "representation":
            inline = event.get("inline_tokens") or 0
            delivered = event.get("delivered_tokens") or 0
            saved = inline - delivered
            title, rows = (
                "MANAGER  answered with a bounded view",
                [
                    f"the literal result would have cost {inline:,} tokens;"
                    f" the delivered answer costs {delivered:,}",
                    f"saved {saved:,} tokens"
                    f" ({100 * saved / inline if inline else 0:.0f}%)"
                    f" via {event.get('mode')}"
                    f"   excerpt {event.get('excerpt_chars')} chars",
                    "complete bytes stay in the store behind the original reference",
                ],
            )
        elif kind == "context_pressure":
            title, rows = (
                "LOOP  root under context pressure",
                [
                    f"{event.get('tokens'):,} of {event.get('budget'):,} tokens"
                    f"   ({100 * (event.get('fill') or 0):.1f}% full)",
                    "delegation is now offered for derived observations",
                ],
            )
        else:
            return []
        self.step += 1
        return block(f"STEP {self.step}  {title}", rows) + [""]


def render(run, metrics, events, artifacts, limits, reconstruct=None):
    request = run.get("request") or {}
    task = request.get("task") or {}
    context_budget = limits.get("context_tokens") or 0
    lines = [
        BAR,
        f"RUN {run['id']}",
        f"  adapter {task.get('adapter')}   method {request.get('method')}"
        f"   status {run.get('status')}   verification {run.get('verification')}",
        f"  limits  {limits.get('max_attempts')} attempts"
        f"   {context_budget:,} context tokens"
        f"   {limits.get('output_tokens')} output tokens"
        f"   {limits.get('max_children')} children",
        BAR,
        "",
    ]
    narrator = Narrator(artifacts, limits, reconstruct)
    for event in events:
        lines.extend(narrator.render_event(event))

    tokens = metrics.get("tokens") or {}
    total_input = (tokens.get("uncached") or 0) + (tokens.get("cached") or 0)
    cache_share = 100 * (tokens.get("cached") or 0) / total_input if total_input else 0
    lines += [
        BAR,
        "SUMMARY",
        f"  attempts {metrics.get('attempts')}"
        f"  (model {metrics.get('model_attempts')}, tool {metrics.get('tool_attempts')})",
        f"  input {total_input:,} tokens   cached {tokens.get('cached'):,} ({cache_share:.1f}%)"
        f"   billed output {tokens.get('output'):,}",
        f"  known cost {money(metrics.get('known_cost'))}"
        f"   complete {metrics.get('cost_complete')}"
        f"   wall {metrics.get('wall_seconds', 0):.1f}s",
        f"  operations planned {len(metrics.get('comparisons') or [])}",
        BAR,
    ]
    for comparison in metrics.get("comparisons") or []:
        predicted = comparison.get("predicted") or {}
        lines.append(
            f"  {comparison.get('mode')}/{comparison.get('view')}"
            f"  predicted {money(predicted.get('cost'))}"
            f"  actual {money(comparison.get('actual_inclusive_cost'))}"
            f"  error {money(comparison.get('cost_error'))}"
        )
    return "\n".join(lines)
