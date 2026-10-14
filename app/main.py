"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict, field_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]

db.employees.create_index("emp_code", unique=True)
db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True
)

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #



def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))

    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=IST)
    else:
        punch_in = punch_in.astimezone(IST)

    shift_start_dt = punch_in.replace(
        hour=h,
        minute=m,
        second=0,
        microsecond=0
    )

    minutes = int((punch_in - shift_start_dt).total_seconds() // 60)

    # First 10 minutes are the grace period
    return max(0, minutes - 10)



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = (punch_out - punch_in).total_seconds()
    hours = Decimal(str(seconds)) / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))





def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str
) -> int:
    h, m = map(int, shift_end.split(":"))

    shift_date = datetime.fromisoformat(date_str).date()

    end = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        h,
        m,
        tzinfo=IST
    )

    punch_out = punch_out.replace(tzinfo=IST) if punch_out.tzinfo is None else punch_out.astimezone(IST)

    if end <= punch_out:
        end += timedelta(days=1)

    overtime = max(
        0,
        int((punch_out - end).total_seconds() // 60)
    )

    return overtime if overtime >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        import re
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Time must be in HH:MM format")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        datetime.strptime(value, "%Y-%m-%d")
        return value

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000
    )
    status: str = "PRESENT"

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value not in {"PRESENT", "WFH", "ON_DUTY"}:
            raise ValueError("Invalid punch-in status")
        return value


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=1000000000000,
        le=4102444800000
    )
# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(IST)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Employee code already exists"
        )

    doc.pop("_id", None)
    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
):
    if page < 1:
        raise HTTPException(400, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            400, "page_size must be between 1 and 100"
        )

    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    
    ts = (
       
        datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        ).astimezone(IST)
        if body.punched_at is not None
        else datetime.now(IST)
    )
    # print("DEBUG punch-in time:", ts, "Shift start:", emp["shift_start"])
    d = ts.date().isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
       
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
     res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
     raise HTTPException(
        status_code=409,
        detail="already punched in for this date"
    )

    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    employee = db.employees.find_one({"emp_code": body.emp_code})

    if not employee:
        raise HTTPException(404, "unknown employee")

    if body.punched_at is not None:
        punched_at = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=IST
        )
    else:
        punched_at = datetime.now(IST)
    


    date_str = punched_at.date().isoformat()

    record = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": {"$in": [
            date_str,
            (punched_at.date() - timedelta(days=1)).isoformat()
        ]}
    }, sort=[("punch_in", -1)])

    if not record:
        raise HTTPException(404, "no punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    punch_in = record["punch_in"]
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=IST)

    if punched_at <= punch_in:
        raise HTTPException(422, "punch-out must be after punch-in")

    if (punched_at - punch_in).total_seconds() > 24 * 3600:
        raise HTTPException(422, "punch-out cannot be more than 24 hours after punch-in")

    work_hours = compute_work_hours(punch_in, punched_at)

    shift_end = employee.get("shift_end", "18:00")
    overtime = compute_overtime(
        punched_at, shift_end, record["date"]
    )

    half_day = work_hours < 4

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": punched_at,
            "work_hours": work_hours,
            "overtime_minutes": overtime,
            "half_day": half_day
        }}
    )

    if result.modified_count != 1:
        raise HTTPException(409, "attendance record was changed; retry")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))
    return updated

