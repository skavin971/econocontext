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


def controls(worker, method):
    result = (
        [
            tool(
                "complete_task",
                "Finish the task with answer and evidence references.",
                {"answer": STRING, "evidence": STRINGS},
                ["answer", "evidence"],
            )
        ]
        if worker.role == "root"
        else []
    )
    if method == "econocontext":
        result.append(
            tool(
                "complete_operation",
                "Finish the active operation, not the entire task.",
                {"answer": STRING, "evidence": STRINGS},
                ["answer", "evidence"],
            )
        )
        if worker.role == "root":
            result.append(
                tool(
                    "request_operation",
                    "Request bounded side-effect-free analysis; no nested requests.",
                    {
                        "goal": STRING,
                        "scope": STRING,
                        "kind": {"type": "string", "enum": ["analysis", "diagnosis", "research"]},
                        "required": STRINGS,
                    },
                    ["goal", "scope", "kind"],
                )
            )
    return result
