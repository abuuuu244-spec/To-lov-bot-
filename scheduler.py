import html
import logging
from datetime import datetime, date, timedelta
from calendar import monthrange

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Tashkent")
except Exception:
    from datetime import timezone
    TZ = timezone(timedelta(hours=5))

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import get_students, update_student, mark_reminder, get_setting
from keyboards import reminder_payment_keyboard

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone=TZ)


def get_due_date(year: int, month: int, day: int) -> date:
    """
    31-kuni bo'lmagan oylarda oyning oxirgi kunini oladi (masalan, 28 yoki 30).
    """
    last_day = monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


async def check_payments(bot):
    """
    Har kuni o'quvchilar to'lovlarini tekshiradi va quyidagi eslatmalarni yuboradi:
    1. To'lovga 3 kun yoki undan kam qolganida (3, 2, 1 kun qolganda tayyorgarlik eslatmasi);
    2. To'lov kunida (Bugun to'lov kuni eslatmasi);
    3. To'lov muddati o'tib ketganida (Qarzdorlik eslatmalari).
    """
    try:
        now_tz = datetime.now(TZ)
        today = now_tz.date()
        students = await get_students()
        admin_user = await get_setting("admin_username") or "@Isroilova_Ozoda"

        logger.info(f"🔄 To'lovlar tekshiruvi boshlandi. Sana: {today}, O'quvchilar soni: {len(students)}")

        for user_id_str, student in students.items():
            try:
                user_id = int(user_id_str)

                # Agar to'lov so'rovi adminga yuborilgan bo'lsa va tasdiqlanishini kutayotgan bo'lsa
                if student.get("pending_payment"):
                    continue

                # To'lov muddatini aniqlash (next_payment_date mavjud bo'lsa undan foydalanish)
                due = None
                next_pay_str = student.get("next_payment_date")
                if next_pay_str:
                    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                        try:
                            due = datetime.strptime(str(next_pay_str).strip(), fmt).date()
                            break
                        except Exception:
                            pass

                if due is None:
                    payment_day = int(student.get("payment_day", 1))
                    due = get_due_date(today.year, today.month, payment_day)

                sent_reminders = student.get("sent_reminders") or []
                diff_days = (due - today).days

                # 1. Agar to'lov muddati kelgan yoki o'tib ketgan bo'lsa, 'unpaid' ga o'tkazish
                if today >= due and student.get("payment_status") == "paid":
                    await update_student(user_id, payment_status="unpaid")
                    student["payment_status"] = "unpaid"

                # 2. To'lov qilingan bo'lsa va kelgusi to'lovga hali 3 kundan ko'p vaqt bo'lsa - eslatma kerak emas
                if student.get("payment_status") == "paid" and diff_days > 3:
                    continue

                # Eslatma matnlari va kaliti
                should_send = False
                reminder_key = None
                msg_title = ""
                msg_action = ""

                # -------------------------------------------------------------
                # 1-BOSQICH: To'lovga 3 kun yoki undan kam qolganida (3, 2, 1 kun)
                # -------------------------------------------------------------
                if 0 < diff_days <= 3:
                    reminder_key = f"{due.strftime('%Y-%m')}-before"
                    if reminder_key not in sent_reminders:
                        should_send = True
                        if diff_days == 1:
                            days_text = "<b>1 kun (ertaga)</b>"
                        else:
                            days_text = f"<b>{diff_days} kun</b>"

                        msg_title = f"Navbatdagi oylik to‘lovingizga {days_text} qoldi."
                        msg_action = "To‘lovni o‘z vaqtida amalga oshirish uchun pastdagi tugmani bosing 👇"

                # -------------------------------------------------------------
                # 2-BOSQICH: Aynan to'lov kuni (bugun to'lov kuni)
                # -------------------------------------------------------------
                elif diff_days == 0:
                    reminder_key = f"{due.strftime('%Y-%m')}-due"
                    if reminder_key not in sent_reminders:
                        should_send = True
                        msg_title = "🔔 <b>Bugun sizning oylik to‘lov kuningiz keldi!</b>"
                        msg_action = "To‘lovni amalga oshirish uchun pastdagi tugmani bosing 👇"

                # -------------------------------------------------------------
                # 3-BOSQICH: To'lov muddati o'tib ketganda (qarzdorlik eslatmasi)
                # -------------------------------------------------------------
                elif diff_days < 0 and student.get("payment_status") != "paid":
                    overdue_days = abs(diff_days)

                    if 1 <= overdue_days <= 3:
                        reminder_key = f"{due.strftime('%Y-%m')}-overdue_1"
                        if reminder_key not in sent_reminders:
                            should_send = True
                            msg_title = f"⚠️ Sizning oylik to‘lov muddatingiz <b>{overdue_days} kun o‘tib ketdi</b>."
                            msg_action = "Iltimos, to‘lovni kechiktirmasdan amalga oshirishingizni so‘raymiz 👇"

                    elif overdue_days >= 5:
                        reminder_key = f"{due.strftime('%Y-%m')}-overdue_2"
                        if reminder_key not in sent_reminders:
                            should_send = True
                            msg_title = f"🚨 <b>DIQQAT! Oylik to‘lov muddati {overdue_days} kunga kechikmoqda!</b>"
                            msg_action = "Darslardan uzilib qolmaslik uchun to‘lovni zudlik bilan amalga oshiring 👇"

                # Agar eslatma jo'natilishi kerak bo'lsa
                if should_send and reminder_key:
                    student_name = html.escape(str(student.get("name") or student.get("full_name") or "O‘quvchi"))
                    subject_name = html.escape(str(student.get("subject", "")))
                    teacher_name = html.escape(str(student.get("teacher", "")))
                    due_str = due.strftime("%d.%m.%Y")
                    amount_val = student.get("amount", 0)

                    teacher_line = f"👨‍🏫 <b>Ustoz:</b> {teacher_name}\n" if teacher_name else ""

                    text = (
                        "🏛 <b>«PIRAMIDA» O‘QUV MARKAZI</b>\n\n"
                        f"Hurmatli o‘quvchi, <b>{student_name}</b>!\n"
                        f"{msg_title}\n\n"
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"📚 <b>Fan:</b> {subject_name}\n"
                        f"{teacher_line}"
                        f"💰 <b>Oylik to‘lov:</b> {amount_val:,} so‘m\n"
                        f"📅 <b>To‘lov muddati:</b> {due_str}\n"
                        "━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{msg_action}"
                    )

                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=text,
                            reply_markup=reminder_payment_keyboard(admin_user),
                            parse_mode="HTML"
                        )
                        # FAQAT xabar muvaffaqiyatli ketganidan keyin eslatma belgilandi deb yozamiz!
                        await mark_reminder(user_id, reminder_key)
                        logger.info(f"✅ Eslatma muvaffaqiyatli yuborildi: user_id={user_id}, kalit={reminder_key}")
                    except Exception as send_err:
                        logger.error(f"❌ Xabar yuborilmadi (ID: {user_id}): {send_err}")

            except Exception as item_err:
                logger.error(f"⚠️ O'quvchini qayta ishlashda xatolik (ID: {user_id_str}): {item_err}")

        logger.info("✅ To'lovlar tekshiruvi to'liq yakunlandi.")
    except Exception as general_err:
        logger.error(f"❌ check_payments umumiy xatosi: {general_err}")


