import json
import re


HIGH_KEYWORDS = ("delete", "drop", "truncate", "password", "token", "exec", "eval", "chmod", "sudo")
MEDIUM_KEYWORDS = ("transaction", "migration", "schema", "api", "auth", "cascade")


def run(inputs: dict) -> dict:
    diff = inputs.get("diff", "")
    reasons = []
    score = 0

    added = len(re.findall(r"^\+[^+]", diff, flags=re.M))
    removed = len(re.findall(r"^-[^-]", diff, flags=re.M))
    score += min(added + removed, 40)
    if added + removed > 200:
        reasons.append("改动行数较大")
    elif added + removed == 0:
        reasons.append("未检测到代码改动")

    changed_files = re.findall(r"^\+\+\+\s+(?:b/)?(.+)$", diff, flags=re.M)
    if changed_files:
        reasons.append(f"涉及文件：{', '.join(sorted(set(changed_files))[:5])}")
    if any(name in diff.lower() for name in ("/test", "test_", "tests/", "pom.xml", "package.json", "pyproject.toml")):
        score -= 10
        reasons.append("包含构建或测试相关改动")

    high_hits = [k for k in HIGH_KEYWORDS if k in diff.lower()]
    medium_hits = [k for k in MEDIUM_KEYWORDS if k in diff.lower()]
    if high_hits:
        score += 35
        reasons.append(f"包含高风险关键字：{', '.join(high_hits)}")
    if medium_hits:
        score += 15
        reasons.append(f"包含中风险关键字：{', '.join(medium_hits)}")

    level = "Low"
    if score >= 65:
        level = "High"
    elif score >= 35:
        level = "Medium"
    if level != "Low":
        reasons.append(f"风险评分 {score}/100，需人工确认或审批")

    return {"status": "ok", "data": {"level": level, "score": min(score, 100), "reasons": reasons}}


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
