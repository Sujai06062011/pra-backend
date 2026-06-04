"""
Follow-up flow:
Day after prescription ends:
  8AM → WhatsApp message sent
  6PM → If no reply → Twilio Voice Call (Sarvam AI Tamil TTS)
"""
import os
import base64
import httpx
import tempfile
from datetime import date, datetime, timedelta
from supabase import create_client
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import PlainTextResponse

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY")
)

twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
TWILIO_VOICE_NUMBER = os.getenv("TWILIO_VOICE_NUMBER")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://web-production-e5f38.up.railway.app")

# Language config
LANGUAGE_CONFIG = {
    "tamil": {
        "code": "ta-IN",
        "speaker": "kavitha",
        "followup_message": (
            "வணக்கம் {name}! நான் Dr. {doctor} Clinic இலிருந்து பேசுகிறேன். "
            "உங்கள் மருந்து கோர்ஸ் முடிந்தது. "
            "நீங்கள் எப்படி உணர்கிறீர்கள்? "
            "நலமாக இருந்தால் 1 அழுத்தவும். "
            "மீண்டும் மருத்துவரை சந்திக்க வேண்டும் என்றால் 2 அழுத்தவும்."
        )
    },
    "hindi": {
        "code": "hi-IN",
        "speaker": "priya",
        "followup_message": (
            "नमस्ते {name}! मैं Dr. {doctor} Clinic से बोल रही हूं। "
            "आपका दवाई का कोर्स पूरा हो गया है। "
            "आप कैसा महसूस कर रहे हैं? "
            "अच्छा महसूस हो रहा है तो 1 दबाएं। "
            "डॉक्टर से मिलना है तो 2 दबाएं।"
        )
    },
    "english": {
        "code": "en-IN",
        "speaker": "ritu",
        "followup_message": (
            "Hello {name}! This is a call from Dr. {doctor} Clinic. "
            "Your medicine course has been completed. "
            "How are you feeling? "
            "Press 1 if you are feeling better. "
            "Press 2 if you would like to book an appointment."
        )
    }
}


