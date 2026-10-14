from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_trend_explanation():
    response = client.get("/admin/explain/trend")
    assert response.status_code == 200
    assert response.json()["endpoint"] == "trend"

def test_list_employees():
    response = client.get("/employees")
    assert response.status_code == 200

def test_list_attendance():
    response = client.get("/attendance")
    assert response.status_code == 200

def test_department_summary():
    response = client.get(
        "/analytics/departments/summary",
        params={"month": "2026-10"}
    )
    assert response.status_code == 200

def test_late_leaderboard():
    response = client.get(
        "/analytics/leaderboard/late",
        params={"month": "2026-10"}
    )
    assert response.status_code == 200

def test_department_trend():
    response = client.get(
        "/analytics/departments/IT/trend",
        params={"month": "2026-10"}
    )
    assert response.status_code == 200

def test_employee_monthly_analytics():
    response = client.get(
        "/analytics/employees/EMP1235/monthly",
        params={"month": "2026-10"}
    )
    assert response.status_code in (200, 404)

def test_employee_monthly_invalid_month():
    response = client.get(
        "/analytics/employees/EMP1235/monthly",
        params={"month": "invalid"}
    )
    assert response.status_code == 422

def test_punch_in_unknown_employee():
    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": "EMP9999",
            "punched_at": 1791970000000,
            "status": "PRESENT"
        }
    )
    assert response.status_code == 404

def test_punch_out_unknown_employee():
    response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": "EMP9999",
            "punched_at": 1791973600000
        }
    )
    assert response.status_code == 404

def test_correction_unknown_employee_date():
    response = client.patch(
        "/attendance/EMP9999/2026-10-14",
        json={
            "status": "PRESENT",
            "by": "admin",
            "reason": "Test correction"
        }
    )
    assert response.status_code == 404

def test_employee_monthly_missing_month():
    response = client.get(
        "/analytics/employees/EMP1235/monthly"
    )
    assert response.status_code == 422    



