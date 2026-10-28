import pytest

from tests.conftest import OPERATOR, VIEWER

QUESTION = {"question": "Why did our AWS bill spike?"}


def create(client, body=None, headers=VIEWER):
    return client.post("/api/runs", json=body or QUESTION, headers=headers)


# ----------------------------------------------------------------------------- success


def test_health_needs_no_key(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["llm_provider"] == "scripted" and body["fixtures_ok"]
    assert body["clock_now"].startswith("2026-11-02T09:00:00")


def test_faults_catalog(client):
    response = client.get("/api/faults", headers=VIEWER)
    assert response.status_code == 200
    assert {f["id"] for f in response.json()} == {
        "stale_billing",
        "malformed_rows",
        "tool_timeout",
        "invalid_tool_response",
    }


def test_create_run_returns_201_with_proposals(client):
    response = create(client)
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "awaiting_approval"
    assert [a["id"] for a in run["proposed_actions"]] == ["act_1", "act_2"]
    assert run["grounding"]["ok"] is True
    assert run["trace"] and run["tool_calls"]


def test_get_and_list_runs(client):
    run_id = create(client).json()["id"]
    assert client.get(f"/api/runs/{run_id}", headers=VIEWER).json()["id"] == run_id
    listed = client.get("/api/runs", headers=VIEWER).json()
    assert [r["id"] for r in listed] == [run_id]
    assert "trace" not in listed[0] and "messages" not in listed[0]


def test_operator_approves_selected_action(client):
    run_id = create(client).json()["id"]
    response = client.post(f"/api/runs/{run_id}/approve", json={"action_ids": ["act_2"]}, headers=OPERATOR)
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert [(e["action_id"], e["status"]) for e in run["executions"]] == [("act_2", "deleted (mock)")]
    assert run["approval"]["actor"].startswith("operator:")


def test_operator_rejects(client):
    run_id = create(client).json()["id"]
    response = client.post(f"/api/runs/{run_id}/reject", json={"reason": "later"}, headers=OPERATOR)
    assert response.status_code == 200
    assert response.json()["approval"]["decision"] == "rejected" and response.json()["executions"] == []


# ----------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 501},
        {"question": "ok", "faults": ["power_outage"]},
        {"question": "ok", "unexpected": 1},
        {},
    ],
)
def test_create_run_validation(client, body):
    response = client.post("/api/runs", json=body, headers=VIEWER)
    assert response.status_code == 422
    assert response.json()["error"]["kind"] == "validation_error"


def test_approve_requires_non_empty_action_ids(client):
    run_id = create(client).json()["id"]
    response = client.post(f"/api/runs/{run_id}/approve", json={"action_ids": []}, headers=OPERATOR)
    assert response.status_code == 422


def test_approve_unknown_action_id(client):
    run_id = create(client).json()["id"]
    response = client.post(f"/api/runs/{run_id}/approve", json={"action_ids": ["act_9"]}, headers=OPERATOR)
    assert response.status_code == 422
    assert response.json()["error"]["kind"] == "unknown_action_id"


# ----------------------------------------------------------------------------- authorization


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_missing_or_invalid_key_is_401(client, headers):
    for method, path in [("get", "/api/faults"), ("get", "/api/runs"), ("post", "/api/runs")]:
        response = getattr(client, method)(path, headers=headers, **({"json": QUESTION} if method == "post" else {}))
        assert response.status_code == 401
        assert response.json()["error"]["kind"] == "unauthorized"


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_viewer_cannot_decide(client, services, decision):
    run_id = create(client).json()["id"]
    body = {"action_ids": ["act_1"]} if decision == "approve" else {}
    response = client.post(f"/api/runs/{run_id}/{decision}", json=body, headers=VIEWER)
    assert response.status_code == 403
    assert response.json()["error"]["kind"] == "forbidden"
    assert services.executor.executed == []


# ----------------------------------------------------------------------------- failure


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/run_missing", headers=VIEWER).status_code == 404
    response = client.post("/api/runs/run_missing/approve", json={"action_ids": ["a"]}, headers=OPERATOR)
    assert response.status_code == 404 and response.json()["error"]["kind"] == "not_found"


def test_approving_a_completed_run_is_409(client):
    run_id = create(client).json()["id"]
    client.post(f"/api/runs/{run_id}/reject", json={}, headers=OPERATOR)
    response = client.post(f"/api/runs/{run_id}/approve", json={"action_ids": ["act_1"]}, headers=OPERATOR)
    assert response.status_code == 409
    assert response.json()["error"]["kind"] == "invalid_transition"


def test_failure_scenario_is_a_successful_request_with_failed_run(client):
    response = create(client, {"question": "Why did our AWS bill spike?", "faults": ["stale_billing"]})
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed" and run["error"]["kind"] == "stale_data"
    assert run["proposed_actions"] == []


def test_unexpected_errors_use_the_error_envelope(services, monkeypatch):
    from fastapi.testclient import TestClient

    from leakhunter.api.app import create_app

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(services.service, "run_question", boom)
    with TestClient(create_app(services=services), raise_server_exceptions=False) as client:
        response = client.post("/api/runs", json=QUESTION, headers=VIEWER)
    assert response.status_code == 500
    assert response.json()["error"]["kind"] == "internal_error"


def test_refused_run_cannot_be_approved(client):
    run = create(client, {"question": "Delete all production resources"}).json()
    assert run["status"] == "refused" and run["refusal"]["rule"] == "destructive_bulk_request"
    response = client.post(f"/api/runs/{run['id']}/approve", json={"action_ids": ["act_1"]}, headers=OPERATOR)
    assert response.status_code == 409
