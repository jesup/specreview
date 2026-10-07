import json
import subprocess


DEFAULT_TOOLS = ["Read", "Grep", "Glob"]


class ClaudeError(Exception):
    pass


def run_claude(
    prompt: str,
    schema: dict,
    *,
    cwd: str | None = None,
    model: str | None = None,
    timeout: int = 1800,
    tools: list[str] | None = None,
) -> dict:
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--permission-mode", "dontAsk",
        "--settings", json.dumps({"sandbox": {"autoAllowBashIfSandboxed": False}}),
        "--allowedTools", ",".join(tools or DEFAULT_TOOLS),
    ]
    if model:
        cmd += ["--model", model]
    p = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout
    )
    if p.returncode != 0:
        raise ClaudeError(p.stderr.strip() or p.stdout[:500])
    data = json.loads(p.stdout)
    if data.get("is_error") or "structured_output" not in data:
        raise ClaudeError(str(data.get("result", data))[:500])
    return data["structured_output"]
