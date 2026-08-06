from skills.knowledge_extract.executor import run


def test_extracts_patterns():
    notes = "Problem: ORM 循环查询导致性能下降\nSolution: Batch Fetch\nRule: 检测到循环查询时优先使用批量加载"
    result = run({"notes": notes, "tags": ["performance", "n-plus-one"]})
    assert result["data"]["problem_pattern"] == "ORM 循环查询导致性能下降"
    assert result["data"]["solution_pattern"] == "Batch Fetch"
    assert "批量加载" in result["data"]["reusable_rule"]