def get_prescriptions_ending_today():
    """
    Get all prescriptions where the medicine course ends today.
    i.e. prescription_date + max(duration_days) = yesterday
    So today is the day AFTER the course ended.
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # Get all prescriptions with their medicines
    result = supabase.table("prescriptions").select(
        "id, prescription_date, followup_whatsapp_sent, followup_replied, "
        "followup_call_sent, patient_id, doctor_id, "
        "patients(name, mobile, language), "
        "doctors(name, clinic_name), "
        "prescription_medicines(duration_days)"
    ).eq("followup_whatsapp_sent", False).execute()

    prescriptions = result.data or []
    due_prescriptions = []

    for pres in prescriptions:
        medicines = pres.get("prescription_medicines", [])
        if not medicines:
            continue

        pres_date_str = pres.get("prescription_date", "")
        if not pres_date_str:
            continue

        pres_date = datetime.strptime(pres_date_str, "%Y-%m-%d").date()
        max_duration = max(m.get("duration_days", 1) for m in medicines)
        course_end = pres_date + timedelta(days=max_duration - 1)

        # Course ended yesterday = follow up today
        if course_end.isoformat() == yesterday:
            due_prescriptions.append(pres)

    return due_prescriptions


def get_prescriptions_needing_call():
    """
    Get prescriptions where:
    - WhatsApp was sent today
    - No reply received
    - Voice call not yet made
    """
    result = supabase.table("prescriptions").select(
        "id, patient_id, doctor_id, "
        "patients(name, mobile, language), "
        "doctors(name, clinic_name)"
    ).eq("followup_whatsapp_sent", True).eq(
        "followup_replied", False
    ).eq("followup_call_sent", False).execute()

    return result.data or []


async def generate_sarvam_audio(text: str, language: str) -> bytes:
    """Generate audio using Sarvam AI Bulbul v3"""
    config = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["english"])

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "inputs": [text],
                "target_language_code": config["code"],
                "speaker": config["speaker"],
                "model": "bulbul:v3",
                "audio_format": "wav",
                "pace": 0.9,
                "enable_preprocessing": True
            },
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()

        # Decode base64 audio
        audio_b64 = data["audios"][0]
        return base64.b64decode(audio_b64)


async def upload_audio_to_twilio(audio_bytes: bytes, filename: str) -> str:
    """
    Save audio to a temp file and return URL.
    In production, upload to S3/Supabase Storage.
    For now use Twilio's built-in hosting via TwiML.
    """
    # Save to Supabase Storage
    file_path = f"followup_audio/{filename}"
    result = supabase.storage.from_("clinic-audio").upload(
        file_path,
        audio_bytes,
        {"content-type": "audio/wav", "upsert": True}
    )

    # Get public URL
    url_result = supabase.storage.from_("clinic-audio").get_public_url(file_path)
    return url_result


async def send_followup_whatsapp(pres: dict):
    """Send WhatsApp follow-up message"""
    patient = pres.get("patients", {})
    doctor = pres.get("doctors", {})
    patient_name = patient.get("name", "Patient")
    mobile = patient.get("mobile", "")
    language = patient.get("language", "tamil")
    clinic_name = doctor.get("clinic_name", "Clinic")

    if not mobile:
        return

    # Build message based on language
    if language == "tamil":
        message = (
            f"வணக்கம் {patient_name}! 🙏\n\n"
            f"உங்கள் மருந்து கோர்ஸ் முடிந்தது.\n"
            f"நீங்கள் எப்படி உணர்கிறீர்கள்?\n\n"
            f"1. நலமாக இருக்கிறேன் 😊\n"
            f"2. இன்னும் குணமாகவில்லை 🤒\n"
            f"3. மீண்டும் மருத்துவரை சந்திக்க வேண்டும் 🏥\n\n"
            f"- {clinic_name}"
        )
    elif language == "hindi":
        message = (
            f"नमस्ते {patient_name}! 🙏\n\n"
            f"आपका दवाई का कोर्स पूरा हो गया है।\n"
            f"आप कैसा महसूस कर रहे हैं?\n\n"
            f"1. बहुत बेहतर हूं 😊\n"
            f"2. अभी भी ठीक नहीं हूं 🤒\n"
            f"3. डॉक्टर से मिलना है 🏥\n\n"
            f"- {clinic_name}"
        )
    else:
        message = (
            f"Hello {patient_name}! 🙏\n\n"
            f"Your medicine course has been completed.\n"
            f"How are you feeling today?\n\n"
            f"1. Much better 😊\n"
            f"2. Still recovering 🤒\n"
            f"3. Need to see doctor again 🏥\n\n"
            f"- {clinic_name}"
        )

    try:
        twilio_client.messages.create(
            from_=TWILIO_FROM,
            to=f"whatsapp:+{mobile}",
            body=message
        )

        # Mark as sent in DB
        supabase.table("prescriptions").update({
            "followup_whatsapp_sent": True,
            "followup_whatsapp_sent_at": datetime.now().isoformat()
        }).eq("id", pres["id"]).execute()

        print(f"✅ Follow-up WhatsApp sent to {patient_name} ({mobile})")

    except Exception as e:
        print(f"❌ Error sending follow-up WhatsApp: {e}")


async def make_followup_call(pres: dict):
    """Make Twilio voice call with Sarvam AI audio"""
    patient = pres.get("patients", {})
    doctor = pres.get("doctors", {})
    patient_name = patient.get("name", "Patient")
    mobile = patient.get("mobile", "")
    language = patient.get("language", "tamil")
    doctor_name = doctor.get("name", "Doctor")
    pres_id = pres["id"]

    if not mobile:
        return

    config = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["english"])
    script = config["followup_message"].format(
        name=patient_name,
        doctor=doctor_name
    )

    try:
        # Generate audio with Sarvam AI
        print(f"🎙️ Generating {language} audio for {patient_name}...")
        audio_bytes = await generate_sarvam_audio(script, language)

        # Upload to Supabase Storage
        filename = f"{pres_id}_{language}.wav"
        audio_url = await upload_audio_to_twilio(audio_bytes, filename)

        # Make Twilio call
        call = twilio_client.calls.create(
            from_=TWILIO_VOICE_NUMBER,
            to=f"+{mobile}",
            url=f"{BASE_URL}/webhook/voice/followup?pres_id={pres_id}&lang={language}&audio_url={audio_url}",
            timeout=30
        )

        # Mark call as sent
        supabase.table("prescriptions").update({
            "followup_call_sent": True,
            "followup_call_sent_at": datetime.now().isoformat()
        }).eq("id", pres_id).execute()

        print(f"✅ Follow-up call initiated to {patient_name} ({mobile}): {call.sid}")

    except Exception as e:
        print(f"❌ Error making follow-up call: {e}")
        import traceback
        traceback.print_exc()


async def send_followup_whatsapp_job():
    """
    8AM Job: Send follow-up WhatsApp to patients
    whose prescription course ended yesterday.
    """
    print("💬 Running: Follow-up WhatsApp Job")
    prescriptions = get_prescriptions_ending_today()
    print(f"Found {len(prescriptions)} prescriptions needing follow-up")

    for pres in prescriptions:
        await send_followup_whatsapp(pres)


async def make_followup_calls_job():
    """
    6PM Job: Call patients who haven't replied to follow-up WhatsApp.
    """
    print("📞 Running: Follow-up Voice Call Job")
    prescriptions = get_prescriptions_needing_call()
    print(f"Found {len(prescriptions)} patients needing follow-up call")

    for pres in prescriptions:
        await make_followup_call(pres)


async def handle_voice_followup_webhook(request: Request):
    """
    Twilio Voice webhook — plays audio and captures keypress.
    """
    params = dict(request.query_params)
    pres_id = params.get("pres_id", "")
    lang = params.get("lang", "english")
    audio_url = params.get("audio_url", "")

    response = VoiceResponse()
    gather = Gather(
        num_digits=1,
        action=f"{BASE_URL}/webhook/voice/followup-response?pres_id={pres_id}",
        method="POST",
        timeout=10
    )

    # Play the Sarvam AI generated audio
    if audio_url:
        gather.play(audio_url)
    else:
        # Fallback text
        config = LANGUAGE_CONFIG.get(lang, LANGUAGE_CONFIG["english"])
        gather.say(
            "Hello! This is a follow up call from your clinic. "
            "Press 1 if you are feeling better. Press 2 to book appointment.",
            language=config["code"]
        )

    response.append(gather)

    # If no input received
    response.say("We did not receive your input. Please reply on WhatsApp. Thank you.")

    return PlainTextResponse(str(response), media_type="application/xml")


async def handle_voice_followup_response(request: Request):
    """
    Handle patient's keypress response from follow-up call.
    """
    params = dict(request.query_params)
    pres_id = params.get("pres_id", "")
    form_data = await request.form()
    digit = form_data.get("Digits", "")

    response_map = {
        "1": "feeling_better",
        "2": "needs_appointment"
    }

    followup_response = response_map.get(digit, "no_response")

    # Save response to DB
    if pres_id:
        supabase.table("prescriptions").update({
            "followup_call_response": followup_response,
            "followup_replied": True
        }).eq("id", pres_id).execute()

    # Build voice response
    response = VoiceResponse()

    if digit == "1":
        response.say(
            "Wonderful! We are glad you are feeling better. "
            "Take care and stay healthy. Goodbye!",
            language="en-IN"
        )
        # If patient booked — notify via WhatsApp
    elif digit == "2":
        response.say(
            "We will have our team contact you shortly to book an appointment. "
            "Thank you. Goodbye!",
            language="en-IN"
        )
        # TODO: Notify receptionist to call back
    else:
        response.say(
            "Thank you for your time. Stay healthy. Goodbye!",
            language="en-IN"
        )

    print(f"✅ Follow-up call response: pres_id={pres_id}, digit={digit}, response={followup_response}")
    return PlainTextResponse(str(response), media_type="application/xml")


def save_followup_reply(mobile: str, reply_text: str):
    """
    Save WhatsApp reply from patient to prescriptions table.
    Called from whatsapp_handler when patient replies 1/2/3 to follow-up.
    """
    reply_map = {
        "1": "feeling_better",
        "2": "still_recovering",
        "3": "needs_appointment"
    }

    response = reply_map.get(reply_text.strip(), reply_text)

    # Find the latest prescription with pending follow-up
    result = supabase.table("prescriptions").select(
        "id"
    ).eq("followup_whatsapp_sent", True).eq(
        "followup_replied", False
    ).execute()

    # Match by patient
    patient_result = supabase.table("patients").select(
        "id"
    ).eq("mobile", mobile).eq("family_head_mobile", mobile).execute()

    if not patient_result.data:
        return

    patient_id = patient_result.data[0]["id"]

    pres_result = supabase.table("prescriptions").select(
        "id"
    ).eq("patient_id", patient_id).eq(
        "followup_whatsapp_sent", True
    ).eq("followup_replied", False).order(
        "created_at", desc=True
    ).limit(1).execute()

    if pres_result.data:
        pres_id = pres_result.data[0]["id"]
        supabase.table("prescriptions").update({
            "followup_replied": True,
            "followup_reply": response
        }).eq("id", pres_id).execute()
        print(f"✅ Follow-up reply saved for mobile {mobile}: {response}")
