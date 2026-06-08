"""
seed_test_data.py — Full test data reset for PRA

Wipes all patient-related tables (keeps doctors) then inserts
fresh records that exercise every scheduled job and flow:

  Flow                       Trigger
  ──────────────────────     ────────────────────────────────────────
  Morning medicine reminder  prescription_date=today, active medicines
  Evening medicine reminder  same prescription, night=True medicines
  Visit summary              visit with visit_date=today, status=Completed
  Followup WhatsApp          followups row, scheduled_date=today, Pending
  Followup voice call        prescription with whatsapp_sent=True, no reply
  Google review              visit created 7 days ago (IST)
  Patient asks doctor        query with status=Pending
  Doctor replies             query with status=Closed + reply text
  WhatsApp conversation      conversation_state for a mobile in idle

Usage:
  cd pra-backend
  source venv/bin/activate
  python3 seed_test_data.py
"""

import os
import sys
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import pytz

load_dotenv()

from supabase import create_client
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

IST = pytz.timezone("Asia/Kolkata")

def ist_today():
    return datetime.now(IST).date()

def ist_days_ago(n):
    return (ist_today() - timedelta(days=n))

def ist_days_ahead(n):
    return (ist_today() + timedelta(days=n))

# ── Constants ───────────────────────────────────────────────────────────
DOCTOR_ID  = "8c33abe0-5d2e-4613-9437-c7c375e8d162"
TEST_MOBILE = "919047099959"   # WhatsApp messages go here

TODAY      = ist_today().isoformat()
YESTERDAY  = ist_days_ago(1).isoformat()
SEVEN_AGO  = ist_days_ago(7).isoformat()
THREE_DAYS = ist_days_ahead(3).isoformat()

# Predictable UUIDs (prefix TEST- makes them easy to spot)
P1 = "aaaaaaaa-0001-0001-0001-000000000001"  # Morning/evening reminder
P2 = "aaaaaaaa-0002-0002-0002-000000000002"  # Followup WhatsApp today
P3 = "aaaaaaaa-0003-0003-0003-000000000003"  # Followup voice call
P4 = "aaaaaaaa-0004-0004-0004-000000000004"  # Visit summary today
P5 = "aaaaaaaa-0005-0005-0005-000000000005"  # Google review (7 days ago)
P6 = "aaaaaaaa-0006-0006-0006-000000000006"  # Query (pending)
P7 = "aaaaaaaa-0007-0007-0007-000000000007"  # Family: child of P1

A1 = "bbbbbbbb-0001-0001-0001-000000000001"  # P1 appt today
A2 = "bbbbbbbb-0002-0002-0002-000000000002"  # P2 appt today
A3 = "bbbbbbbb-0003-0003-0003-000000000003"  # P3 appt yesterday
A4 = "bbbbbbbb-0004-0004-0004-000000000004"  # P4 appt today
A5 = "bbbbbbbb-0005-0005-0005-000000000005"  # P5 appt 7 days ago
A6 = "bbbbbbbb-0006-0006-0006-000000000006"  # P6 appt today

V1 = "cccccccc-0001-0001-0001-000000000001"  # P1 visit today (morning reminder src)
V2 = "cccccccc-0002-0002-0002-000000000002"  # P2 visit (prescription ended)
V3 = "cccccccc-0003-0003-0003-000000000003"  # P3 visit (voice call pending)
V4 = "cccccccc-0004-0004-0004-000000000004"  # P4 visit today (visit summary)
V5 = "cccccccc-0005-0005-0005-000000000005"  # P5 visit 7 days ago (review)
V6 = "cccccccc-0006-0006-0006-000000000006"  # P6 visit (query src)

RX1 = "dddddddd-0001-0001-0001-000000000001"  # P1 active prescription
RX2 = "dddddddd-0002-0002-0002-000000000002"  # P2 ended prescription (followup WhatsApp due)
RX3 = "dddddddd-0003-0003-0003-000000000003"  # P3 prescription (voice call pending)
RX4 = "dddddddd-0004-0004-0004-000000000004"  # P4 prescription today

FU1 = "eeeeeeee-0001-0001-0001-000000000001"  # P2 followup scheduled today
FU2 = "eeeeeeee-0002-0002-0002-000000000002"  # P6 followup 3 days ahead