class AttendanceCorrection(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    half_day: Optional[bool] = None

@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(
    emp_code: str,
    date: str,
    body: AttendanceCorrection
):
    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })

    if not record:
        raise HTTPException(404, "Attendance record not found")

    changes = body.model_dump(exclude_unset=True, exclude_none=True)

    if not changes:
        raise HTTPException(400, "No correction fields provided")

    if "punch_in" in changes:
        changes["punch_in"] = datetime.fromtimestamp(
            changes["punch_in"] / 1000, tz=IST
        )

    if "punch_out" in changes:
        changes["punch_out"] = datetime.fromtimestamp(
            changes["punch_out"] / 1000, tz=IST
        )

    new_in = changes.get("punch_in", record.get("punch_in"))
    new_out = changes.get("punch_out", record.get("punch_out"))

    if new_in and new_out:
        if new_out < new_in:
            raise HTTPException(400, "Punch-out cannot be before punch-in")

        changes["work_hours"] = compute_work_hours(new_in, new_out)
        changes["half_day"] = changes.get(
            "half_day", changes["work_hours"] < 4
        )

        employee = db.employees.find_one({"emp_code": emp_code})
        if employee:
            changes["late_minutes"] = compute_late_minutes(
            new_in,
            employee.get("shift_start", "09:30")
    )

            changes["overtime_minutes"] = compute_overtime(
            new_out,
            employee.get("shift_end", "18:00"),
            date
    )

    changes["history"] = record.get("history", []) + [{
        "previous": {
            key: record.get(key)
            for key in changes
            if key != "history"
        },
        "corrected_at": datetime.now(IST).isoformat()
    }]

    db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {"$set": changes}
    )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))
    return updated

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    try:
        start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    year, month_num = start.year, start.month
    next_month = (
        datetime(year + 1, 1, 1)
        if month_num == 12
        else datetime(year, month_num + 1, 1)
    )

    logs = list(db.attendance_logs.find({
        "emp_code": emp_code,
        "date": {
            "$gte": start.strftime("%Y-%m-01"),
            "$lt": next_month.strftime("%Y-%m-%d")
        }
    }))

    working_days = sum(
        1 for day in range(1, (next_month - start).days + 1)
        if datetime(year, month_num, day).weekday() < 5
    )

    present_statuses = {"PRESENT", "WFH", "ON_DUTY"}
    present_days = sum(
        0.5 if log.get("half_day") else 1
        for log in logs
        if log.get("status") in present_statuses
        and datetime.strptime(log["date"], "%Y-%m-%d").weekday() < 5
    )

    leave_days = sum(1 for log in logs if log.get("status") == "LEAVE")
    late_logs = [log for log in logs if log.get("late_minutes", 0) > 0]
    total_late = sum(log.get("late_minutes", 0) for log in logs)
    total_overtime = sum(log.get("overtime_minutes", 0) for log in logs)

    attendance_pct = (
        round(present_days / working_days * 100, 2)
        if working_days > 0 else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": len(late_logs),
        "total_late_minutes": total_late,
        "total_overtime_minutes": total_overtime,
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary():
    employees = list(db.employees.find({}, {"_id": 0}))
    departments = {}

    for emp in employees:
        dept = emp.get("department", "Unknown")
        departments.setdefault(dept, {
            "department": dept,
            "employee_count": 0,
            "present_today": 0,
            "late_today": 0
        })
        departments[dept]["employee_count"] += 1

    today = datetime.now(IST).date().isoformat()
    logs = list(db.attendance_logs.find({"date": today}))

    for log in logs:
        emp = db.employees.find_one({"emp_code": log["emp_code"]})
        if not emp:
            continue

        dept = emp.get("department", "Unknown")
        departments.setdefault(dept, {
            "department": dept,
            "employee_count": 0,
            "present_today": 0,
            "late_today": 0
        })

        if log.get("status") in {"PRESENT", "WFH", "ON_DUTY"}:
            departments[dept]["present_today"] += 1
        if log.get("late_minutes", 0) > 0:
            departments[dept]["late_today"] += 1

    return {"departments": list(departments.values())}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str):
    try:
        start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    next_month = (
        datetime(start.year + 1, 1, 1)
        if start.month == 12
        else datetime(start.year, start.month + 1, 1)
    )

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start.strftime("%Y-%m-01"),
                    "$lt": next_month.strftime("%Y-%m-%d")
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },
        {"$sort": {"total_late_minutes": -1}},
        {"$limit": 10}
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "leaderboard": [
            {
                "emp_code": item["_id"],
                "late_count": item["late_count"],
                "total_late_minutes": item["total_late_minutes"]
            }
            for item in results
        ]
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str):
    try:
        start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    next_month = (
        datetime(start.year + 1, 1, 1)
        if start.month == 12
        else datetime(start.year, start.month + 1, 1)
    )

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start.strftime("%Y-%m-01"),
                    "$lt": next_month.strftime("%Y-%m-%d")
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {
            "$group": {
                "_id": "$employee.department",
                "total_records": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["LEAVE", "ABSENT"]]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "departments": [
            {
                "department": item["_id"],
                "total_records": item["total_records"],
                "present_days": item["present_days"],
                "leave_days": item["leave_days"]
            }
            for item in results
        ]
    }

@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, month: str):
    try:
        year, month_num = map(int, month.split("-"))
        if not 1 <= month_num <= 12 or year < 2000 or year > 2100:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    records = list(
        db.attendance_logs.aggregate([
            {
                "$match": {
                    "date": {
                        "$regex": f"^{year}-{month_num:02d}-"
                    }
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee"
                }
            },
            {"$unwind": "$employee"},
            {
                "$match": {
                    "employee.department": {
                        "$regex": f"^{department}$",
                        "$options": "i"
                    }
                }
            },
            {
                "$group": {
                    "_id": "$date",
                    "total_records": {"$sum": 1},
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$cond": [
                                    {"$ne": ["$punch_out", None]},
                                    {"$cond": [
                                        {"$lt": ["$work_hours", 4.5]},
                                        0.5, 1
                                    ]},
                                    0.5
                                ]},
                                0
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$status", ["LEAVE", "ABSENT"]]},
                                1, 0
                            ]
                        }
                    }
                }
            },
            {"$sort": {"_id": 1}}
        ])
    )

    return {
        "department": department,
        "month": f"{year}-{month_num:02d}",
        "trend": [
            {
                "date": item["_id"],
                "total_records": item["total_records"],
                "present_days": item["present_days"],
                "leave_days": item["leave_days"]
            }
            for item in records
        ]
    }

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    explanations = {
        "health": "Checks whether the API is running.",
        "employees": "Creates or lists employee records.",
        "attendance": "Lists attendance records with filters and pagination.",
        "punch-in": "Records an employee's punch-in time.",
        "punch-out": "Records punch-out and calculates work hours.",
        "monthly": "Returns monthly attendance analytics for an employee.",
        "summary": "Returns department attendance summary.",
        "late": "Returns the late attendance leaderboard.",
        "trend": "Returns department attendance trends."
    }

    key = endpoint.strip("/").split("/")[-1]

    if key not in explanations:
        raise HTTPException(
            status_code=404,
            detail="Explanation not found for this endpoint."
        )

    return {
        "endpoint": endpoint,
        "explanation": explanations[key]
    }



