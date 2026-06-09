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
from scheduler import init_scheduler, reschedule
from followup import prewarm_response_audios
import config_loader


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

# ── GLOBAL SCHEDULER REFERENCE (for reload endpoint) ─────
_scheduler = None


@app.on_event("startup")
async def startup_event():
    global _scheduler
    _scheduler = await init_scheduler()
    _scheduler.start()

    # Pre-warm all response audios
    await prewarm_response_audios()

    print("🚀 PRA Backend started with DB-driven scheduler")

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


# ── CLINIC MEDICINES ──────────────────────────────────────

@app.get("/medicines/categories")
async def get_medicine_categories(doctor_id: str):
    from database import supabase as db
    result = db.table("clinic_medicines").select("category").eq("is_active", True).execute()
    categories = sorted(set(r["category"] for r in (result.data or []) if r.get("category")))
    return categories

@app.get("/medicines")
async def search_medicines(doctor_id: str, search: str = "", limit: int = 10):
    from database import supabase as db
    # Fetch all active medicines, filter by name in Python (ilike not reliable across versions)
    result = db.table("clinic_medicines").select("*").eq("is_active", True).order("usage_count", desc=True).limit(200).execute()
    data = result.data or []
    if search:
        q_lower = search.lower()
        data = [m for m in data if q_lower in (m.get("name") or "").lower()]
    return data[:limit]

@app.post("/medicines")
async def add_medicine(request: Request):
    from database import supabase as db
    import datetime as dt
    body = await request.json()
    result = db.table("clinic_medicines").insert({
        "doctor_id": body["doctor_id"],
        "name": body["name"],
        "category": body.get("category", "Other"),
        "dosages": body.get("dosages", []),
        "form": body.get("form", "tablet"),
        "is_active": True,
    }).execute()
    return result.data[0] if result.data else {}

@app.put("/medicines/{medicine_id}")
async def update_medicine(medicine_id: str, request: Request):
    from database import supabase as db
    import datetime as dt
    body = await request.json()
    update_data = {k: v for k, v in {
        "name": body.get("name"),
        "category": body.get("category"),
        "dosages": body.get("dosages"),
        "form": body.get("form"),
        "is_active": body.get("is_active"),
        "updated_at": dt.datetime.utcnow().isoformat(),
    }.items() if v is not None}
    result = db.table("clinic_medicines").update(update_data).eq("id", medicine_id).execute()
    return result.data[0] if result.data else {}

@app.patch("/medicines/{medicine_id}/increment-usage")
async def increment_usage(medicine_id: str):
    from database import supabase as db
    db.rpc("increment_medicine_usage", {"med_id": medicine_id}).execute()
    return {"ok": True}

@app.delete("/medicines/{medicine_id}")
async def deactivate_medicine(medicine_id: str):
    from database import supabase as db
    db.table("clinic_medicines").update({"is_active": False}).eq("id", medicine_id).execute()
    return {"ok": True}


# ── PRESCRIPTIONS WRITE ───────────────────────────────────

