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
    h, m = t.split(":")
    hour = int(h)
    ampm = "PM" if hour >= 12 else "AM"
    display = hour - 12 if hour > 12 else hour
    return f"{display}:{m} {ampm}"


def parse_date(text: str):
    lower = text.lower()
    day = None
    month = None
    year = date.today().year

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


def build_main_menu(patient_name: str, clinic_name: str) -> str:
    return (
        f"Hello {patient_name}! Welcome back to {clinic_name}. 👋\n\n"
        f"1. Book Appointment\n"
        f"2. Queue Status\n"
        f"3. Cancel Appointment\n"
        f"4. Clinic Timings\n"
        f"5. Speak to Receptionist\n"
        f"6. Book for Family Member\n"
        f"7. Ask Doctor a Question\n\n"
        f"Reply with a number."
        + MENU_HINT
    )


async def handle_message(from_number: str, text: str, to_number: str, media_url: str = ""):
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

    # ── GLOBAL COMMANDS (work from any state) ────────────────
    if t in ["menu", "main menu", "back", "home"]:
        save_conversation_state(from_number, "idle", {})
        return build_main_menu(patient_name, clinic_name)

    if t in ["bye", "goodbye", "exit", "end", "quit"]:
        save_conversation_state(from_number, "idle", {})
        return f"Thank you for contacting {clinic_name}. 🙏\n\nStay healthy! Reply Hi anytime to start again."
    
    if current_state == "idle" and t in ["1", "2", "3"]:
        from followup import save_followup_reply, has_pending_followup
        if has_pending_followup(from_number):
            save_followup_reply(from_number, t)
            responses = {
                "1": f"Wonderful! We are glad you are feeling better. 😊\n\nStay healthy!\n- {clinic_name}",
                "2": f"We hope you feel better soon. 🙏\n\nPlease rest well and follow the diet instructions.\n- {clinic_name}",
                # "3": f"We will arrange an appointment for you. Our team will contact you shortly.\n- {clinic_name}"
            }
        if t == "3":
            save_followup_reply(from_number, t)  # ← saves followup_replied=True
            save_conversation_state(from_number, "idle", {})
            return (
                f"No problem! Let us book an appointment for you. 🏥\n\n"
                + build_main_menu(patient_name, clinic_name)
            )
            save_conversation_state(from_number, "idle", {})
            return responses.get(t, "Thank you for your response!")

    # ── INTENT DETECTION ─────────────────────────────────────
    # State-based intents FIRST before keyword matching
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
    elif current_state == "awaiting_family_choice":
        intent = "family_choice"
    elif current_state == "awaiting_family_name":
        intent = "family_name_provided"
    elif current_state == "awaiting_family_dob":
        intent = "family_dob_provided"
    elif current_state == "awaiting_family_gender":
        intent = "family_gender_provided"
    elif current_state == "awaiting_query":
        intent = "query_text_provided"
    elif media_url:
        intent = "media"
    elif t == "1" or any(k in t for k in ["book", "appointment"]):
        intent = "book"
    elif t == "2" or any(k in t for k in ["queue", "status", "token", "wait"]):
        intent = "queue"
    elif t == "3" or "cancel" in t:
        intent = "cancel"
    elif t == "4" or any(k in t for k in ["timing", "hour", "open", "close"]):
        intent = "timing"
    elif t == "5" or any(k in t for k in ["speak", "receptionist", "staff"]):
        intent = "speak"
    elif t == "6" or "family" in t:
        intent = "family"
    elif t == "7" or any(k in t for k in ["ask", "question", "query"]):
        intent = "ask_question"
    elif t in ["hi", "hello", "hey", "start", "help"]:
        intent = "menu"

    # ── BUILD REPLY ───────────────────────────────────────────
    reply = ""
    new_state = "idle"
    new_temp = {}

    # ── NEW PATIENT ───────────────────────────────────────────
    if not is_existing and current_state == "idle":
        reply = (
            f"Welcome to {clinic_name}! 🙏\n\n"
            f"We noticed you are a new patient. Let us register you quickly.\n\n"
            f"Please reply with your Full Name."
        )
        new_state = "awaiting_name"

    # ── REGISTRATION FLOW ─────────────────────────────────────
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
        new_patient = create_patient(from_number, name, dob, gender,
                                       family_head_mobile=from_number)
        patient_id = new_patient["id"] if new_patient else ""
        reply = (
            f"You are now registered at {clinic_name}! Welcome {name}! 🎉\n\n"
            + build_main_menu(name, clinic_name)
        )
        new_state = "idle"

    # ── MAIN MENU ─────────────────────────────────────────────
    elif intent == "menu":
        reply = build_main_menu(patient_name, clinic_name)
        new_state = "idle"

    # ── BOOK APPOINTMENT (self) ───────────────────────────────
    elif intent == "book":
        reply = "Please share your preferred date for appointment.\n\nExample: 10 June 2026"
        new_state = "awaiting_date"
        new_temp = {"booking_for": patient_id, "booking_name": patient_name}

    # ── FAMILY FLOW ───────────────────────────────────────────
    elif intent == "family":
        family = get_family_members(from_number)
        msg = "Who is this appointment for?\n\n"
        for i, member in enumerate(family, 1):
            age_str = f", {member['age']} yrs" if member.get("age") else ""
            msg += f"{i}. {member['name']}{age_str}\n"
        msg += f"{len(family) + 1}. Add new family member\n\nReply with number." + MENU_HINT
        reply = msg
        new_state = "awaiting_family_choice"
        new_temp = {"family": family}

    elif intent == "family_choice":
        family = temp_data.get("family", [])
        try:
            choice = int(t) - 1
            if choice == len(family):
                reply = "Please enter the family member's Full Name."
                new_state = "awaiting_family_name"
                new_temp = temp_data
            elif 0 <= choice < len(family):
                selected = family[choice]
                reply = (
                    f"Booking appointment for {selected['name']}. 👍\n\n"
                    f"Please share preferred date.\n\nExample: 10 June 2026"
                )
                new_state = "awaiting_date"
                new_temp = {
                    "booking_for": selected["id"],
                    "booking_name": selected["name"]
                }
            else:
                reply = "Invalid choice. Please reply with a number from the list."
                new_state = "awaiting_family_choice"
                new_temp = temp_data
        except ValueError:
            reply = "Invalid choice. Please reply with a number from the list."
            new_state = "awaiting_family_choice"
            new_temp = temp_data

    elif intent == "family_name_provided":
        reply = f"Thank you! Please share {text}'s Date of Birth.\n\nExample: 14 May 2015"
        new_state = "awaiting_family_dob"
        new_temp = {**temp_data, "family_name": text}

    elif intent == "family_dob_provided":
        reply = "Please share Gender.\n\nReply M for Male or F for Female."
        new_state = "awaiting_family_gender"
        new_temp = {**temp_data, "family_dob": text}

    elif intent == "family_gender_provided":
        gender = "Male" if t.startswith("m") else "Female"
        name = temp_data.get("family_name", "")
        dob = temp_data.get("family_dob", "")
        new_patient = create_patient(from_number, name, dob, gender,
                                       family_head_mobile=from_number)
        reply = (
            f"{name} has been registered! 🎉\n\n"
            f"Now booking appointment for {name}.\n\n"
            f"Please share preferred date.\n\nExample: 10 June 2026"
        )
        new_state = "awaiting_date"
        new_temp = {
            "booking_for": new_patient["id"] if new_patient else "",
            "booking_name": name
        }

    # ── DATE PROVIDED ─────────────────────────────────────────
    elif intent == "date_provided":
        parsed_date, error = parse_date(text)
        booking_name = temp_data.get("booking_name", patient_name)
        booking_for = temp_data.get("booking_for", patient_id)

        if error:
            reply = error
            new_state = "awaiting_date"
            new_temp = temp_data
        else:
            date_obj = datetime.strptime(parsed_date, "%Y-%m-%d").date()
            if date_obj.weekday() == 6:
                reply = "Sorry! Clinic is closed on Sundays. Please choose Monday to Saturday."
                new_state = "idle"
            else:
                holiday = check_holiday(doctor_id, parsed_date)
                if holiday:
                    reply = f"Sorry! Clinic is closed on {text} due to {holiday['reason']}. Please choose another date."
                    new_state = "idle"
                else:
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
                            "available_slots": available,
                            "booking_for": booking_for,
                            "booking_name": booking_name
                        }

    # ── SLOT SELECTED ─────────────────────────────────────────
    elif intent == "slot_selected":
        try:
            slot_index = int(t) - 1
            slots = temp_data.get("available_slots", [])
            selected_slot = slots[slot_index]
            parsed_date = temp_data.get("parsed_date", "")
            booking_date = temp_data.get("booking_date", "")
            booking_name = temp_data.get("booking_name", patient_name)
            booking_for = temp_data.get("booking_for", patient_id)

            token = get_next_token(doctor_id, parsed_date)
            create_appointment(booking_for, doctor_id, parsed_date, selected_slot, token)

            reply = (
                f"Appointment Confirmed! ✅\n\n"
                f"Patient: {booking_name}\n"
                f"Date: {booking_date}\n"
                f"Time: {format_time(selected_slot)}\n"
                f"Token: #{token}\n"
                f"Clinic: {clinic_name}\n\n"
                f"See you soon!"
                + MENU_HINT
            )
            new_state = "idle"
        except (IndexError, ValueError):
            reply = "Invalid choice. Please reply with a number from the list."
            new_state = "awaiting_slot"
            new_temp = temp_data

    # ── QUEUE STATUS ──────────────────────────────────────────
    elif intent == "queue":
        queue = get_queue_status(doctor_id)
        if queue:
            current = queue["current_token"]
            total = queue["total_tokens"]
            avg = queue.get("avg_minutes_per_patient", 10)
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
            reply = (
                f"Queue not started yet today. Clinic opens at 9:00 AM.\n\n"
                f"Reply 1 to book an appointment."
            )

    # ── CANCEL APPOINTMENT ────────────────────────────────────
    elif intent == "cancel":
        appointments = get_upcoming_appointments(patient_id, doctor_id)
        if not appointments:
            reply = "You have no upcoming appointments to cancel.\n\nReply 1 to book an appointment."
            new_state = "idle"
        else:
            apt_list = "Your upcoming appointments:\n\n"
            for i, apt in enumerate(appointments, 1):
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
            reply = build_main_menu(patient_name, clinic_name)
            new_state = "idle"
        else:
            try:
                choice = int(t) - 1
                appointments = temp_data.get("appointments", [])
                apt = appointments[choice]
                cancel_appointment(apt["id"])
                apt_date = datetime.strptime(
                    apt["appointment_date"], "%Y-%m-%d"
                ).strftime("%d %B %Y")
                apt_time = format_time(apt["appointment_time"][:5])
                reply = (
                    f"Your appointment on {apt_date} at {apt_time} "
                    f"has been cancelled. ✅\n\n"
                    f"Reply 1 to book a new appointment."
                    + MENU_HINT
                )
                new_state = "idle"
            except (IndexError, ValueError):
                reply = "Invalid choice. Please reply with a number from the list."
                new_state = "awaiting_cancel_choice"
                new_temp = temp_data

    # ── CLINIC TIMINGS ────────────────────────────────────────
    elif intent == "timing":
        address_line = f"\nAddress: {clinic_address}" if clinic_address else ""
        reply = (
            f"{clinic_name} Timings 🕐\n\n"
            f"{clinic_timings}"
            f"{address_line}\n\n"
            f"Reply 1 for appointment."
            + MENU_HINT
        )
        new_state = "idle"

    # ── SPEAK TO RECEPTIONIST ─────────────────────────────────
    elif intent == "speak":
        reply = (
            f"Our team will contact you shortly. 📞\n\n"
            f"Clinic hours: {clinic_timings}\n\n"
            f"Alternatively reply 1 to book online."
            + MENU_HINT
        )
        new_state = "idle"

    # ── ASK DOCTOR A QUESTION ─────────────────────────────────
    elif intent == "ask_question":
        reply = (
            "Please type your question for Dr. Kumar.\n\n"
            "Our doctor will reply within a few hours. 💬"
        )
        new_state = "awaiting_query"

    elif intent == "query_text_provided":
        try:
            from database import supabase as _supa
            import datetime as _dt
            _supa.table("queries").insert({
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "question": text,
                "status": "Pending",
                "created_at": _dt.datetime.utcnow().isoformat(),
            }).execute()
        except Exception as _e:
            print(f"❌ Failed to save query: {_e}")
        reply = (
            "✅ Your question has been sent to Dr. Kumar!\n\n"
            "You'll receive a reply on WhatsApp within a few hours.\n\n"
            "Reply MENU for main menu."
        )
        new_state = "idle"

    # ── MEDIA / LAB REPORT ────────────────────────────────────
    elif intent == "media":
        reply = (
            f"Thank you for sharing the report. 📋 "
            f"{doctor_name} will review it and get back to you shortly."
            + MENU_HINT
        )
        new_state = "idle"

    # ── DEFAULT ───────────────────────────────────────────────
    else:
        reply = build_main_menu(patient_name or "", clinic_name)
        new_state = "idle"

    # Save conversation state
    save_conversation_state(from_number, new_state, new_temp)

    return reply
