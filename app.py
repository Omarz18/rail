
import os
import re
import asyncio
import json
from typing import List, Tuple

import httpx
import phonenumbers
from phonenumbers import PhoneNumberFormat, carrier
import html as _html

def _best_decode(resp):
    # prefer server-declared encoding; else try utf-8; else cp1256; else iso-8859-6
    # return (text, used_encoding)
    enc_order = []
    if resp.encoding:
        enc_order.append(resp.encoding)
    enc_order += ["utf-8", "cp1256", "windows-1256", "iso-8859-6"]
    raw = resp.content
    for enc in enc_order:
        try:
            txt = raw.decode(enc, errors="replace")
            return txt, enc
        except Exception:
            continue
    return resp.text, resp.encoding or "unknown"

def _clean_text(s: str) -> str:
    s = _html.unescape(s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, CallbackQueryHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

CHOOSING, INPUT_EMAIL, INPUT_PHONE, INPUT_USER = range(4)

NEGATIVE_HINTS = [
    "not found", "doesn't exist", "page not found", "404", "sorry, this page isn't available",
    "user not found", "couldn’t find", "couldn't find", "no such user", "profile is unavailable"
]

def is_email(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", s, re.I))

def normalize_username(s: str) -> str:
    s = s.strip()
    if s.startswith("@"): s = s[1:]
    return s

def is_username(s: str) -> bool:
    s = normalize_username(s)
    return bool(re.fullmatch(r"[A-Za-z0-9_\.]{3,30}", s))

def try_parse_phone(s: str, default_region: str = "SA"):
    s = s.strip()
    try:
        num = phonenumbers.parse(s, default_region)
        if phonenumbers.is_valid_number(num):
            return num
    except Exception:
        return None
    return None

# ---- Original services (extracted from the provided Who-is-this.py) ---------
# EMAIL endpoints (13 known):
EMAIL_ENDPOINTS = [
    ("Microsoft (officeapps.live)", "GET", "https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=0&emailAddress={email}"),
    ("Twitter", "GET", "https://twitter.com/users/email_available?email={email}"),
    ("TikTok (mobile)", "GET", "https://api16-normal-c-alisg.tiktokv.com/passport/email/send_code/v1/"),
    ("Instagram (recovery)", "POST", "https://www.instagram.com/accounts/account_recovery_send_ajax/"),
    ("SoundCloud (reset)", "GET", "https://api-mobile.soundcloud.com/users/passwords/reset?client_id=Fiy8xlRI0xJNNGDLbPmGUjTpPRESPx8C&email={email}"),
    ("Noon (reset)", "POST", "https://www.noon.com/_svc/customer-v1/auth/reset_password"),
    ("ACAPS (password)", "POST", "https://www.acaps.org/user/password"),
    ("Vimeo (forgot)", "POST", "https://vimeo.com/forgot_password"),
    ("NewsAPI (reset)", "POST", "https://newsapi.org/reset-password"),
    ("NewsAPI (home)", "GET", "https://newsapi.org"),
    ("DarkwebID (login)", "GET", "https://secure.darkwebid.com/user/login"),
    ("Snapchat (accounts)", "GET", "https://accounts.snapchat.com"),
    ("Snapchat (merlin login)", "POST", "https://accounts.snapchat.com/accounts/merlin/login"),
]

async def email_check(email: str) -> List[str]:
    out = []
    timeout = httpx.Timeout(12.0, read=12.0, connect=12.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for site, method, url in EMAIL_ENDPOINTS:
            try:
                if "{email}" in url:
                    url_fmt = url.format(email=email)
                else:
                    url_fmt = url
                if site == "TikTok (mobile)":
                    # The original endpoint is a mobile API that likely needs many params & device headers.
                    out.append("⏭️ TikTok: تخطّي (تحتاج mobile params/CSRF)")
                    continue
                if method == "GET":
                    r = await client.get(url_fmt, headers={"User-Agent": "Mozilla/5.0"})
                else:
                    # Minimal body depending on endpoint
                    data = {}
                    if "instagram.com" in url_fmt:
                        data = {"email_or_username": email}
                        headers = {"X-Requested-With": "XMLHttpRequest", "User-Agent": "Mozilla/5.0"}
                        r = await client.post(url_fmt, data=data, headers=headers)
                    elif "noon.com" in url_fmt:
                        data = {"email": email}
                        r = await client.post(url_fmt, json=data, headers={"Content-Type": "application/json"})
                    elif "acaps.org" in url_fmt:
                        data = {"name": email}
                        r = await client.post(url_fmt, data=data)
                    elif "vimeo.com" in url_fmt:
                        data = {"email": email}
                        r = await client.post(url_fmt, data=data)
                    elif "newsapi.org/reset-password" in url_fmt:
                        data = {"email": email}
                        r = await client.post(url_fmt, data=data)
                    elif "snapchat.com/accounts/merlin/login" in url_fmt:
                        out.append("⏭️ Snapchat (merlin): تخطّي (تحتاج جلسة/CSRF)")
                        continue
                    else:
                        r = await client.post(url_fmt, data=data)
                # Interpret response heuristically
                status = r.status_code
                text_l = ""
                try:
                    text_l = r.text.lower()[:2000]
                except Exception:
                    text_l = ""
                verdict = None
                if "officeapps.live" in url_fmt:
                    # Microsoft returns JSON with 'IfExistsResult'
                    try:
                        j = r.json()
                        # 0 = not existing? 1/2 different providers; consider non-zero as exists
                        exists = j.get("IfExistsResult", -1) in (1,2)
                        verdict = "✅ قد يكون البريد مستخدم (Microsoft)" if exists else "❌ غير موجود (Microsoft)"
                    except Exception:
                        verdict = f"ℹ️ Microsoft: status {status}"
                elif "twitter.com/users/email_available" in url_fmt:
                    try:
                        j = r.json()
                        available = j.get("valid", False) and j.get("available", False)
                        verdict = "❌ غير مستخدم على تويتر" if available else "✅ مستخدم/مرتبط على تويتر"
                    except Exception:
                        verdict = f"ℹ️ Twitter: status {status}"
                else:
                    # Generic heuristic: 200 with no obvious "not found" might indicate email accepted
                    negative = any(h in text_l for h in ["invalid email","no account","not found","does not exist","unknown email"])
                    verdict = "✅ مستلم/محتمل مرتبط" if (status < 400 and not negative) else "❌ غير مؤكد/مرفوض"
                out.append(f"{site}: {verdict}")
            except Exception as e:
                out.append(f"{site}: ⚠️ خطأ الشبكة/الحماية ({type(e).__name__})")
    return out

# PHONE endpoint (from original):

CALLER_ID_URL = "http://caller-id.saedhamdan.com/index.php/UserManagement/search_number?number={number}&country_code={cc}"

def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s)


async def phone_check(raw: str) -> List[str]:
    num = try_parse_phone(raw, default_region="SA")
    if not num:
        return ["⚠️ رقم غير صالح. أرسل بصيغة +9665xxxxxxxx أو 05xxxxxxxx"]
    e164 = phonenumbers.format_number(num, PhoneNumberFormat.E164)
    intl = phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL)
    local = phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL)
    carr = carrier.name_for_number(num, "en") or "-"
    out = [f"📞 رقم صالح:\nE164: {e164}\nIntl: {intl}\nLocal: {local}\nCarrier: {carr}"]

    cc = phonenumbers.region_code_for_number(num) or 'SA'  # e.g., 'SA'
    nsn = str(num.national_number)  # e.g., 5XXXXXXXX
    variants = []
    variants.append(("national_no_zero", nsn.lstrip("0")))        # 5XXXXXXXX
    variants.append(("national_with_zero", ("0" + nsn) if not nsn.startswith("0") else nsn))  # 05XXXXXXXX
    variants.append(("e164_digits", re.sub(r"\D","", e164)))     # 9665XXXXXXXX

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; who-bot/1.0)",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Referer": "http://caller-id.saedhamdan.com/",
    }
    timeout = httpx.Timeout(12.0, read=12.0, connect=12.0)

    DEBUG = os.getenv("DEBUG_PHONE", "0") == "1"

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for tag, number_variant in variants:
            url = CALLER_ID_URL.format(number=number_variant, cc=cc)
            try:
                r = await client.get(url)
            except Exception as e:
                out.append(f"ℹ️ Caller-ID ({tag}): خطأ شبكة/منع ({type(e).__name__})")
                continue

            used_encoding = r.encoding or "server-default"
            txt, used_encoding = _best_decode(r)
            short = _clean_text(txt[:240])

            # JSON path
            name_val = None
            try:
                j = r.json()
                if isinstance(j, dict):
                    for k in ["name", "Name", "callerName", "caller_name", "caller"]:
                        if isinstance(j.get(k), str) and j[k].strip():
                            name_val = j[k].strip()
                            break
                    if not name_val:
                        # nested
                        for v in j.values():
                            if isinstance(v, dict):
                                for kk in ["name", "Name", "callerName", "caller_name"]:
                                    if isinstance(v.get(kk), str) and v[kk].strip():
                                        name_val = v[kk].strip()
                                        break
                            if name_val:
                                break
            except Exception:
                pass

            # HTML path
            if not name_val:
                m = re.search(r'"name"\s*:\s*"([^"]+)"', txt, flags=re.I)
                if m: name_val = m.group(1).strip()
            if not name_val:
                m2 = re.search(r"(?:الاسم|name)\s*[:\-]\s*([^\n\r<]{3,60})", txt, flags=re.I)
                if m2: name_val = m2.group(1).strip()
            if not name_val:
                m3 = re.search(r">(الاسم|name)\s*</td>\s*<td>\s*([^<]{3,60})", txt, flags=re.I)
                if m3: name_val = m3.group(2).strip()

            if name_val:
                try:
                    if "\\u" in name_val:
                        name_val = json.loads(f'"{name_val}"')
                except Exception:
                    pass
                out.append(f"👤 الاسم المحتمل: {name_val}  ({tag}, {used_encoding})")
                return out

            # Diagnostics when no name
            if DEBUG:
                out.append(f"🧪 {tag}: status={r.status_code}, enc={used_encoding}, sample='{short}'")

        out.append("❌ لم يصلني اسم من الخدمة. قد لا يتوفر اسم، أو أن الصيغة غير المدعومة.")
    return out