@app.post("/prescriptions/write")
async def write_prescription(request: Request):
    from database import supabase as db
    import datetime as dt
    import pytz

    body = await request.json()
    patient_id       = body["patient_id"]
    doctor_id_req    = body.get("doctor_id", "8c33abe0-5d2e-4613-9437-c7c375e8d162")
    appointment_id   = body.get("appointment_id") or None
    chief_complaint  = body.get("chief_complaint", "")
    diagnosis        = body.get("diagnosis", "")
    notes            = body.get("notes", "")
    dietary          = body.get("dietary_instructions", "")
    precautions      = body.get("precautions", "")
    medicines_input  = body.get("medicines", [])

    IST = pytz.timezone("Asia/Kolkata")
    now_ist = dt.datetime.now(IST)
    today_str = now_ist.date().isoformat()

    # 1. Fetch patient
    pat_res = db.table("patients").select("id, name, mobile, patient_code, language").eq("id", patient_id).execute()
    if not pat_res.data:
        return {"error": "Patient not found"}, 404
    patient = pat_res.data[0]

    # 2. Create visit
    visit_res = db.table("visits").insert({
        "patient_id":      patient_id,
        "doctor_id":       doctor_id_req,
        "appointment_id":  appointment_id,
        "chief_complaint": chief_complaint,
        "diagnosis":       diagnosis,
        "notes":           notes,
        "visit_status":    "Completed",
        "created_at":      now_ist.isoformat(),
    }).execute()
    visit = visit_res.data[0] if visit_res.data else {}
    visit_id = visit.get("id", "")

    # 3. Create prescription
    pres_res = db.table("prescriptions").insert({
        "patient_id":           patient_id,
        "doctor_id":            doctor_id_req,
        "visit_id":             visit_id,
        "prescription_date":    today_str,
        "dietary_instructions": dietary,
        "precautions":          precautions,
        "general_notes":        notes,
        "followup_whatsapp_sent": False,
        "followup_replied":     False,
        "followup_call_sent":   False,
        "created_at":           now_ist.isoformat(),
    }).execute()
    pres = pres_res.data[0] if pres_res.data else {}
    pres_id = pres.get("id", "")

    # 4. Insert medicines
    med_rows = []
    for i, m in enumerate(medicines_input):
        if not m.get("medicine_name", "").strip():
            continue
        med_rows.append({
            "prescription_id": pres_id,
            "medicine_name":   m["medicine_name"],
            "dosage":          m.get("dosage", ""),
            "morning":         m.get("morning", False),
            "afternoon":       m.get("afternoon", False),
            "evening":         m.get("evening", False),
            "night":           m.get("night", False),
            "before_food":     m.get("before_food", False),
            "duration_days":   m.get("duration_days", 5),
            "instructions":    m.get("instructions", ""),
            "sort_order":      m.get("sort_order", i + 1),
        })
    if med_rows:
        db.table("prescription_medicines").insert(med_rows).execute()

    # 5. Create followup record (7 days from today — WhatsApp channel)
    followup_date = (now_ist.date() + dt.timedelta(days=7)).isoformat()
    try:
        db.table("followups").insert({
            "patient_id":    patient_id,
            "doctor_id":     doctor_id_req,
            "visit_id":      visit_id,
            "scheduled_date": followup_date,
            "channel":       "whatsapp",
            "call_status":   "Pending",
            "followup_day":  7,
        }).execute()
    except Exception as fe:
        print(f"⚠️ Followup insert error: {fe}")

    # 6. Send WhatsApp prescription summary
    whatsapp_sent = False
    try:
        mobile   = patient.get("mobile", "")
        pname    = patient.get("name", "Patient")
        pcode    = patient.get("patient_code", "")
        language = patient.get("language", "english")

        # Build medicine lines
        timing_icons = {"morning": "🌅", "afternoon": "☀️", "evening": "🌆", "night": "🌙"}
        timing_labels_en = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening", "night": "Night"}
        timing_labels_ta = {"morning": "காலை", "afternoon": "மதியம்", "evening": "மாலை", "night": "இரவு"}

        def med_line(m, lang, idx):
            timings_keys = [k for k in ["morning", "afternoon", "evening", "night"] if m.get(k)]
            icons = " + ".join(timing_icons[k] for k in timings_keys)
            if lang == "tamil":
                labels = " + ".join(timing_labels_ta[k] for k in timings_keys)
                food = "சாப்பிடுவதற்கு முன்" if m.get("before_food") else "சாப்பிட்ட பின்"
                dur = f"{m.get('duration_days', 5)} நாட்கள்"
            else:
                labels = " + ".join(timing_labels_en[k] for k in timings_keys)
                food = "Before food" if m.get("before_food") else "After food"
                dur = f"{m.get('duration_days', 5)} days"
            inst = f"\n   {m['instructions']}" if m.get("instructions") else ""
            return f"{idx}. {m['medicine_name']} — {m.get('dosage','')}\n   {icons} {labels} | {food} | {dur}{inst}"

        med_lines = "\n\n".join(med_line(m, language, i+1) for i, m in enumerate(medicines_input) if m.get("medicine_name","").strip())

        if language == "tamil":
            msg = (
                f"💊 *மருந்துச் சீட்டு*\n"
                f"🏥 Dr. Kumar Child Care Clinic\n\n"
                f"நோயாளி: {pname}" + (f" ({pcode})" if pcode else "") + f"\n"
                f"தேதி: {now_ist.strftime('%d %b %Y')}\n"
                f"நோய்: {diagnosis}\n\n"
                f"மருந்துகள்:\n{med_lines}"
            )
            if dietary:
                msg += f"\n\n🥗 உணவு: {dietary}"
            if precautions:
                msg += f"\n⚠️ எச்சரிக்கை: {precautions}"
            msg += f"\n\nFollow-up: 3 நாட்களில் வரவும்.\nகேள்விகளுக்கு MENU என்று reply பண்ணுங்கள்."
        else:
            msg = (
                f"💊 *Your Prescription*\n"
                f"🏥 Dr. Kumar Child Care Clinic\n\n"
                f"Patient: {pname}" + (f" ({pcode})" if pcode else "") + f"\n"
                f"Date: {now_ist.strftime('%d %b %Y')}\n"
                f"Diagnosis: {diagnosis}\n\n"
                f"Medicines:\n{med_lines}"
            )
            if dietary:
                msg += f"\n\n🥗 Diet: {dietary}"
            if precautions:
                msg += f"\n⚠️ Precautions: {precautions}"
            msg += f"\n\nFollow-up in 3 days if not improving.\nReply MENU for any help."

        if mobile:
            from scheduler import send_whatsapp as _wa
            _wa(mobile, msg)
            whatsapp_sent = True

    except Exception as we:
        print(f"⚠️ WhatsApp send error: {we}")

    return {
        "prescription_id": pres_id,
        "visit_id": visit_id,
        "whatsapp_sent": whatsapp_sent,
        "patient_name": patient.get("name", ""),
    }