Q1  = "ffffffff-0001-0001-0001-000000000001"  # Pending query from P6
Q2  = "ffffffff-0002-0002-0002-000000000002"  # Closed/answered query from P6

# ── Wipe ────────────────────────────────────────────────────────────────
def wipe_all():
    """Delete all rows in dependency order (child → parent)."""
    # Tables with text primary keys need different sentinel
    uuid_tables = [
        "prescription_medicines",
        "prescriptions",
        "followups",
        "queries",
        "reviews",
        "visits",
        "appointments",
        "tokens",
        "patients",
    ]
    # conversation_state has a text 'mobile' PK, not uuid
    print("🗑️  Wiping tables...")
    for table in uuid_tables:
        try:
            # Use gte with zero-uuid to match all rows
            supabase.table(table).delete().gte("id", "00000000-0000-0000-0000-000000000000").execute()
            print(f"   ✓ {table}")
        except Exception as e:
            print(f"   ✗ {table}: {e}")
    # conversation_state: mobile is text PK
    try:
        supabase.table("conversation_state").delete().neq("mobile", "").execute()
        print("   ✓ conversation_state")
    except Exception as e:
        print(f"   ✗ conversation_state: {e}")

# ── Seed ────────────────────────────────────────────────────────────────

def seed_patients():
    rows = [
        # P1 — Arjun Tamil: active prescription → morning + evening reminders
        {
            "id": P1, "patient_code": "ARJ-9959-1990", "name": "Arjun Kumar",
            "mobile": TEST_MOBILE, "age": 34, "gender": "Male",
            "language": "tamil", "registration_source": "whatsapp",
            "created_at": f"{TODAY}T08:00:00+05:30",
        },
        # P7 — Arjun's child (family member, same mobile)
        {
            "id": P7, "patient_code": "ARI-9959-2020", "name": "Ariya Kumar",
            "mobile": TEST_MOBILE, "age": 4, "gender": "Female",
            "language": "tamil", "registration_source": "whatsapp",
            "family_head_mobile": TEST_MOBILE,
            "created_at": f"{TODAY}T08:01:00+05:30",
        },
        # P2 — Priya English: prescription ended yesterday → followup WhatsApp today
        {
            "id": P2, "patient_code": "PRI-9959-1995", "name": "Priya Nair",
            "mobile": TEST_MOBILE, "age": 29, "gender": "Female",
            "language": "english", "registration_source": "whatsapp",
            "created_at": f"{YESTERDAY}T09:00:00+05:30",
        },
        # P3 — Ravi Hindi: WA followup sent, no reply → voice call due
        {
            "id": P3, "patient_code": "RAV-9959-1985", "name": "Ravi Sharma",
            "mobile": TEST_MOBILE, "age": 39, "gender": "Male",
            "language": "hindi", "registration_source": "whatsapp",
            "created_at": f"{YESTERDAY}T10:00:00+05:30",
        },
        # P4 — Meena Tamil: visited today → visit summary (6 PM job)
        {
            "id": P4, "patient_code": "MEE-9959-1988", "name": "Meena Devi",
            "mobile": TEST_MOBILE, "age": 36, "gender": "Female",
            "language": "tamil", "registration_source": "whatsapp",
            "created_at": f"{TODAY}T09:30:00+05:30",
        },
        # P5 — Suresh English: visited 7 days ago → Google review
        {
            "id": P5, "patient_code": "SUR-9959-1975", "name": "Suresh Babu",
            "mobile": TEST_MOBILE, "age": 49, "gender": "Male",
            "language": "english", "registration_source": "whatsapp",
            "created_at": f"{SEVEN_AGO}T08:00:00+05:30",
        },
        # P6 — Lakshmi Tamil: has a pending query + an answered query
        {
            "id": P6, "patient_code": "LAK-9959-1992", "name": "Lakshmi S",
            "mobile": TEST_MOBILE, "age": 32, "gender": "Female",
            "language": "tamil", "registration_source": "whatsapp",
            "created_at": f"{TODAY}T07:00:00+05:30",
        },
    ]
    supabase.table("patients").insert(rows).execute()
    print(f"   ✓ patients ({len(rows)} rows)")


