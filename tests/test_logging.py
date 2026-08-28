from app.dblog import log_event


def test_audit_logs_queryable_after_admin_actions(client, admin_headers):
    audit = client.get("/admin/audit-logs", headers=admin_headers)
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()}
    assert "admin.login" not in actions
    assert any(a.startswith(("connector.", "api_key.", "task.")) for a in actions) or audit.json() == []


def test_app_logs_written_and_queryable(client, admin_headers):
    log_event("error", "test.logger", "一条测试日志", {"k": "v"})
    logs = client.get("/admin/app-logs", headers=admin_headers)
    assert logs.status_code == 200
    entries = logs.json()
    assert any(e["level"] == "error" and e["logger"] == "test.logger" and "测试日志" in e["message"] for e in entries)


def test_app_logs_filter_by_level(client, admin_headers):
    log_event("warning", "f.logger", "warn-one")
    log_event("error", "f.logger", "err-one")
    only_error = client.get("/admin/app-logs?level=error", headers=admin_headers)
    assert only_error.status_code == 200
    levels = {e["level"] for e in only_error.json()}
    assert levels == {"error"}
    msgs = "".join(e["message"] for e in only_error.json())
    assert "err-one" in msgs
    assert "warn-one" not in msgs


def test_app_logs_filter_by_logger(client, admin_headers):
    log_event("info", "scope.a", "from-a")
    log_event("info", "scope.b", "from-b")
    only_a = client.get("/admin/app-logs?logger=scope.a", headers=admin_headers)
    assert only_a.status_code == 200
    loggers = {e["logger"] for e in only_a.json()}
    assert loggers == {"scope.a"}


def test_audit_logs_require_admin(client):
    assert client.get("/admin/audit-logs").status_code == 401
    assert client.get("/admin/app-logs").status_code == 401