# USERNAME sites from Link_all.txt (exact list provided)
def load_username_sites() -> List[str]:
    try:
        with open("Link_all.txt","r",encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        return lines
    except Exception:
        return []

async def username_check(username: str) -> List[str]:
    username = normalize_username(username)
    sites = load_username_sites()
    out = []
    timeout = httpx.Timeout(10.0, read=10.0, connect=10.0)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; who-bot/1.0)"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        tasks = []
        for pat in sites:
            url = pat.format(username)
            tasks.append(_probe(client, url))
        results = await asyncio.gather(*tasks, return_exceptions=False)
    found = [u for u, ok in results if ok]
    missing = [u for u, ok in results if not ok]
    if found:
        out.append("✅ موجود في:")
        out += [f"• {u}" for u in found]
    if missing:
        out.append("\n❌ غير موجود/غير مؤكد في:")
        # just show domain names for brevity
        for u in missing:
            out.append("• " + re.sub(r"^https?://(www\\.)?","",u).split("/")[0])
    return out if out else ["لم يتم التأكد من أي منصة."]

async def _probe(client: httpx.AsyncClient, url: str) -> Tuple[str, bool]:
    try:
        r = await client.get(url)
        ok = r.status_code < 400
        text = ""
        try:
            text = r.text.lower()[:2000]
        except Exception:
            text = ""
        if any(h in text for h in NEGATIVE_HINTS):
            ok = False
        return (url, ok)
    except Exception:
        return (url, False)