def seed_appointments():
    rows = [
        {"id": A1, "patient_id": P1, "doctor_id": DOCTOR_ID, "appointment_date": TODAY,     "appointment_time": "09:00", "token_number": 1, "status": "Confirmed"},
        {"id": A2, "patient_id": P2, "doctor_id": DOCTOR_ID, "appointment_date": TODAY,     "appointment_time": "09:15", "token_number": 2, "status": "Confirmed"},
        {"id": A3, "patient_id": P3, "doctor_id": DOCTOR_ID, "appointment_date": YESTERDAY, "appointment_time": "09:30", "token_number": 1, "status": "Confirmed"},
        {"id": A4, "patient_id": P4, "doctor_id": DOCTOR_ID, "appointment_date": TODAY,     "appointment_time": "09:45", "token_number": 3, "status": "Confirmed"},
        {"id": A5, "patient_id": P5, "doctor_id": DOCTOR_ID, "appointment_date": SEVEN_AGO, "appointment_time": "10:00", "token_number": 1, "status": "Confirmed"},
        {"id": A6, "patient_id": P6, "doctor_id": DOCTOR_ID, "appointment_date": TODAY,     "appointment_time": "10:15", "token_number": 4, "status": "Confirmed"},
    ]
    supabase.table("appointments").insert(rows).execute()
    print(f"   ✓ appointments ({len(rows)} rows)")


def seed_tokens():
    rows = [
        # Current token = 2 → token 1=Done, 2=In Progress, 3,4=Waiting
        {"doctor_id": DOCTOR_ID, "queue_date": TODAY, "current_token": 2, "total_tokens": 4, "is_active": True},
    ]
    supabase.table("tokens").insert(rows).execute()
    print("   ✓ tokens (today: current=2, total=4)")


def seed_visits():
    rows = [
        # V1 — P1 today: active visit (prescription will be added)
        {
            "id": V1, "patient_id": P1, "doctor_id": DOCTOR_ID, "appointment_id": A1,
            "visit_date": TODAY,
            "chief_complaint": "Fever, body ache",
            "symptoms": "Fever 38.5°C, headache, body ache",
            "diagnosis": "Viral fever",
            "notes": "Rest for 3 days. Plenty of fluids.",
            "follow_up_date": THREE_DAYS,
            "follow_up_notes": "Review after 3 days if fever persists",
            "visit_status": "Completed",
            "created_at": f"{TODAY}T10:00:00+05:30",
        },
        # V2 — P2 yesterday: prescription ended (followup WA due today)
        {
            "id": V2, "patient_id": P2, "doctor_id": DOCTOR_ID, "appointment_id": A2,
            "visit_date": YESTERDAY,
            "chief_complaint": "Sore throat",
            "diagnosis": "Acute pharyngitis",
            "visit_status": "Completed",
            "created_at": f"{YESTERDAY}T11:00:00+05:30",
        },
        # V3 — P3 yesterday: WA followup sent, awaiting voice call
        {
            "id": V3, "patient_id": P3, "doctor_id": DOCTOR_ID, "appointment_id": A3,
            "visit_date": YESTERDAY,
            "chief_complaint": "Knee pain",
            "diagnosis": "Osteoarthritis right knee",
            "visit_status": "Completed",
            "created_at": f"{YESTERDAY}T10:30:00+05:30",
        },
        # V4 — P4 TODAY → triggers visit summary tonight
        {
            "id": V4, "patient_id": P4, "doctor_id": DOCTOR_ID, "appointment_id": A4,
            "visit_date": TODAY,
            "chief_complaint": "Cough, cold",
            "diagnosis": "Upper Respiratory Infection",
            "notes": "Avoid cold drinks. Steam inhalation twice daily.",
            "follow_up_date": THREE_DAYS,
            "visit_status": "Completed",
            "created_at": f"{TODAY}T11:30:00+05:30",
        },
        # V5 — P5 SEVEN DAYS AGO → triggers Google review today
        {
            "id": V5, "patient_id": P5, "doctor_id": DOCTOR_ID, "appointment_id": A5,
            "visit_date": SEVEN_AGO,
            "chief_complaint": "Back pain",
            "diagnosis": "Lumbar muscle strain",
            "visit_status": "Completed",
            "created_at": f"{SEVEN_AGO}T10:00:00+05:30",
        },
        # V6 — P6 today: query-related visit
        {
            "id": V6, "patient_id": P6, "doctor_id": DOCTOR_ID, "appointment_id": A6,
            "visit_date": TODAY,
            "chief_complaint": "Loose stools, stomach pain",
            "diagnosis": "Gastroenteritis",
            "visit_status": "Completed",
            "created_at": f"{TODAY}T09:00:00+05:30",
        },
    ]
    supabase.table("visits").insert(rows).execute()
    print(f"   ✓ visits ({len(rows)} rows)")


