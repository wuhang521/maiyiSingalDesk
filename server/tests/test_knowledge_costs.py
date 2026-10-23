"""Prepared, not run: exact decimal accounting and explicit bill reconciliation."""
import json
from app.config import get_settings
from app.knowledge_costs import micros
from app.knowledge_usage import lock_paid_owner, reserve
from app.knowledge_models import KnowledgeCloudUsage
from app.models import UserAccount
from sqlalchemy import select
import pytest
from fastapi import HTTPException
from test_knowledge import kb

@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0.0000001", "1000001", 1.2, None])
def test_invalid_currency_never_rounds_or_guesses(value):
    with pytest.raises(ValueError): micros(value)

def test_reservation_budget_reconciliation_and_private_ledger(kb, monkeypatch):
    client, users, factory, _ = kb; s = get_settings()
    monkeypatch.setattr(s, "knowledge_money_budget_enabled", True)
    monkeypatch.setattr(s, "knowledge_budget_daily_cny", "0.5")
    monkeypatch.setattr(s, "knowledge_budget_monthly_cny", "1")
    monkeypatch.setattr(s, "knowledge_request_ceiling_cny", json.dumps({"semantic_query": "0.5"}))
    with factory() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "kb-test-0")); user = lock_paid_owner(db, user)
        reserve(db, user.id, "a" * 32, "semantic_query"); db.commit()
        user = lock_paid_owner(db, user)
        with pytest.raises(HTTPException) as error: reserve(db, user.id, "b" * 32, "semantic_query")
        assert error.value.status_code == 429; db.rollback()
    own = client.get("/api/v1/knowledge/costs/ledger", headers=users[0]); assert own.status_code == 200
    assert own.json()["items"][0]["reserved_cny"] == "0.500000"
    assert client.get("/api/v1/knowledge/costs/ledger", headers=users[1]).json()["items"] == []
    bill = {"rows": [{"usage_id": "a" * 32, "amount_cny": "0.1", "invoice_reference": "synthetic:bill:line1"}], "verified_provider_bill": True}
    assert client.post("/api/v1/knowledge/admin/costs/reconcile", headers=users[0], json=bill).status_code == 403
    assert client.post("/api/v1/knowledge/admin/costs/reconcile", headers=users[2], json={**bill, "verified_provider_bill": False}).status_code == 422
    result = client.post("/api/v1/knowledge/admin/costs/reconcile", headers=users[2], json=bill); assert result.status_code == 200
    assert client.post("/api/v1/knowledge/admin/costs/reconcile", headers=users[2], json=bill).json()["reconciled"] == 0
    assert client.get("/api/v1/knowledge/costs", headers=users[0]).json()["daily_exposure_cny"] == "0.100000"
    bill["rows"][0]["amount_cny"] = "0.2"
    assert client.post("/api/v1/knowledge/admin/costs/reconcile", headers=users[2], json=bill).status_code == 409

def test_legacy_unpriced_submission_blocks_enabled_money_budget(kb, monkeypatch):
    _, _, factory, _ = kb; s = get_settings()
    monkeypatch.setattr(s, "knowledge_money_budget_enabled", True)
    monkeypatch.setattr(s, "knowledge_budget_daily_cny", "1")
    monkeypatch.setattr(s, "knowledge_budget_monthly_cny", "1")
    monkeypatch.setattr(s, "knowledge_request_ceiling_cny", '{"semantic_query":"0.1"}')
    with factory() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "kb-test-0"))
        db.add(KnowledgeCloudUsage(id="d" * 32, owner_id=user.id, operation="semantic_query")); db.commit()
        user = lock_paid_owner(db, user)
        with pytest.raises(HTTPException) as error: reserve(db, user.id, "f" * 32, "semantic_query")
        assert error.value.status_code == 409