def start_scheduler(bot):
    """
    To'lov eslatmalari schedulerini ishga tushirish.
    Kuniga 3 marta muntazam tekshiradi (09:00, 14:00, 19:00)
    va bot yoqilgan paytda ham darhol 10 soniyadan so'ng tekshiradi.
    """
    # 1. Har kuni soat 09:00 da (ertalabki asosiy tekshiruv)
    scheduler.add_job(
        check_payments,
        trigger="cron",
        hour=9,
        minute=0,
        args=[bot],
        id="payment_reminders_morning",
        replace_existing=True,
        misfire_grace_time=3600
    )

    # 2. Har kuni soat 14:00 da (kunduzgi tekshiruv - agar ertalab o'tkazib yuborilgan bo'lsa)
    scheduler.add_job(
        check_payments,
        trigger="cron",
        hour=14,
        minute=0,
        args=[bot],
        id="payment_reminders_afternoon",
        replace_existing=True,
        misfire_grace_time=3600
    )

    # 3. Har kuni soat 19:00 da (kechki tekshiruv)
    scheduler.add_job(
        check_payments,
        trigger="cron",
        hour=19,
        minute=0,
        args=[bot],
        id="payment_reminders_evening",
        replace_existing=True,
        misfire_grace_time=3600
    )

    # 4. Bot ishga tushishi bilan 10 soniyadan so'ng darhol tekshirish
    scheduler.add_job(
        check_payments,
        trigger="date",
        run_date=datetime.now(TZ) + timedelta(seconds=10),
        args=[bot],
        id="payment_check_startup",
        replace_existing=True
    )

    if not scheduler.running:
        scheduler.start()
        logger.info("🚀 Avtomatik to'lov eslatmalari jadvali (Scheduler) muvaffaqiyatli ishga tushdi!")