def seed_prescriptions():
    rows = [
        # RX1 — P1: ACTIVE 5-day course starting today → morning + evening reminders
        {
            "id": RX1, "patient_id": P1, "doctor_id": DOCTOR_ID, "visit_id": V1,
            "prescription_date": TODAY,
            "dietary_instructions": "Avoid oily food. Drink ORS if needed.",
            "precautions": "Rest. Avoid exposure to cold.",
            "general_notes": "Review after 3 days if fever persists.",
            "followup_whatsapp_sent": False, "followup_replied": False, "followup_call_sent": False,
        },
        # RX2 — P2: 3-day course started 3 days ago → ENDED YESTERDAY → followup WA today
        {
            "id": RX2, "patient_id": P2, "doctor_id": DOCTOR_ID, "visit_id": V2,
            "prescription_date": ist_days_ago(3).isoformat(),
            "dietary_instructions": "Warm water gargles. Avoid cold drinks.",
            "followup_whatsapp_sent": False, "followup_replied": False, "followup_call_sent": False,
        },
        # RX3 — P3: WA followup SENT, no reply → voice call pending
        {
            "id": RX3, "patient_id": P3, "doctor_id": DOCTOR_ID, "visit_id": V3,
            "prescription_date": YESTERDAY,
            "dietary_instructions": "Apply warm compress.",
            "followup_whatsapp_sent": True,
            "followup_whatsapp_sent_at": f"{YESTERDAY}T08:00:00+05:30",
            "followup_replied": False,
            "followup_call_sent": False,
        },
        # RX4 — P4: prescription today (visit summary patient)
        {
            "id": RX4, "patient_id": P4, "doctor_id": DOCTOR_ID, "visit_id": V4,
            "prescription_date": TODAY,
            "dietary_instructions": "Steam inhalation. Warm soups.",
            "precautions": "Avoid cold drinks and ice cream.",
            "followup_whatsapp_sent": False, "followup_replied": False, "followup_call_sent": False,
        },
    ]
    supabase.table("prescriptions").insert(rows).execute()
    print(f"   ✓ prescriptions ({len(rows)} rows)")


def seed_prescription_medicines():
    rows = [
        # RX1 — P1 active medicines (morning + afternoon + night → both reminder jobs)
        {"prescription_id": RX1, "medicine_name": "Paracetamol 650mg",   "dosage": "1 tablet", "morning": True,  "afternoon": True,  "evening": False, "night": True,  "before_food": False, "duration_days": 5, "sort_order": 1},
        {"prescription_id": RX1, "medicine_name": "Cetirizine 10mg",     "dosage": "1 tablet", "morning": False, "afternoon": False, "evening": False, "night": True,  "before_food": False, "duration_days": 5, "sort_order": 2},
        {"prescription_id": RX1, "medicine_name": "ORS Sachet",          "dosage": "1 sachet", "morning": True,  "afternoon": True,  "evening": True,  "night": False, "before_food": False, "duration_days": 3, "sort_order": 3},

        # RX2 — P2 ended medicines (3-day course, started 3 days ago → ended yesterday)
        {"prescription_id": RX2, "medicine_name": "Azithromycin 500mg",  "dosage": "1 tablet", "morning": True,  "afternoon": False, "evening": False, "night": False, "before_food": False, "duration_days": 3, "sort_order": 1},
        {"prescription_id": RX2, "medicine_name": "Ibuprofen 400mg",     "dosage": "1 tablet", "morning": True,  "afternoon": True,  "evening": False, "night": False, "before_food": True,  "duration_days": 3, "sort_order": 2},

        # RX3 — P3 (voice call patient)
        {"prescription_id": RX3, "medicine_name": "Diclofenac 50mg",     "dosage": "1 tablet", "morning": True,  "afternoon": False, "evening": False, "night": True,  "before_food": True,  "duration_days": 5, "sort_order": 1},
        {"prescription_id": RX3, "medicine_name": "Rabeprazole 20mg",    "dosage": "1 tablet", "morning": True,  "afternoon": False, "evening": False, "night": False, "before_food": True,  "duration_days": 5, "sort_order": 2},

        # RX4 — P4 medicines (visit summary patient)
        {"prescription_id": RX4, "medicine_name": "Amoxicillin 500mg",   "dosage": "1 capsule","morning": True,  "afternoon": False, "evening": False, "night": True,  "before_food": False, "duration_days": 5, "sort_order": 1},
        {"prescription_id": RX4, "medicine_name": "Bromhexine syrup",    "dosage": "10ml",     "morning": True,  "afternoon": True,  "evening": False, "night": False, "before_food": False, "duration_days": 5, "sort_order": 2},
        {"prescription_id": RX4, "medicine_name": "Vitamin C 500mg",     "dosage": "1 tablet", "morning": True,  "afternoon": False, "evening": False, "night": False, "before_food": False, "duration_days": 5, "sort_order": 3},
    ]
    supabase.table("prescription_medicines").insert(rows).execute()
    print(f"   ✓ prescription_medicines ({len(rows)} rows)")


