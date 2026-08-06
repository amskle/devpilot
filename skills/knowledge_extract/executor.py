import json


def run(inputs: dict) -> dict:
    notes = inputs.get("notes", "")
    lines = [line.strip() for line in notes.splitlines() if line.strip()]
    issue = inputs.get("issue", "")
    solution = inputs.get("solution", "")

    problem_pattern = next(
        (line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("problem") and ":" in line),
        issue,
    )
    solution_pattern = next(
        (line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("solution") and ":" in line),
        solution,
    )
    reusable_rule = next(
        (line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("rule") and ":" in line),
        f"当出现 {problem_pattern or '同类问题'} 时，优先采用 {solution_pattern or '已验证方案'}",
    )
    confidence = float(inputs.get("confidence", 0.8))
    tags = [t.strip() for t in inputs.get("tags", []) if t.strip()]
    return {
        "status": "ok",
        "data": {
            "problem_pattern": problem_pattern,
            "solution_pattern": solution_pattern,
            "reusable_rule": reusable_rule,
            "confidence": confidence,
            "tags": tags,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