# ---- Telegram bot flow ------------------------------------------------------

def main_menu():
    kb = [
        [InlineKeyboardButton("📧 فحص إيميل", callback_data="email")],
        [InlineKeyboardButton("📞 فحص رقم", callback_data="phone")],
        [InlineKeyboardButton("👤 فحص يوزر", callback_data="user")],
    ]
    return InlineKeyboardMarkup(kb)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("اختر نوع الفحص:", reply_markup=main_menu())
    return CHOOSING

async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    choice = q.data
    if choice == "email":
        await q.edit_message_text("أرسل الإيميل:")
        return INPUT_EMAIL
    elif choice == "phone":
        await q.edit_message_text("أرسل رقم الجوال (+9665xxxxxxxx أو 05xxxxxxxx):")
        return INPUT_PHONE
    else:
        await q.edit_message_text("أرسل اليوزر (مثال: @username):")
        return INPUT_USER

async def handle_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if not is_email(email):
        await update.message.reply_text("صيغة بريد غير صحيحة. حاول مرة أخرى.")
        return INPUT_EMAIL
    await update.message.reply_text("جاري الفحص…")
    res = await email_check(email)
    await update.message.reply_text("\n".join(res)[:4000], disable_web_page_preview=True)
    await update.message.reply_text("انتهى. اختر نوع فحص آخر:", reply_markup=main_menu())
    return CHOOSING

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    await update.message.reply_text("جاري الفحص…")
    res = await phone_check(raw)
    await update.message.reply_text("\n".join(res)[:4000], disable_web_page_preview=True)
    await update.message.reply_text("انتهى. اختر نوع فحص آخر:", reply_markup=main_menu())
    return CHOOSING

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uname = update.message.text.strip()
    if not is_username(uname) and not uname.startswith("@"):
        await update.message.reply_text("صيغة يوزر غير صحيحة. مثال: @example")
        return INPUT_USER
    await update.message.reply_text("جاري الفحص… قد يستغرق ثوانٍ.")
    res = await username_check(uname)
    await update.message.reply_text("\n".join(res)[:4000], disable_web_page_preview=True)
    await update.message.reply_text("انتهى. اختر نوع فحص آخر:", reply_markup=main_menu())
    return CHOOSING

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END

def build_app():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [CallbackQueryHandler(on_menu)],
            INPUT_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email)],
            INPUT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            INPUT_USER:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="whois_menu",
        persistent=False,
    )
    app.add_handler(conv)
    return app


async def on_error(update, context):
    import traceback
    tb = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    print("ERROR:", tb[:4000])

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN env var is required")
    app = build_app()
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
