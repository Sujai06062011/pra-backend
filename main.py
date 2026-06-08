from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
import os
from datetime import date, timedelta
from collections import defaultdict
from whatsapp_handler import handle_message
from scheduler import init_scheduler
from followup import prewarm_response_audios


load_dotenv()

app = FastAPI(title="PRA - Patient Relationship Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")


@app.post("/admin/onboard-clinic")
async def onboard_clinic(request: Request):
    data = await request.json()
    
    # Normalize WhatsApp number - always strip +
    whatsapp = data.get("whatsapp_number", "").replace("+", "").strip()
    
    result = supabase.table("doctors").insert({
        "name": data.get("doctor_name"),
        "clinic_name": data.get("clinic_name"),
        "whatsapp_number": whatsapp,  # always stored without +
        "clinic_timings": data.get("timings", "Mon-Sat: 9AM-1PM, 5PM-8PM"),
        "clinic_address": data.get("address", ""),
        "email": data.get("email", ""),
        "mobile": data.get("mobile", "")
    }).execute()
    
    return {"status": "success", "doctor_id": result.data[0]["id"]}

# In main.py - startup event
@app.on_event("startup")
async def startup_event():
    scheduler = init_scheduler()
    scheduler.start()
    
    # Pre-warm all response audios
    await prewarm_response_audios()
    
    print("🚀 PRA Backend started with scheduler")

def send_whatsapp(to_number: str, message: str):
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_FROM,
            to=f"whatsapp:+{to_number}",
            body=message
        )
        print(f"✅ Sent to {to_number}: SID {msg.sid}")
    except Exception as e:
        print(f"❌ Twilio error: {e}")


@app.on_event("startup")
async def startup_event():
    scheduler = init_scheduler()
    scheduler.start()
    print("🚀 PRA Backend started with scheduler")


