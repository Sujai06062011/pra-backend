from datetime import datetime, date
import re
from database import (
    get_doctor_by_whatsapp, get_patient_by_mobile, get_conversation_state,
    save_conversation_state, get_queue_status, get_patient_token_today,
    check_holiday, get_booked_slots, get_next_token, create_appointment,
    get_upcoming_appointments, cancel_appointment, create_patient,
    get_family_members
)


MENU_HINT = "\n\nReply MENU for main menu or BYE to end conversation."

MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    "january": "01", "february": "02", "march": "03", "april": "04",
    "june": "06", "july": "07", "august": "08", "september": "09",
    "october": "10", "november": "11", "december": "12"
}

ALL_SLOTS = [
    "09:00", "09:15", "09:30", "09:45",
    "10:00", "10:15", "10:30", "10:45",
    "11:00", "11:30", "12:00", "12:30",
    "17:00", "17:30", "18:00", "18:30",
    "19:00", "19:30"
]


def format_time(t: str) -> str:
    """Convert 09:30 to 9:30 AM"""
    h, m = t.split(":")
    hour = int(h)
    ampm = "PM" if hour >= 12 else "AM"
    display = hour - 12 if hour > 12 else hour
    return f"{display}:{m} {ampm}"


def parse_date(text: str):
    """Parse date from natural language. Returns (parsed_date, error)"""
    lower = text.lower()
    day = None
    month = None
    year = date.today().year

    import re
    day_match = re.search(r"(\d{1,2})(st|nd|rd|th)?", lower)
    if day_match:
        day = day_match.group(1).zfill(2)

    for name, num in MONTHS.items():
        if name in lower:
            month = num
            break

    year_match = re.search(r"202[5-9]", lower)
    if year_match:
        year = year_match.group(0)

    if not day or not month:
        return None, "Could not understand date. Please try again.\n\nExample: 10 June 2026"

    return f"{year}-{month}-{day}", None


