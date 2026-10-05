import json
from da_agent.app import normalize_review


def response():
    return {"state": "completed", "text": json.dumps({"criteria": [{"key": key, "level": 3, "reason": "근거", "claim_ids": ["c1"], "saved_execution_ids": ["e1"]} for key in ("problem_definition", "analysis_approach", "sql_accuracy", "interpretation", "next_actions")], "strengths": [], "improvements": [], "next_steps": ["다음 분석"]}), "model": "test-only"}


def test_review_score_and_evidence_validation():
    report, evidence = {"claims": [{"claim_id": "c1"}]}, [{"saved_execution_id": "e1"}]
    good = normalize_review(response(), report, evidence)
    assert good["status"] == "completed" and good["feedback"]["total_score"] == 75.0
    bad = normalize_review(response(), report, [])
    assert bad["status"] == "failed" and bad["feedback"] is None


def test_unstructured_or_partial_response_not_review_success():
    assert normalize_review({"state": "completed", "text": "내용"}, {"claims": []}, [])["status"] == "failed"
    assert normalize_review({"state": "error", "reason": "response_interrupted"}, {"claims": []}, [])["status"] == "failed"