@app.get("/")
async def root():
    return {"status": "PRA Backend Running", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    try:
        form_data = await request.form()
        from_raw = form_data.get("From", "")
        to_raw = form_data.get("To", "")
        body = form_data.get("Body", "").strip()
        media_url = form_data.get("MediaUrl0", "")

        from_number = from_raw.replace("whatsapp:+", "").replace("whatsapp:", "")
        to_number = to_raw.replace("whatsapp:", "")

        print(f"\n📱 Inbound: {from_number} → {to_number}: {body}")

        reply = await handle_message(from_number, body, to_number, media_url)
        print(f"💬 Reply: {reply[:80]}...")

        send_whatsapp(from_number, reply)

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    resp = MessagingResponse()
    return PlainTextResponse(str(resp), status_code=200, media_type="application/xml")


@app.get("/webhook/whatsapp")
async def whatsapp_verify(request: Request):
    params = dict(request.query_params)
    challenge = params.get("hub.challenge", "")
    return PlainTextResponse(challenge)


# ── TEST TRIGGER ENDPOINTS ────────────────────────────────
@app.post("/trigger/morning-reminders")
async def trigger_morning_reminders():
    from scheduler import send_morning_reminders
    await send_morning_reminders()
    return {"status": "Morning reminders sent"}


@app.post("/trigger/evening-reminders")
async def trigger_evening_reminders():
    from scheduler import send_evening_reminders
    await send_evening_reminders()
    return {"status": "Evening reminders sent"}


@app.post("/trigger/visit-summary")
async def trigger_visit_summary():
    from scheduler import send_visit_summary
    await send_visit_summary()
    return {"status": "Visit summaries sent"}


@app.post("/trigger/review-requests")
async def trigger_review_requests():
    from scheduler import send_review_requests
    await send_review_requests()
    return {"status": "Review requests sent"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


# ── DASHBOARD ─────────────────────────────────────────────
@app.get("/dashboard/stats")
async def dashboard_stats(doctor_id: str):
    from database import supabase
    today = date.today().isoformat()

    today_appts = supabase.table("appointments").select("id", count="exact").eq("doctor_id", doctor_id).eq("appointment_date", today).execute()
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("appointment_date", today).execute()
    total_patients = supabase.table("patients").select("id", count="exact").eq("doctor_id", doctor_id).execute()
    pending_followups = supabase.table("follow_ups").select("id", count="exact").eq("doctor_id", doctor_id).eq("status", "pending").execute()
    today_completed = supabase.table("appointments").select("id", count="exact").eq("doctor_id", doctor_id).eq("appointment_date", today).eq("status", "completed").execute()

    weekly = []
    week_map = defaultdict(int)
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        week_map[d] = 0
    week_appts = supabase.table("appointments").select("appointment_date").eq("doctor_id", doctor_id).gte("appointment_date", (date.today() - timedelta(days=6)).isoformat()).lte("appointment_date", today).execute()
    for row in (week_appts.data or []):
        week_map[row["appointment_date"]] += 1
    weekly = [{"date": d, "count": c} for d, c in sorted(week_map.items())]

    prescriptions = supabase.table("prescriptions").select("diagnosis").eq("doctor_id", doctor_id).execute()
    diag_map = defaultdict(int)
    for row in (prescriptions.data or []):
        if row.get("diagnosis"):
            diag_map[row["diagnosis"]] += 1
    top_diagnoses = sorted([{"diagnosis": k, "count": v} for k, v in diag_map.items()], key=lambda x: -x["count"])[:5]

    return {
        "today_appointments": today_appts.count or 0,
        "current_token": (token_row.data[0]["current_token"] if token_row.data else 0),
        "total_patients": total_patients.count or 0,
        "pending_followups": pending_followups.count or 0,
        "today_completed": today_completed.count or 0,
        "weekly_appointments": weekly,
        "top_diagnoses": top_diagnoses,
    }


# ── PATIENTS ──────────────────────────────────────────────
@app.get("/patients")
async def list_patients(doctor_id: str, search: str = ""):
    from database import supabase
    q = supabase.table("patients").select("*").eq("doctor_id", doctor_id)
    if search:
        q = q.or_(f"name.ilike.%{search}%,mobile.ilike.%{search}%")
    result = q.order("created_at", desc=True).execute()
    return result.data or []


@app.get("/patients/family/{head_mobile}")
async def family_members(head_mobile: str):
    from database import supabase
    result = supabase.table("patients").select("*").eq("family_head_mobile", head_mobile).execute()
    return result.data or []


@app.get("/patients/{patient_id}")
async def get_patient(patient_id: str):
    from database import supabase
    result = supabase.table("patients").select("*").eq("id", patient_id).single().execute()
    return result.data


# ── APPOINTMENTS ──────────────────────────────────────────
@app.get("/appointments/today")
async def today_appointments(doctor_id: str):
    from database import supabase
    today = date.today().isoformat()
    result = supabase.table("appointments").select("*, patients(*)").eq("doctor_id", doctor_id).eq("appointment_date", today).order("token_number", desc=False).execute()
    return result.data or []


@app.get("/appointments")
async def list_appointments(doctor_id: str, date: str = ""):
    from database import supabase
    q = supabase.table("appointments").select("*, patients(*)").eq("doctor_id", doctor_id)
    if date:
        q = q.eq("appointment_date", date)
    result = q.order("appointment_date", desc=True).order("token_number", desc=False).execute()
    return result.data or []


@app.patch("/appointments/{appointment_id}/status")
async def update_appointment_status(appointment_id: str, request: Request):
    from database import supabase
    body = await request.json()
    result = supabase.table("appointments").update({"status": body["status"]}).eq("id", appointment_id).execute()
    return result.data[0] if result.data else {}


# ── QUEUE ─────────────────────────────────────────────────
@app.get("/queue/status")
async def queue_status(doctor_id: str, date: str = ""):
    from database import supabase
    d = date or str(date.today()) if False else (date if date else str(__import__("datetime").date.today()))
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("appointment_date", d).execute()
    current = token_row.data[0]["current_token"] if token_row.data else 0

    total = supabase.table("appointments").select("id", count="exact").eq("doctor_id", doctor_id).eq("appointment_date", d).execute()
    completed = supabase.table("appointments").select("id", count="exact").eq("doctor_id", doctor_id).eq("appointment_date", d).eq("status", "completed").execute()
    appts = supabase.table("appointments").select("*, patients(*)").eq("doctor_id", doctor_id).eq("appointment_date", d).order("token_number", desc=False).execute()
    waiting = [a for a in (appts.data or []) if a.get("status") == "scheduled" and (a.get("token_number") or 0) >= current]

    return {
        "current_token": current,
        "total_today": total.count or 0,
        "waiting": len(waiting),
        "completed": completed.count or 0,
        "appointments": appts.data or [],
    }


@app.post("/queue/next")
async def queue_next(request: Request):
    from database import supabase
    body = await request.json()
    doctor_id = body["doctor_id"]
    today = str(__import__("datetime").date.today())
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("appointment_date", today).execute()
    new_token = (token_row.data[0]["current_token"] if token_row.data else 0) + 1
    supabase.table("tokens").upsert({"doctor_id": doctor_id, "appointment_date": today, "current_token": new_token}, on_conflict="doctor_id,appointment_date").execute()
    return {"token": new_token}


@app.post("/queue/set-token")
async def queue_set_token(request: Request):
    from database import supabase
    body = await request.json()
    doctor_id = body["doctor_id"]
    token = body["token"]
    today = str(__import__("datetime").date.today())
    supabase.table("tokens").upsert({"doctor_id": doctor_id, "appointment_date": today, "current_token": token}, on_conflict="doctor_id,appointment_date").execute()
    return {"token": token}


# ── PRESCRIPTIONS ─────────────────────────────────────────
@app.get("/prescriptions/active")
async def active_prescriptions(doctor_id: str):
    from database import supabase
    today = str(__import__("datetime").date.today())
    result = supabase.table("prescriptions").select("*, patients(name, mobile, patient_code)").eq("doctor_id", doctor_id).eq("is_active", True).gte("end_date", today).order("created_at", desc=True).execute()
    return result.data or []


@app.get("/prescriptions")
async def list_prescriptions(doctor_id: str, patient_id: str = ""):
    from database import supabase
    q = supabase.table("prescriptions").select("*, patients(name, mobile, patient_code)").eq("doctor_id", doctor_id)
    if patient_id:
        q = q.eq("patient_id", patient_id)
    result = q.order("created_at", desc=True).execute()
    return result.data or []


@app.post("/prescriptions")
async def create_prescription(request: Request):
    from database import supabase
    body = await request.json()
    result = supabase.table("prescriptions").insert(body).execute()
    return result.data[0] if result.data else {}


# ── FOLLOW-UPS ────────────────────────────────────────────
@app.get("/followups/pending")
async def pending_followups(doctor_id: str):
    from database import supabase
    result = supabase.table("follow_ups").select("*, patients(name, mobile, language)").eq("doctor_id", doctor_id).eq("status", "pending").order("created_at", desc=True).execute()
    return result.data or []


@app.get("/followups")
async def list_followups(doctor_id: str):
    from database import supabase
    result = supabase.table("follow_ups").select("*, patients(name, mobile, language)").eq("doctor_id", doctor_id).order("created_at", desc=True).execute()
    return result.data or []


# ── QUERIES ───────────────────────────────────────────────
@app.get("/queries/pending")
async def pending_queries(doctor_id: str):
    from database import supabase
    result = supabase.table("patient_queries").select("*, patients(name, mobile)").eq("doctor_id", doctor_id).eq("status", "pending").order("created_at", desc=True).execute()
    return result.data or []


@app.get("/queries")
async def list_queries(doctor_id: str):
    from database import supabase
    result = supabase.table("patient_queries").select("*, patients(name, mobile)").eq("doctor_id", doctor_id).order("created_at", desc=True).execute()
    return result.data or []


@app.patch("/queries/{query_id}/answer")
async def answer_query(query_id: str, request: Request):
    from database import supabase
    body = await request.json()
    result = supabase.table("patient_queries").update({
        "answer": body["answer"],
        "status": "answered",
        "answered_at": __import__("datetime").datetime.utcnow().isoformat(),
    }).eq("id", query_id).execute()
    return result.data[0] if result.data else {}


# ── REVIEWS ───────────────────────────────────────────────
@app.get("/reviews")
async def list_reviews(doctor_id: str):
    from database import supabase
    result = supabase.table("review_requests").select("*, patients(name, mobile)").eq("doctor_id", doctor_id).order("review_sent_at", desc=True).execute()
    return result.data or []


# ── DOCTOR ────────────────────────────────────────────────
@app.get("/doctor/{doctor_id}")
async def get_doctor(doctor_id: str):
    from database import supabase
    result = supabase.table("doctors").select("*").eq("id", doctor_id).single().execute()
    return result.data


@app.patch("/doctor/{doctor_id}")
async def update_doctor(doctor_id: str, request: Request):
    from database import supabase
    body = await request.json()
    result = supabase.table("doctors").update(body).eq("id", doctor_id).execute()
    return result.data[0] if result.data else {}


# ── VOICE WEBHOOKS ────────────────────────────────────
@app.get("/webhook/voice/followup")
@app.post("/webhook/voice/followup")
async def voice_followup(request: Request):
    """Twilio voice webhook - plays follow-up audio"""
    from followup import handle_voice_followup_webhook
    return await handle_voice_followup_webhook(request)


@app.post("/webhook/voice/followup-response")
async def voice_followup_response(request: Request):
    """Twilio voice webhook - handles keypress"""
    from followup import handle_voice_followup_response
    return await handle_voice_followup_response(request)


# ── FOLLOW-UP TRIGGER ENDPOINTS ───────────────────────
@app.post("/trigger/followup-whatsapp")
async def trigger_followup_whatsapp():
    from followup import send_followup_whatsapp_job
    await send_followup_whatsapp_job()
    return {"status": "Follow-up WhatsApp sent"}


@app.post("/trigger/followup-calls")
async def trigger_followup_calls():
    from followup import make_followup_calls_job
    await make_followup_calls_job()
    return {"status": "Follow-up calls initiated"}