async def handle_message(from_number: str, text: str, to_number: str, media_url: str = ""):
    """
    Main message handler. Returns reply text.
    """
    text = text.strip()
    t = text.lower().strip()

    # Get doctor by WhatsApp number
    doctor = get_doctor_by_whatsapp(to_number)
    if not doctor:
        return "Sorry, this clinic is not registered. Please contact support."

    doctor_id = doctor["id"]
    doctor_name = doctor["name"]
    clinic_name = doctor["clinic_name"]
    clinic_timings = doctor.get("clinic_timings", "Mon-Sat: 9AM-1PM, 5PM-8PM")
    clinic_address = doctor.get("clinic_address", "")

    # Get patient
    patient = get_patient_by_mobile(from_number)
    is_existing = patient is not None
    patient_name = patient["name"] if patient else ""
    patient_id = patient["id"] if patient else ""

    # Get conversation state
    current_state, temp_data = get_conversation_state(from_number)

    # ── INTENT DETECTION ─────────────────────────────────────
    # State-based intents FIRST, then keyword matching
    intent = "menu"

    if current_state == "awaiting_name":
        intent = "name_provided"
    elif current_state == "awaiting_dob":
        intent = "dob_provided"
    elif current_state == "awaiting_gender":
        intent = "gender_provided"
    elif current_state == "awaiting_date":
        intent = "date_provided"
    elif current_state == "awaiting_slot":
        intent = "slot_selected"
    elif current_state == "awaiting_cancel_choice":
        intent = "cancel_choice"
    elif media_url:
        intent = "media"
    elif t in ["1"] or any(k in t for k in ["book", "appointment"]):
        intent = "book"
    elif t in ["2"] or any(k in t for k in ["queue", "status", "token", "wait"]):
        intent = "queue"
    elif t in ["3"] or "cancel" in t:
        intent = "cancel"
    elif t in ["4"] or any(k in t for k in ["timing", "hour", "open", "close"]):
        intent = "timing"
    elif t in ["5"] or any(k in t for k in ["speak", "receptionist", "staff", "call"]):
        intent = "speak"
    elif t in ["menu", "hi", "hello", "hey", "start", "help"]:
        intent = "menu"
        # ── GLOBAL COMMANDS (work from any state) ────────────
    if t in ["menu", "main menu", "0", "back", "home"]:
        save_conversation_state(from_number, "idle", {})
        return f"Hello {patient_name or ''}! Welcome back to {clinic_name}. 👋\n\n1. Book Appointment\n2. Queue Status\n3. Cancel Appointment\n4. Clinic Timings\n5. Speak to Receptionist\n\nReply with a number."
    if t in ["exit", "bye", "goodbye", "end", "quit"]:
        save_conversation_state(from_number, "idle", {})
        return f"Thank you for contacting {clinic_name}. 🙏\n\nStay healthy! Reply Hi anytime to start again."

    # ── BUILD REPLY ───────────────────────────────────────────
    reply = ""
    new_state = "idle"
    new_temp = {}

    # NEW PATIENT
    if not is_existing and current_state == "idle":
        reply = f"Welcome to {clinic_name}! 🙏\n\nWe noticed you are a new patient. Let us register you quickly.\n\nPlease reply with your Full Name."
        new_state = "awaiting_name"

    # REGISTRATION FLOW
    elif intent == "name_provided":
        reply = f"Thank you {text}! 😊\n\nPlease share your Date of Birth.\n\nExample: 14 May 1982"
        new_state = "awaiting_dob"
        new_temp = {"name": text}

    elif intent == "dob_provided":
        reply = "Got it! Please share your Gender.\n\nReply M for Male or F for Female."
        new_state = "awaiting_gender"
        new_temp = {**temp_data, "dob": text}

    elif intent == "gender_provided":
        gender = "Male" if t.startswith("m") else "Female"
        name = temp_data.get("name", "")
        dob = temp_data.get("dob", "")

        # Create patient in DB
        new_patient = create_patient(from_number, name, dob, gender)
        patient_id = new_patient["id"] if new_patient else ""

        reply = f"You are now registered at {clinic_name}! Welcome {name}! 🎉\n\nHow can we help you today?\n\n1. Book Appointment\n2. Queue Status\n3. Cancel Appointment\n4. Clinic Timings\n5. Speak to Receptionist\n\nReply with a number."
        new_state = "idle"

        # MAIN MENU
    elif intent == "menu" or (is_existing and current_state == "idle" and intent == "menu"):
        family = get_family_members(from_number)
        family_option = "\n6. Book for Family Member" if len(family) > 1 else ""
        reply = f"Hello {patient_name}! Welcome back to {clinic_name}. 👋\n\n1. Book Appointment\n2. Queue Status\n3. Cancel Appointment\n4. Clinic Timings\n5. Speak to Receptionist{family_option}\n\nReply with a number." + MENU_HINT
        new_state = "idle"

    # APPOINTMENT BOOKING
    elif intent == "book":
        reply = f"Please share your preferred date for appointment.\n\nExample: 10 June 2026"
        new_state = "awaiting_date"

    elif intent == "date_provided":
        parsed_date, error = parse_date(text)
        if error:
            reply = error
            new_state = "awaiting_date"
        else:
            date_obj = datetime.strptime(parsed_date, "%Y-%m-%d").date()

            # Check Sunday
            if date_obj.weekday() == 6:
                reply = "Sorry! Clinic is closed on Sundays. Please choose Monday to Saturday."
                new_state = "idle"
            else:
                # Check holiday
                holiday = check_holiday(doctor_id, parsed_date)
                if holiday:
                    reply = f"Sorry! Clinic is closed on {text} due to {holiday['reason']}. Please choose another date."
                    new_state = "idle"
                else:
                    # Get available slots
                    booked = get_booked_slots(doctor_id, parsed_date)
                    available = [s for s in ALL_SLOTS if s not in booked][:6]

                    if not available:
                        reply = f"Sorry! No slots available on {text}. Please try another date."
                        new_state = "idle"
                    else:
                        slot_list = f"Available slots on {text}:\n\n"
                        for i, slot in enumerate(available, 1):
                            slot_list += f"{i}. {format_time(slot)}\n"
                        slot_list += "\nReply with slot number to confirm." + MENU_HINT
                        reply = slot_list
                        new_state = "awaiting_slot"
                        new_temp = {
                            "booking_date": text,
                            "parsed_date": parsed_date,
                            "available_slots": available
                        }

    elif intent == "slot_selected":
        try:
            slot_index = int(t) - 1
            slots = temp_data.get("available_slots", [])
            selected_slot = slots[slot_index]
            parsed_date = temp_data.get("parsed_date", "")
            booking_date = temp_data.get("booking_date", "")

            # Get next token
            token = get_next_token(doctor_id, parsed_date)

            # Create appointment
            create_appointment(patient_id, doctor_id, parsed_date, selected_slot, token)

            # reply = f"Appointment Confirmed! ✅\n\nPatient: {patient_name}\nDate: {booking_date}\nTime: {format_time(selected_slot)}\nToken: #{token}\nClinic: {clinic_name}\n\nSee you soon! Reply MENU for more options."
            reply = f"Appointment Confirmed! ✅\n\nPatient: {patient_name}\nDate: {booking_date}\nTime: {format_time(selected_slot)}\nToken: #{token}\nClinic: {clinic_name}\n\nSee you soon!" + MENU_HINT

            new_state = "idle"
        except (IndexError, ValueError):
            reply = "Invalid choice. Please reply with a number from the list."
            new_state = "awaiting_slot"
            new_temp = temp_data

    # QUEUE STATUS
    elif intent == "queue":
        queue = get_queue_status(doctor_id)
        if queue:
            current = queue["current_token"]
            total = queue["total_tokens"]
            avg = queue.get("avg_minutes_per_patient", 10)

            # Check patient's token for today
            patient_token_data = get_patient_token_today(patient_id, doctor_id) if patient_id else None

            if patient_token_data:
                my_token = patient_token_data["token_number"]
                if my_token <= current:
                    wait_msg = "Your turn may have passed. Please check with reception."
                else:
                    wait = (my_token - current) * avg
                    wait_msg = f"Est. Wait: ~{wait} mins"

                    reply = (
                    f"{clinic_name} - Live Queue 🏥\n\n"
                    f"Current Token: {current}\n"
                    f"Your Token: #{my_token}\n"
                    f"{wait_msg}"
                    + MENU_HINT
        )
            else:
               reply = (
                f"{clinic_name} - Live Queue 🏥\n\n"
                f"Current Token: {current}\n"
                f"Total Today: {total}\n\n"
                f"You do not have an appointment today.\n"
                f"Reply 1 to book an appointment."
                + MENU_HINT
            )
        else:
            reply = "Queue not started yet today. Clinic opens at 9:00 AM.\n\nReply 1 to book an appointment."

    # CANCEL APPOINTMENT
    elif intent == "cancel":
        appointments = get_upcoming_appointments(patient_id, doctor_id)
        if not appointments:
            reply = "You have no upcoming appointments to cancel.\n\nReply 1 to book an appointment."
            new_state = "idle"
        else:
            apt_list = "Your upcoming appointments:\n\n"
            for i, apt in enumerate(appointments, 1):
                # Format date nicely
                # from datetime import datetime
                apt_date = datetime.strptime(
                    apt["appointment_date"], "%Y-%m-%d"
                ).strftime("%d %B %Y")
                apt_time = format_time(apt["appointment_time"][:5])
                token = apt["token_number"]
                apt_list += f"{i}. {apt_date} at {apt_time} (Token #{token})\n"
            apt_list += "\nReply with number to cancel. Reply 0 to go back."
            reply = apt_list
            new_state = "awaiting_cancel_choice"
            new_temp = {"appointments": appointments}

    elif intent == "cancel_choice":
        if t == "0":
            reply = f"Hello {patient_name}! How can we help?\n\n1. Book Appointment\n2. Queue Status\n3. Cancel Appointment\n4. Clinic Timings\n5. Speak to Receptionist"
            new_state = "idle"
        else:
            try:
                choice = int(t) - 1
                appointments = temp_data.get("appointments", [])
                apt = appointments[choice]
                cancel_appointment(apt["id"])
                apt_date = apt["appointment_date"]
                apt_time = format_time(apt["appointment_time"][:5])
                reply = f"Your appointment on {apt_date} at {apt_time} has been cancelled. ✅\n\nReply 1 to book a new appointment." + MENU_HINT
                new_state = "idle"
            except (IndexError, ValueError):
                reply = "Invalid choice. Please reply with a number from the list."
                new_state = "awaiting_cancel_choice"
                new_temp = temp_data

    # CLINIC TIMINGS
    elif intent == "timing":
        address_line = f"\nAddress: {clinic_address}" if clinic_address else ""
        reply = f"{clinic_name} Timings 🕐\n\n{clinic_timings}..." + MENU_HINT
        new_state = "idle"

    # SPEAK TO RECEPTIONIST
    elif intent == "speak":
        reply = f"Our team will contact you shortly. 📞\n\nClinic hours: {clinic_timings}\n\nAlternatively reply 1 to book online."
        new_state = "idle"

    # MEDIA / LAB REPORT
    elif intent == "media":
        reply = f"Thank you for sharing the report. 📋 {doctor_name} will review it and get back to you shortly."
        new_state = "idle"

    # DEFAULT
    else:
        reply = f"Hello {patient_name or ''}! How can we help? 😊\n\n1. Book Appointment\n2. Queue Status\n3. Cancel Appointment\n4. Clinic Timings\n5. Speak to Receptionist\n\nReply with a number."
        new_state = "idle"

    # Save conversation state
    save_conversation_state(from_number, new_state, new_temp)

    return reply
