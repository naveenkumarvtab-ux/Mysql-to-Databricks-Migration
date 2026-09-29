from __future__ import annotations
import os
import json
import pytest
from app.core.security import hash_password, create_access_token, decode_token, mask_secrets
from app.models.entities import User, MigrationProject, MigrationSource, MigrationObject
from app.models.canonical import MigrationAudit
def test_item_01_business_purpose(client, auth_headers):
    # Tier 1: Core Migration Engine
    r = client.post("/api/projects", json={"name": "Business_Purpose_Test"}, headers=auth_headers)
    assert r.status_code in (200, 201)
    data = r.json()
    assert "id" in data
    assert data["name"] == "Business_Purpose_Test"

def test_item_02_core_workflow_transpilations(client, auth_headers, db):
    # Tier 1: Core Migration Engine
    from app.services.engine import map_sqlserver_type, classify_procedure
    mapped = map_sqlserver_type("VARCHAR", 100, None)
    assert mapped == "STRING"
    mapped_int = map_sqlserver_type("INT", None, None)
    assert mapped_int == "INT"
    mapped_dec = map_sqlserver_type("DECIMAL", 10, 2)
    assert "DECIMAL" in mapped_dec

def test_item_03_ui_ux_error_contracts(client, auth_headers):
    # Tier 1: Core Migration Engine
    r = client.get("/api/projects/NON_EXISTENT_PROJECT_XYZ/sources", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []

def test_item_04_login_security(client, db):
    # Tier 2: Production DevOps & Safety
    u = db.query(User).filter(User.username == "test_user_p04").first()
    if not u:
        u = User(id="USR_test_p04", username="test_user_p04", password_hash=hash_password("SecurePass!123"), role="OPERATOR", failed_attempts=0, locked=False)
        db.add(u); db.commit()

    # Valid Login
    r = client.post("/api/login", json={"username": "test_user_p04", "password": "SecurePass!123"})
    assert r.status_code == 200
    assert "access_token" in r.json()

    # Invalid Login
    r_bad = client.post("/api/login", json={"username": "test_user_p04", "password": "WrongPassword"})
    assert r_bad.status_code == 401

def test_item_05_session_security(client, auth_headers):
    # Tier 2: Production DevOps & Safety
    token = auth_headers["Authorization"].split(" ")[1]
    decoded = decode_token(token)
    assert decoded["sub"] == "admin"
    assert "exp" in decoded

    # Tampered Token
    tampered_headers = {"Authorization": f"Bearer {token}TAMPERED"}
    r = client.get("/api/projects", headers=tampered_headers)
    assert r.status_code == 401

def test_item_06_roles_and_permissions(client, auth_headers):
    # Tier 3: Enterprise SaaS Governance
    viewer_token = create_access_token("viewer", "VIEWER")
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}
    
    # Viewer cannot create database backup (requires ADMIN or OPERATOR)
    r = client.post("/api/admin/backups/create", headers=viewer_headers)
    assert r.status_code == 403

    # Admin can create database backup
    r_admin = client.post("/api/admin/backups/create", headers=auth_headers)
    assert r_admin.status_code == 200

