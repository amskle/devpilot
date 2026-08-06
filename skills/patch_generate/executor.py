import difflib
import json
from pathlib import Path


def _replace_once(content: str, old: str, new: str, occurrence: int = 1) -> str:
    if not old:
        return content
    parts = content.split(old)
    if len(parts) <= occurrence:
        raise ValueError(f"replacement target not found (occurrence {occurrence}): {old[:80]}")
    return old.join(parts[:occurrence]) + new + old.join(parts[occurrence + 1 :])


def run(inputs: dict) -> dict:
    target = Path(inputs.get("target_file", ""))
    replacements = inputs.get("replacements", [])
    apply_to_disk = bool(inputs.get("apply", False))
    if not target.exists():
        return {"status": "error", "error": f"target not found: {target}"}

    original = target.read_text(encoding="utf-8", errors="ignore")
    content = original
    for item in replacements:
        content = _replace_once(content, item["old"], item["new"], int(item.get("occurrence", 1)))

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
        )
    )
    if apply_to_disk:
        target.write_text(content, encoding="utf-8")

    return {
        "status": "ok",
        "data": {"diff": diff, "new_content": content, "applied": apply_to_disk},
    }


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
