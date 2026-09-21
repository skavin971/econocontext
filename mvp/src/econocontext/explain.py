"""Render one run as an ordered, readable narrative of what actually happened."""

import json

WIDTH = 96
BAR = "=" * WIDTH
SUB = "-" * WIDTH


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
    workers, shown, step = {}, {}, 0
    for event in events:
        kind = event["kind"]
        if kind == "assembly":
            step += 1
            label = workers.setdefault(
                event["worker_id"], "root" if not workers else f"child-{len(workers)}"
            )
            tokens = event.get("tokens") or 0
            fill = f"{100 * tokens / context_budget:.1f}%" if context_budget else "n/a"
            rows = [
                "assembler.py built the exact request from selected evidence + history",
                f"{tokens:,} tokens assembled   context fill {fill} of {context_budget:,}",
                f"manifest {event.get('manifest')}   took {1000 * (event.get('duration') or 0):.1f}ms",
            ]
            if reconstruct:
                try:
                    body = reconstruct(event["manifest"])
                    messages = (body.get("body") or body).get("messages") or []
                    seen = shown.get(event["worker_id"], 0)
                    added = message_rows(messages, seen)
                    shown[event["worker_id"]] = len(messages)
                    if added:
                        rows.append("")
                        rows.append(
                            "full prompt:" if seen == 0 else "added to this worker's context:"
                        )
                        rows.extend(added)
                except Exception as exc:
                    rows.append(f"(could not reconstruct request: {exc})")
            lines.extend(block(f"STEP {step}  ASSEMBLE INPUT  [{label}]", rows))
        elif kind == "planning":
            step += 1
            candidates = event.get("candidates") or []
            rows = [
                f"trigger: {event.get('trigger')}",
                f"operation: {event.get('operation_id')}",
                f"planner considered {len(candidates)} candidate plan(s):",
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
            lines.extend(block(f"STEP {step}  PLANNER", rows))
        elif kind == "selection":
            plan = event.get("plan") or {}
            estimate = plan.get("estimate") or {}
            step += 1
            lines.extend(
                block(
                    f"STEP {step}  PLAN COMMITTED",
                    [
                        f"{plan.get('mode')}{'/' + plan['view'] if plan.get('view') else ''}"
                        f"   predicted {money(estimate.get('cost'))}"
                        f"   predicted tokens {estimate.get('tokens')}",
                        f"evidence attached: {plan.get('evidence')}",
                    ],
                )
            )
        elif kind == "assembly_rejected":
            step += 1
            lines.extend(
                block(
                    f"STEP {step}  PLAN REJECTED AT ASSEMBLY",
                    [f"plan {event.get('plan_id')}", f"reason: {event.get('reason')}"],
                )
            )
        elif kind == "evidence":
            step += 1
            lines.extend(
                block(
                    f"STEP {step}  STORE EVIDENCE",
                    [
                        f"source {event.get('source')}   {event.get('bytes')} bytes",
                        f"evidence id {event.get('evidence_id')}",
                        f"version {event.get('version')}",
                    ],
                )
            )
        elif kind == "operation_outcome":
            step += 1
            lines.extend(
                block(
                    f"STEP {step}  OPERATION FINISHED",
                    [
                        f"operation {event.get('operation_id')} delivered via {event.get('mode')}",
                        f"stored result {event.get('result_id')}",
                    ],
                )
            )
        elif kind == "integration_complete":
            step += 1
            lines.extend(
                block(
                    f"STEP {step}  ROOT INTEGRATED RESULT",
                    [f"operation {event.get('operation_id')}  plan {event.get('plan_id')}"],
                )
            )
        elif kind == "attempt":
            step += 1
            label = workers.get(event["worker_id"], "root")
            raw = event.get("raw_usage") or {}
            usage = event.get("usage") or {}
            rows = []
            if (
                event["kind"] == "attempt"
                and event.get("name", "").startswith(("gpt", "google"))
                or raw
            ):
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
                for row in describe_call(artifacts(event.get("response"))):
                    rows.append("  ->  " + row[: WIDTH - 12])
                rows += [
                    f"cost {money(event.get('cost'))}"
                    f"   latency {event.get('duration', 0):.3f}s"
                    f"   status {event.get('status')}",
                ]
            else:
                payload = artifacts(event.get("response"))
                rows.append(f"tool {event.get('name')}   status {event.get('status')}")
                rows.append(f"request  {json.dumps(artifacts(event.get('request')))[: WIDTH - 14]}")
                summary = payload
                if isinstance(payload, dict):
                    summary = payload.get("output") or payload.get("text") or payload
                rows.extend(wrap(json.dumps(summary)[:600], indent=2))
            if event.get("error"):
                rows.append(f"ERROR {event['error']}")
            title = "MODEL CALL" if raw else "TOOL CALL"
            lines.extend(block(f"STEP {step}  {title}  [{label}]", rows))
        lines.append("")

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
