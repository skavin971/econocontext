from typing import Protocol


class DomainAdapter(Protocol):
    name: str

    async def prepare(self): ...
    def prompt(self, worker, method): ...
    def tools(self, worker, method): ...
    async def execute(self, name, arguments, worker): ...
    async def refresh(self): ...
    async def verify(self, result): ...
    async def export(self): ...


def tool(name, description, properties, required=()):
    return dict(
        type="function",
        function=dict(
            name=name,
            description=description,
            parameters=dict(
                type="object",
                properties=properties,
                required=list(required),
                additionalProperties=False,
            ),
        ),
    )


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
# Real models otherwise invent citation formats such as "file.txt:<id>"; the
# store only resolves the opaque id exactly as a tool returned it.
EVIDENCE = {
    "type": "array",
    "items": STRING,
    "description": (
        "Evidence ids copied verbatim from the 'evidence' field of earlier tool "
        "results. Use the opaque id alone; never add a filename, prefix or suffix."
    ),
}


def controls(worker, method, delegation=False):
    result = (
        [
            tool(
                "complete_task",
                "Finish the task with answer and evidence references.",
                {"answer": STRING, "evidence": EVIDENCE},
                ["answer", "evidence"],
            )
        ]
        if worker.role == "root"
        else []
    )
    if method == "econocontext":
        # A child always needs to finish its assignment. A root needs
        # complete_operation only when it can raise one itself, which is the
        # delegation-request path; otherwise offering it leaks the mechanism.
        if worker.role == "child" or delegation:
            result.append(
                tool(
                    "complete_operation",
                    "Finish the active operation, not the entire task.",
                    {"answer": STRING, "evidence": EVIDENCE},
                    ["answer", "evidence"],
                )
            )
        if worker.role == "root" and delegation:
            result.append(
                tool(
                    "request_operation",
                    "Delegate one bounded, side-effect-free investigation to a "
                    "separate worker that reads on your behalf and reports back a "
                    "finding. You receive only its result, so the reading it does "
                    "never enters your own context. Delegation costs one extra "
                    "exchange, so it pays off for a sub-problem that needs real "
                    "investigation and not for something you can settle in a turn. "
                    "Request operations one after another as needed, but never "
                    "while one is already active. A delegated worker cannot edit "
                    "files or delegate further.",
                    {
                        "goal": {
                            "type": "string",
                            "description": (
                                "What the worker must determine, stated so its answer "
                                "stands on its own without your context."
                            ),
                        },
                        "scope": {
                            "type": "string",
                            "description": (
                                "Stable name for the area under investigation, such as "
                                "a file path or module. This is the reuse key: an "
                                "identical scope continues the worker that already "
                                "studied that area, keeping what it learned, while a "
                                "different scope starts a fresh worker with an empty "
                                "context."
                            ),
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["analysis", "diagnosis", "research"],
                            "description": (
                                "diagnosis to find the cause of a defect, analysis to "
                                "reason over known material, research to locate "
                                "information."
                            ),
                        },
                        "required": EVIDENCE,
                    },
                    ["goal", "scope", "kind"],
                )
            )
    return result