def seed_followups():
    rows = [
        # FU1 — P2 followup scheduled TODAY → triggers followup_whatsapp_job
        {
            "id": FU1,
            "patient_id": P2, "doctor_id": DOCTOR_ID, "visit_id": V2,
            "followup_day": 1, "scheduled_date": TODAY,
            "channel": "whatsapp", "call_status": "Pending",
            "created_at": f"{YESTERDAY}T11:00:00+05:30",
        },
        # Also one overdue from yesterday (scheduled_date <= today also picks this up)
        {
            "id": "eeeeeeee-0003-0003-0003-000000000003",
            "patient_id": P4, "doctor_id": DOCTOR_ID, "visit_id": V4,
            "followup_day": 3, "scheduled_date": TODAY,
            "channel": "whatsapp", "call_status": "Pending",
            "created_at": f"{TODAY}T11:30:00+05:30",
        },
        # FU2 — P6 followup 3 days from now (future, should NOT trigger today)
        {
            "id": FU2,
            "patient_id": P6, "doctor_id": DOCTOR_ID, "visit_id": V6,
            "followup_day": 3, "scheduled_date": THREE_DAYS,
            "channel": "whatsapp", "call_status": "Pending",
            "created_at": f"{TODAY}T09:00:00+05:30",
        },
    ]
    supabase.table("followups").insert(rows).execute()
    print(f"   ✓ followups ({len(rows)} rows)")


def seed_queries():
    rows = [
        # Q1 — P6 PENDING query (shows in Queries page, doctor hasn't replied)
        {
            "id": Q1, "patient_id": P6, "doctor_id": DOCTOR_ID, "visit_id": V6,
            "question": "Doctor, my stomach pain has reduced but I still have loose stools on day 2. Should I continue the same medicines?",
            "question_source": "whatsapp",
            "status": "Pending",
            "created_at": f"{TODAY}T14:00:00+05:30",
        },
        # Q2 — P6 CLOSED/ANSWERED query (shows in answered section)
        {
            "id": Q2, "patient_id": P6, "doctor_id": DOCTOR_ID, "visit_id": V6,
            "question": "Is ORS safe for adults?",
            "question_source": "whatsapp",
            "reply": "Yes, ORS is safe for all ages. Drink 200-400ml after each loose stool. Stay hydrated.",
            "replied_by": DOCTOR_ID,
            "status": "Closed",
            "created_at": f"{TODAY}T12:00:00+05:30",
            "replied_at": f"{TODAY}T13:00:00+05:30",
        },
        # Bonus — P1 (Arjun) asking about fever
        {
            "id": "ffffffff-0003-0003-0003-000000000003",
            "patient_id": P1, "doctor_id": DOCTOR_ID, "visit_id": V1,
            "question": "Fever came back at night to 39°C. Should I come in tomorrow?",
            "question_source": "whatsapp",
            "status": "Pending",
            "created_at": f"{TODAY}T20:00:00+05:30",
        },
    ]
    supabase.table("queries").insert(rows).execute()
    print(f"   ✓ queries ({len(rows)} rows)")