# ── CLINIC CONFIG ─────────────────────────────────────────

@app.get("/config/{doctor_id}")
async def get_config(doctor_id: str):
    """Return all config rows for a doctor as typed dict."""
    result = config_loader._sb.table("clinic_config") \
        .select("config_key, config_value, config_type, description, updated_at") \
        .eq("doctor_id", doctor_id) \
        .order("config_key") \
        .execute()
    return result.data or []


@app.patch("/config/{doctor_id}/{config_key}")
async def update_config(doctor_id: str, config_key: str, request: Request):
    """Upsert a single config key for a doctor."""
    import datetime as dt
    body = await request.json()
    config_value = body.get("config_value", "")
    result = config_loader._sb.table("clinic_config").upsert({
        "doctor_id": doctor_id,
        "config_key": config_key,
        "config_value": str(config_value),
        "updated_at": dt.datetime.utcnow().isoformat(),
    }, on_conflict="doctor_id,config_key").execute()
    # Invalidate in-process cache
    config_loader.invalidate_cache()
    return result.data[0] if result.data else {}


@app.post("/config/reload-scheduler")
async def reload_scheduler_endpoint():
    """Invalidate config cache and reschedule all jobs with fresh DB config."""
    global _scheduler
    if _scheduler is None:
        return {"status": "error", "message": "Scheduler not initialized"}
    await reschedule(_scheduler)
    return {"status": "ok", "message": "Scheduler reloaded from DB config"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


# ── DASHBOARD ─────────────────────────────────────────────
@app.get("/dashboard/stats")
async def dashboard_stats(doctor_id: str):
    from database import supabase
    today = date.today().isoformat()

    today_appts = supabase.table("appointments").select("id", count="exact").eq("doctor_id", doctor_id).eq("appointment_date", today).execute()
    # tokens uses queue_date, not appointment_date
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("queue_date", today).execute()
    # patients table has no doctor_id — single-clinic deployment, count all patients
    all_patients = supabase.table("patients").select("id", count="exact").execute()
    total_patients = all_patients.count or 0
    # followups table (no underscore), filter by call_status not status
    pending_followups = supabase.table("followups").select("id", count="exact").eq("doctor_id", doctor_id).is_("completed_at", "null").execute()
    today_completed = supabase.table("appointments").select("id", count="exact").eq("doctor_id", doctor_id).eq("appointment_date", today).eq("status", "completed").execute()

    week_map = defaultdict(int)
    for i in range(6, -1, -1):
        week_map[(date.today() - timedelta(days=i)).isoformat()] = 0
    week_appts = supabase.table("appointments").select("appointment_date").eq("doctor_id", doctor_id).gte("appointment_date", (date.today() - timedelta(days=6)).isoformat()).lte("appointment_date", today).execute()
    for row in (week_appts.data or []):
        week_map[row["appointment_date"]] += 1
    weekly = [{"date": d, "count": c} for d, c in sorted(week_map.items())]

    # diagnosis lives in visits table, not prescriptions
    visits = supabase.table("visits").select("diagnosis").eq("doctor_id", doctor_id).execute()
    diag_map = defaultdict(int)
    for row in (visits.data or []):
        if row.get("diagnosis"):
            diag_map[row["diagnosis"]] += 1
    top_diagnoses = sorted([{"diagnosis": k, "count": v} for k, v in diag_map.items()], key=lambda x: -x["count"])[:5]

    return {
        "today_appointments": today_appts.count or 0,
        "current_token": (token_row.data[0]["current_token"] if token_row.data else 0),
        "total_patients": total_patients,
        "pending_followups": pending_followups.count or 0,
        "today_completed": today_completed.count or 0,
        "weekly_appointments": weekly,
        "top_diagnoses": top_diagnoses,
    }


# ── PATIENTS ──────────────────────────────────────────────
@app.get("/patients")
async def list_patients(doctor_id: str, search: str = ""):
    from database import supabase
    # patients table has no doctor_id — single-clinic deployment, return all patients
    q = supabase.table("patients").select("*")
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


@app.get("/patients/{patient_id}/visits")
async def get_patient_visits(patient_id: str):
    from database import supabase
    result = supabase.table("visits") \
        .select("*, appointments(appointment_date, token_number)") \
        .eq("patient_id", patient_id) \
        .order("created_at", desc=True) \
        .limit(5) \
        .execute()
    return result.data or []


# ── APPOINTMENTS ──────────────────────────────────────────
@app.get("/appointments/today")
async def today_appointments(doctor_id: str):
    from database import supabase
    today = date.today().isoformat()
    result = supabase.table("appointments").select("*, patients(*)").eq("doctor_id", doctor_id).eq("appointment_date", today).order("token_number", desc=False).execute()
    return result.data or []


@app.get("/appointments")
async def list_appointments(doctor_id: str, date: str = "", date_from: str = "", date_to: str = "", patient_id: str = ""):
    from database import supabase
    q = supabase.table("appointments").select("*, patients(*)").eq("doctor_id", doctor_id)
    if patient_id:
        q = q.eq("patient_id", patient_id)
    if date:
        q = q.eq("appointment_date", date)
    elif date_from and date_to:
        q = q.gte("appointment_date", date_from).lte("appointment_date", date_to)
    result = q.order("appointment_date", desc=True).order("token_number", desc=False).limit(50).execute()
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
    import datetime as dt
    d = date if date else dt.date.today().isoformat()
    # tokens uses queue_date
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("queue_date", d).execute()
    current = token_row.data[0]["current_token"] if token_row.data else 0

    appts = supabase.table("appointments").select("*, patients(*)").eq("doctor_id", doctor_id).eq("appointment_date", d).order("token_number", desc=False).execute()
    all_appts = appts.data or []
    confirmed = [a for a in all_appts if a.get("status") == "Confirmed"]
    seen    = [a for a in confirmed if (a.get("token_number") or 0) < current]
    waiting = [a for a in confirmed if (a.get("token_number") or 0) > current]

    return {
        "current_token": current,
        "total_today": len(all_appts),
        "waiting": len(waiting),
        "completed": len(seen),
        "appointments": all_appts,
    }


@app.post("/queue/next")
async def queue_next(request: Request):
    from database import supabase
    import datetime as dt
    body = await request.json()
    doctor_id = body["doctor_id"]
    today = dt.date.today().isoformat()
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("queue_date", today).execute()
    new_token = (token_row.data[0]["current_token"] if token_row.data else 0) + 1
    if token_row.data:
        supabase.table("tokens").update({"current_token": new_token}).eq("doctor_id", doctor_id).eq("queue_date", today).execute()
    else:
        supabase.table("tokens").insert({"doctor_id": doctor_id, "queue_date": today, "current_token": new_token}).execute()
    return {"token": new_token}


@app.post("/queue/prev")
async def queue_prev(request: Request):
    from database import supabase
    import datetime as dt
    body = await request.json()
    doctor_id = body["doctor_id"]
    today = dt.date.today().isoformat()
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("queue_date", today).execute()
    current = token_row.data[0]["current_token"] if token_row.data else 1
    new_token = max(1, current - 1)
    if token_row.data:
        supabase.table("tokens").update({"current_token": new_token}).eq("doctor_id", doctor_id).eq("queue_date", today).execute()
    else:
        supabase.table("tokens").insert({"doctor_id": doctor_id, "queue_date": today, "current_token": new_token}).execute()
    return {"token": new_token}


@app.post("/queue/set-token")
async def queue_set_token(request: Request):
    from database import supabase
    import datetime as dt
    body = await request.json()
    doctor_id = body["doctor_id"]
    token = body["token"]
    today = dt.date.today().isoformat()
    token_row = supabase.table("tokens").select("current_token").eq("doctor_id", doctor_id).eq("queue_date", today).execute()
    if token_row.data:
        supabase.table("tokens").update({"current_token": token}).eq("doctor_id", doctor_id).eq("queue_date", today).execute()
    else:
        supabase.table("tokens").insert({"doctor_id": doctor_id, "queue_date": today, "current_token": token}).execute()
    return {"token": token}


# ── PRESCRIPTIONS ─────────────────────────────────────────
@app.get("/prescriptions/active")
async def active_prescriptions(doctor_id: str):
    from database import supabase
    # prescriptions joins via visit_id → visits; filter by doctor_id directly on prescriptions
    result = supabase.table("prescriptions").select("*, patients(name, mobile, patient_code), prescription_medicines(*)").eq("doctor_id", doctor_id).order("created_at", desc=True).execute()
    return result.data or []


@app.get("/prescriptions")
async def list_prescriptions(doctor_id: str, patient_id: str = ""):
    from database import supabase
    q = supabase.table("prescriptions").select("*, patients(name, mobile, patient_code), prescription_medicines(*)").eq("doctor_id", doctor_id)
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
    # followups table (no underscore); pending = completed_at is null
    result = supabase.table("followups").select("*, patients(name, mobile, language)").eq("doctor_id", doctor_id).is_("completed_at", "null").order("created_at", desc=True).execute()
    return result.data or []


@app.get("/followups")
async def list_followups(doctor_id: str):
    from database import supabase
    result = supabase.table("followups").select("*, patients(name, mobile, language)").eq("doctor_id", doctor_id).order("created_at", desc=True).execute()
    return result.data or []


# ── QUERIES ───────────────────────────────────────────────
@app.get("/queries/pending")
async def pending_queries(doctor_id: str):
    from database import supabase
    # table is "queries", not "patient_queries"
    result = supabase.table("queries").select("*, patients(name, mobile, patient_code, age, gender, language, created_at)").eq("doctor_id", doctor_id).eq("status", "Pending").order("created_at", desc=True).execute()
    return result.data or []


@app.get("/queries")
async def list_queries(doctor_id: str, patient_id: str = ""):
    from database import supabase
    q = supabase.table("queries").select("*, patients(name, mobile, patient_code, age, gender, language, created_at)").eq("doctor_id", doctor_id)
    if patient_id:
        q = q.eq("patient_id", patient_id)
    result = q.order("created_at", desc=True).execute()
    return result.data or []


@app.patch("/queries/{query_id}/answer")
async def answer_query(query_id: str, request: Request):
    from database import supabase
    import datetime as dt
    body = await request.json()
    reply_text = body["answer"]
    # replied_by is a UUID column — fetch doctor_id, patient_id and original question
    q_row = supabase.table("queries").select("doctor_id, patient_id, question").eq("id", query_id).execute()
    doctor_id = q_row.data[0]["doctor_id"] if q_row.data else None
    patient_id = q_row.data[0]["patient_id"] if q_row.data else None
    question_text = q_row.data[0]["question"] if q_row.data else ""
    update = {
        "reply": reply_text,
        "status": "Closed",
        "replied_at": dt.datetime.utcnow().isoformat(),
    }
    if doctor_id:
        update["replied_by"] = doctor_id
    result = supabase.table("queries").update(update).eq("id", query_id).execute()

    # Send WhatsApp notification to patient (non-blocking — DB save already succeeded)
    try:
        if patient_id:
            pat = supabase.table("patients").select("mobile, patient_code").eq("id", patient_id).execute()
            mobile = pat.data[0]["mobile"] if pat.data else None
            patient_code = pat.data[0].get("patient_code", "") if pat.data else ""
            if mobile:
                msg = (
                    f"👨‍⚕️ *Dr. Kumar Child Care Clinic*\n\n"
                    f"Patient: *{patient_code}*\n\n"
                    f"Dr. Kumar has replied to your question:\n\n"
                    f"*Your question:* _{question_text}_\n"
                    f"*Dr. Kumar's reply:* _{reply_text}_\n\n"
                    f"Reply MENU for main menu."
                )
                send_whatsapp(mobile, msg)
                print(f"✅ WhatsApp reply sent for query {query_id} to {mobile}")
            else:
                print(f"⚠️ No mobile found for patient {patient_id}, skipping WhatsApp")
    except Exception as e:
        print(f"❌ WhatsApp reply failed for query {query_id}: {e}")

    return result.data[0] if result.data else {}


# ── REVIEWS ───────────────────────────────────────────────
@app.get("/reviews")
async def list_reviews(doctor_id: str):
    from database import supabase
    # table is "reviews" not "review_requests", sort by created_at
    result = supabase.table("reviews").select("*, patients(name, mobile)").eq("doctor_id", doctor_id).order("created_at", desc=True).execute()
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