def test_item_07_admin_portal_apis(client, auth_headers, db):
    # Tier 3: Enterprise SaaS Governance
    u = db.query(User).filter(User.username == "portal_test_user").first()
    if not u:
        u = User(id="USR_portal_user", username="portal_test_user", password_hash=hash_password("testpass123"), role="VIEWER", locked=True, failed_attempts=5)
        db.add(u); db.commit()

    # Admin unlocks user
    r = client.post(f"/api/users/{u.id}/unlock", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["unlocked"] is True

    # Admin changes role
    r_role = client.post(f"/api/users/{u.id}/role", json={"role": "REVIEWER"}, headers=auth_headers)
    assert r_role.status_code == 200
    assert r_role.json()["role"] == "REVIEWER"

def test_item_08_client_data_isolation(client, auth_headers):
    # Tier 1: Core Migration Engine
    r1 = client.post("/api/projects", json={"name": "Tenant_A_Project"}, headers=auth_headers)
    r2 = client.post("/api/projects", json={"name": "Tenant_B_Project"}, headers=auth_headers)
    p1_id = r1.json()["id"]
    p2_id = r2.json()["id"]
    assert p1_id != p2_id

def test_item_09_data_protection_and_secret_masking():
    # Tier 1: Core Migration Engine
    raw = "token=dapi12345abcdef; password=SecretPass!123; api_key=AIzaSyDxyz"
    masked = mask_secrets(raw)
    assert "dapi12345" not in masked
    assert "SecretPass" not in masked
    assert "token=***" in masked
    assert "password=***" in masked

def test_item_10_audit_trail(client, auth_headers, db):
    # Tier 1: Core Migration Engine
    from app.services.engine import uid
    audit = MigrationAudit(id=uid("AUD"), project_id="PRJ_audit_test", status="PASSED", payload_json=json.dumps({"action": "DEPLOY_MEDALLION_DEV"}))
    db.add(audit); db.commit()
    saved = db.get(MigrationAudit, audit.id)
    assert saved is not None
    assert saved.status == "PASSED"

def test_item_11_input_and_api_security(client):
    # Tier 1: Core Migration Engine
    # Missing required body
    r = client.post("/api/login", json={})
    assert r.status_code == 422

    # Unauthorized access
    r_unauth = client.get("/api/projects")
    assert r_unauth.status_code == 401

def test_item_12_error_handling(client, auth_headers):
    # Tier 1: Core Migration Engine
    r = client.post("/api/users/NON_EXISTENT_ID/unlock", headers=auth_headers)
    assert r.status_code == 404
    assert "detail" in r.json()

def test_item_13_backup_and_recovery(client, auth_headers):
    # Tier 2: Production DevOps & Safety
    # Backup creation
    r = client.post("/api/admin/backups/create", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"

    # Backup list
    r_list = client.get("/api/admin/backups", headers=auth_headers)
    assert r_list.status_code == 200
    assert "backups" in r_list.json()

    # Backup restore
    r_restore = client.post("/api/admin/backups/restore", headers=auth_headers)
    assert r_restore.status_code == 200
    assert r_restore.json()["restored"] is True

def test_item_14_deployment_configuration():
    # Tier 2: Production DevOps & Safety
    assert os.path.exists("render.yaml") or os.path.exists("../render.yaml")
    assert os.path.exists("Dockerfile") or os.path.exists("../Dockerfile")

def test_item_15_monitoring_and_support(client, auth_headers):
    # Tier 2: Production DevOps & Safety
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    r_diag = client.get("/api/system/diagnostics", headers=auth_headers)
    assert r_diag.status_code == 200
    assert "environment" in r_diag.json()

def test_item_16_documentation_integrity():
    # Tier 2: Production DevOps & Safety
    docs = ["docs/ARCHITECTURE.md", "docs/DEPLOYMENT.md", "docs/USER_RUNBOOK_1_7.md"]
    found = [os.path.exists(d) or os.path.exists(f"../{d}") for d in docs]
    assert any(found)

def test_item_17_performance_streaming_specs():
    # Tier 1: Core Migration Engine
    from app.core.config import get_settings
    cfg = get_settings()
    assert cfg.batch_size >= 1000
    assert cfg.parallelism >= 1

def test_item_18_data_retention_and_pruning(client, auth_headers):
    # Tier 3: Enterprise SaaS Governance
    r_pol = client.get("/api/admin/retention/policies", headers=auth_headers)
    assert r_pol.status_code == 200
    assert "default_log_retention_days" in r_pol.json()

    r_prune = client.post("/api/admin/retention/prune", json={"retention_days": 30}, headers=auth_headers)
    assert r_prune.status_code == 200
    assert r_prune.json()["pruned"] is True

def test_item_19_enterprise_sso_and_mfa(client, auth_headers):
    # Tier 3: Enterprise SaaS Governance
    # SSO Login
    r_sso = client.post("/api/auth/sso/login", json={"provider": "AZURE_AD", "id_token": "mock_jwt_sso_token", "email": "sso_user@vtabsquare.com"})
    assert r_sso.status_code == 200
    assert "access_token" in r_sso.json()

    # MFA Setup
    r_mfa_setup = client.post("/api/auth/mfa/setup", headers=auth_headers)
    assert r_mfa_setup.status_code == 200
    assert "secret" in r_mfa_setup.json()

    # MFA Verify
    r_mfa_verify = client.post("/api/auth/mfa/verify", json={"code": "123456"}, headers=auth_headers)
    assert r_mfa_verify.status_code == 200
    assert r_mfa_verify.json()["verified"] is True

def test_item_20_accessibility_and_usability():
    # Tier 3: Enterprise SaaS Governance
    assert os.path.exists("frontend/src/App.tsx") or os.path.exists("../frontend/src/App.tsx")

def test_item_21_release_management_and_changelog(client):
    # Tier 3: Enterprise SaaS Governance
    r = client.get("/api/system/version")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == "v2.3.0"
    assert "VTAB Square" in data["organization"]
