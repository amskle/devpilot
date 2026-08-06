from skills.risk_assessment.executor import run


def test_high_risk_detected():
    diff = "+++ b/db.sql\n+DELETE FROM users;\n+password = 'x'\n" + "+" * 260
    result = run({"diff": diff})
    assert result["data"]["level"] == "High"
    assert any("高风险关键字" in r for r in result["data"]["reasons"])


def test_small_change_is_low_risk():
    diff = "+++ b/app.py\n+value = 2\n- value = 1\n"
    result = run({"diff": diff})
    assert result["data"]["level"] == "Low"
