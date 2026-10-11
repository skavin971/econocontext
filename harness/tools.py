"""The tools, run inside the task's container: bash, read_file and write_file (submit ends the run, in
agent.py). The shell keeps its working directory between calls; every output is capped the same way
for every policy."""

import base64
import shlex

STATE = "/tmp/.econo-agent"


def cap(text: str, limit: int) -> str:
    """Head and tail of a long output (the same cap for every policy)."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters omitted] ...\n{text[-half:]}"


async def setup(environment, state: str = STATE) -> None:
    await environment.exec(f"mkdir -p {state} && pwd > {state}/cwd", timeout_sec=30)


async def shell(environment, state: str, timeout: int, script: str) -> str:
    """Run in the saved working directory; save it again afterwards (cd persists)."""
    wrapped = (f"cd \"$(cat {state}/cwd 2>/dev/null || pwd)\" 2>/dev/null\n{script}\n"
               f"__rc=$?; pwd > {state}/cwd; exit $__rc")
    command = f"timeout -k 5 {timeout} bash -c {shlex.quote(wrapped)}"
    try:
        result = await environment.exec(command, timeout_sec=timeout + 30)
    except RuntimeError as exc:  # Harbor raises on its own timeout
        return f"(command failed: {str(exc)[:200]})"
    out = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
    code = result.return_code
    return out.rstrip("\n") + f"\n(exit code {code}{', timed out' if code == 124 else ''})"


async def run_tool(environment, state: str, timeout: int, name: str, args: dict) -> str:
    """Run one tool; the full output (a policy decides what is shown)."""
    if name == "bash":
        return await shell(environment, state, timeout, args.get("command", ""))
    if name == "read_file":
        start = max(1, int(args.get("start_line") or 1))
        end = int(args.get("end_line") or start + 399)
        path = shlex.quote(args.get("path", ""))
        return await shell(environment, state, timeout, (
            f"total=$(wc -l < {path} | tr -d ' ') || exit 1; last=$(( {end} < total ? {end} : total )); "
            f"awk -v s={start} -v e={end} 'NR>=s && NR<=e {{printf \"%d\\t%s\\n\", NR, $0}}' {path}; "
            f"echo \"[lines {start}-$last of $total]\""))
    if name == "write_file":
        path = args.get("path", "")
        data = base64.b64encode(args.get("content", "").encode()).decode()
        return await shell(environment, state, timeout, (
            f"mkdir -p \"$(dirname {shlex.quote(path)})\" && printf %s {data} | base64 -d > {shlex.quote(path)} "
            f"&& echo \"wrote $(wc -c < {shlex.quote(path)} | tr -d ' ') bytes to {path}\""))
    return f"(unknown tool {name})"