def seed_reviews():
    rows = [
        # P5 visited 7 days ago — google_review_link_sent=False → triggers review job
        {
            "patient_id": P5, "doctor_id": DOCTOR_ID, "visit_id": V5,
            "google_review_link_sent": False,
            "created_at": f"{SEVEN_AGO}T10:00:00+05:30",
        },
    ]
    supabase.table("reviews").insert(rows).execute()
    print(f"   ✓ reviews ({len(rows)} rows)")


def seed_conversation_state():
    rows = [
        # P1's mobile in idle state (ready for WhatsApp menu interactions)
        {
            "mobile": TEST_MOBILE,
            "state": "idle",
            "temp_data": {},
            "updated_at": f"{TODAY}T08:00:00+05:30",
        },
    ]
    # Use upsert — a DB trigger may recreate rows on patient insert
    supabase.table("conversation_state").upsert(rows, on_conflict="mobile").execute()
    print("   ✓ conversation_state (1 row, idle)")


# ── Runner ──────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  PRA Test Data Seed")
    print(f"  Doctor:  Dr. Kumar ({DOCTOR_ID[:8]}...)")
    print(f"  Mobile:  +{TEST_MOBILE}")
    print(f"  Today:   {TODAY}")
    print(f"{'='*60}\n")

    wipe_all()
    print()
    print("🌱 Seeding test data...")

    seed_patients()
    seed_appointments()
    seed_tokens()
    seed_visits()
    seed_prescriptions()
    seed_prescription_medicines()
    seed_followups()
    seed_queries()
    seed_reviews()
    seed_conversation_state()

    print()
    print("✅ Seed complete!")
    print()
    print("📋 What each job will find when triggered:")
    print()
    print(f"  🌅 /trigger/morning-reminders")
    print(f"     → Arjun Kumar  (Paracetamol M+A+N, Cetirizine N, ORS M+A+E) — 5-day course Day 1")
    print(f"     → Priya Nair   — course ended, skipped")
    print()
    print(f"  🌙 /trigger/evening-reminders")
    print(f"     → Arjun Kumar  (Paracetamol N, Cetirizine N) — night medicines only")
    print()
    print(f"  🏥 /trigger/visit-summary")
    print(f"     → Arjun Kumar  (Viral fever, follow-up {THREE_DAYS})")
    print(f"     → Meena Devi   (Upper Respiratory Infection, follow-up {THREE_DAYS})")
    print(f"       ⚠ visit_date=today + visit_status=Completed required")
    print()
    print(f"  💬 /trigger/followup-whatsapp")
    print(f"     → Priya Nair   (followups row, scheduled_date={TODAY}, Pending)")
    print(f"     → Meena Devi   (followups row, scheduled_date={TODAY}, Pending)")
    print()
    print(f"  📞 /trigger/followup-calls")
    print(f"     → Ravi Sharma  (RX3: WA sent, no reply, call not sent)")
    print()
    print(f"  ⭐ /trigger/review-requests")
    print(f"     → Suresh Babu  (visit created_at={SEVEN_AGO})")
    print()
    print(f"  💬 Queries page")
    print(f"     Pending  : Lakshmi S  — 'stomach pain/loose stools day 2...'")
    print(f"     Pending  : Arjun Kumar — 'Fever came back at night...'")
    print(f"     Answered : Lakshmi S  — 'Is ORS safe for adults?' → doctor replied")
    print()
    print(f"  📱 WhatsApp (send to +{TEST_MOBILE})")
    print(f"     MENU        → main menu")
    print(f"     1           → book appointment")
    print(f"     2           → check token")
    print(f"     3           → prescription")
    print(f"     7           → ask doctor (family selector: Arjun + Ariya)")
    print()
    print(f"{'='*60}\n")


if __name__ == "__main__":
    confirm = input("⚠️  This will DELETE ALL patient data and reseed. Continue? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Aborted.")
        sys.exit(0)
    main()
