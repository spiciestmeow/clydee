import os
import asyncio
import aiohttp
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
import pytz
from telegram.ext import Application, CommandHandler, ContextTypes

# ═══════════════════════════════════════════════
# CONFIG — loaded from .env file
# ═══════════════════════════════════════════════
load_dotenv()

BOT_TOKEN         = os.getenv("BOT_TOKEN")
PAGE_ID           = os.getenv("PAGE_ID")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
ADMIN_ID          = int(os.getenv("ADMIN_ID"))
SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY")

# ═══════════════════════════════════════════════
# SUPABASE CLIENT
# ═══════════════════════════════════════════════
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ═══════════════════════════════════════════════
# DATABASE HELPERS — replaces JSON files
# ═══════════════════════════════════════════════

# ── PROGRESS ──
def load_progress():
    try:
        res = supabase.table("clydee_progress").select("*").eq("id", 1).single().execute()
        return res.data or {"last_index": 0, "total": 0}
    except:
        return {"last_index": 0, "total": 0}

def save_progress(index, total):
    supabase.table("clydee_progress").upsert({
        "id": 1,
        "last_index": index,
        "total": total,
        "updated_at": datetime.utcnow().isoformat()
    }).execute()

def clear_progress():
    supabase.table("clydee_progress").upsert({
        "id": 1,
        "last_index": 0,
        "total": 0,
        "updated_at": datetime.utcnow().isoformat()
    }).execute()

# ── SENT POSTS ──
def load_sent_ids():
    res = supabase.table("clydee_sent_posts").select("post_id").execute()
    return set(row["post_id"] for row in res.data) if res.data else set()

def save_sent_id(post_id):
    supabase.table("clydee_sent_posts").upsert({
        "post_id": post_id,
        "sent_at": datetime.utcnow().isoformat()
    }).execute()

def clear_sent_ids():
    supabase.table("clydee_sent_posts").delete().neq("post_id", "").execute()

def count_sent_ids():
    res = supabase.table("clydee_sent_posts").select("post_id", count="exact").execute()
    return res.count or 0

# ═══════════════════════════════════════════════
# FETCH ALL POSTS FROM FACEBOOK PAGE
# ═══════════════════════════════════════════════
async def fetch_all_posts(msg=None):
    all_posts = []
    page_num  = 0
    url = (
        f"https://graph.facebook.com/v19.0/{PAGE_ID}/posts"
        f"?fields=id,message,story,created_time,full_picture,attachments"
        f"&limit=100"
        f"&access_token={PAGE_ACCESS_TOKEN}"
    )

    async with aiohttp.ClientSession() as session:
        while url:
            for attempt in range(3):
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        data = await resp.json()
                    break
                except Exception as e:
                    if attempt == 2:
                        raise Exception(f"Connection failed after 3 attempts: {e}")
                    await asyncio.sleep(5)

            if "error" in data:
                raise Exception(f"Facebook API Error: {data['error']['message']}")

            posts = data.get("data", [])
            all_posts.extend(posts)
            page_num += 1

            # ── Live progress update ──
            if msg:
                fetched = len(all_posts)
                bar = "█" * page_num + "░" * max(0, 10 - page_num)
                await msg.edit_text(
                    f"⏳ <b>Fetching posts from Facebook...</b>\n\n"
                    f"[{bar}]\n"
                    f"📦 Pages fetched : <code>{page_num}</code>\n"
                    f"📝 Posts so far  : <code>{fetched}</code>",
                    parse_mode="HTML"
                )

            paging = data.get("paging", {})
            url = paging.get("next")

    all_posts.reverse()
    return all_posts

# ═══════════════════════════════════════════════
# FORMAT & SEND POST
# ═══════════════════════════════════════════════
def format_post(post, index, total):
    created = post.get("created_time", "")
    if created:
        dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S%z")
        date_str = dt.strftime("%B %d, %Y %I:%M %p")
    else:
        date_str = "N/A"

    message = post.get("message") or post.get("story") or "No caption"

    return (
        f"📌 <b>POST #{index} of {total}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗓️ <b>Date:</b> {date_str}\n\n"
        f"📝 <b>Caption:</b>\n{message}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )

def get_post_url(post):
    post_id = post.get("id", "")
    if "_" in post_id:
        parts = post_id.split("_")
        return f"https://www.facebook.com/{parts[0]}/posts/{parts[1]}"
    return f"https://www.facebook.com/{PAGE_ID}"

def get_post_image(post):
    return post.get("full_picture")

async def send_post(context, chat_id, post, index, total):
    caption   = format_post(post, index, total)
    post_url  = get_post_url(post)
    image_url = get_post_image(post)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 View Post on Facebook", url=post_url)]
    ])

    try:
        if image_url:
            await context.bot.send_photo(
                chat_id=chat_id, photo=image_url,
                caption=caption, parse_mode="HTML", reply_markup=markup
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=caption,
                parse_mode="HTML", reply_markup=markup
            )
    except Exception:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"{caption}\n\n⚠️ <i>Image failed to load</i>",
                parse_mode="HTML", reply_markup=markup
            )
        except Exception as e2:
            print(f"[ERROR] Failed to send post #{index}: {e2}")

# ═══════════════════════════════════════════════
# TOKEN STATUS
# ═══════════════════════════════════════════════
async def check_token_status():
    url = (
        f"https://graph.facebook.com/debug_token"
        f"?input_token={PAGE_ACCESS_TOKEN}"
        f"&access_token={PAGE_ACCESS_TOKEN}"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
    return data.get("data", {})

async def token_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    msg = await update.message.reply_text("🔍 <b>Checking token status...</b>", parse_mode="HTML")

    try:
        info       = await check_token_status()
        is_valid   = info.get("is_valid", False)
        expires_at = info.get("expires_at", 0)
        app_name   = info.get("application", "Unknown App")
        scopes     = info.get("scopes", [])

        if expires_at == 0:
            expiry_str = "♾️ <b>Never</b> (Permanent token)"
        else:
            expire_dt = datetime.fromtimestamp(expires_at)
            days_left = (expire_dt - datetime.now()).days
            expiry_str = f"📅 {expire_dt.strftime('%B %d, %Y %I:%M %p')}"
            if days_left <= 0:
                expiry_str += "\n⛔ <b>Already expired!</b>"
            elif days_left <= 7:
                expiry_str += f"\n⚠️ Expiring in <b>{days_left} day(s)!</b>"
            else:
                expiry_str += f"\n✅ {days_left} days remaining"

        scope_str   = ", ".join(scopes) if scopes else "None"
        status_icon = "✅ Valid" if is_valid else "❌ Invalid / Expired"

        await msg.edit_text(
            f"🔑 <b>Token Status</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Status     : <b>{status_icon}</b>\n"
            f"📱 App        : <code>{app_name}</code>\n"
            f"⏳ Expires    : {expiry_str}\n"
            f"🔐 Permissions: <code>{scope_str}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + ("✅ Token is good to use!" if is_valid else
               "❌ Token is invalid! Please update your PAGE_ACCESS_TOKEN."),
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Failed to check token</b>\n\nError: <code>{str(e)[:200]}</code>",
            parse_mode="HTML"
        )

# ═══════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Facebook Page Post Bot</b>\n\n"
        "📋 <b>Commands:</b>\n"
        "/getposts    — Send only NEW posts (skips already sent)\n"
        "/resume      — Resume from where it stopped\n"
        "/status      — Check current progress\n"
        "/tokenstatus — Check Facebook token validity & expiry\n"
        "/stop        — Stop sending posts\n"
        "/reset       — Reset in-progress session only\n"
        "/resetall    — ⚠️ Forget ALL sent posts (start completely fresh)\n\n"
        "⚠️ <i>Only the admin can use these commands.</i>",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    progress   = load_progress()
    last       = progress.get("last_index", 0)
    total      = progress.get("total", 0)
    sent_count = count_sent_ids()

    if total == 0:
        await update.message.reply_text(
            f"📊 No active session.\n"
            f"🗂️ Total posts ever sent: <code>{sent_count}</code>\n\n"
            f"Use /getposts to fetch new posts.",
            parse_mode="HTML"
        )
        return

    pct = int((last / total) * 100) if total else 0
    await update.message.reply_text(
        f"📊 <b>Progress</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Sent this session : <code>{last}</code> / <code>{total}</code>\n"
        f"📈 Progress          : <code>{pct}%</code>\n"
        f"⏳ Remaining         : <code>{total - last}</code> posts\n"
        f"🗂️ All-time sent IDs : <code>{sent_count}</code>",
        parse_mode="HTML"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    clear_progress()
    await update.message.reply_text(
        "🔄 Session progress reset.\n"
        "Sent post history is <b>kept</b> — /getposts will still skip old posts.\n\n"
        "Use /resetall if you want to start completely fresh.",
        parse_mode="HTML"
    )

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    clear_progress()
    clear_sent_ids()
    await update.message.reply_text(
        "⚠️ <b>Full reset done.</b>\n"
        "All sent post history wiped from Supabase.\n"
        "/getposts will now send every post from the beginning.",
        parse_mode="HTML"
    )

async def stop_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    context.bot_data["stop"] = True
    await update.message.reply_text("⏹️ Stopping after current post...\nUse /resume to continue later.")

async def resume_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    progress = load_progress()
    last     = progress.get("last_index", 0)
    total    = progress.get("total", 0)

    if last == 0 or total == 0:
        await update.message.reply_text("⚠️ No saved progress. Use /getposts to start.")
        return

    await update.message.reply_text(
        f"▶️ Resuming from post #{last + 1} of {total}...\nSend /stop anytime to pause."
    )
    await _send_all_posts(update, context, start_from=last)

async def get_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    context.bot_data["stop"] = False

    msg = await update.message.reply_text(
        "⏳ <b>Fetching posts from Facebook...</b>\n\n"
        "[░░░░░░░░░░]\n"
        "📦 Pages fetched : <code>0</code>\n"
        "📝 Posts so far  : <code>0</code>",
        parse_mode="HTML"
    )

    try:
        all_posts = await fetch_all_posts(msg=msg)  # ← pass msg here
        sent_ids  = load_sent_ids()

        new_posts = [p for p in all_posts if p["id"] not in sent_ids]
        total_new = len(new_posts)
        skipped   = len(all_posts) - total_new

        context.bot_data["posts"] = new_posts

        if total_new == 0:
            await msg.edit_text(
                f"✅ <b>No new posts to send!</b>\n\n"
                f"📦 Facebook has <code>{len(all_posts)}</code> posts total.\n"
                f"⏭️ All <code>{skipped}</code> have already been sent.\n\n"
                f"Use /resetall if you want to resend everything.",
                parse_mode="HTML"
            )
            return

        await msg.edit_text(
            f"✅ <b>Fetched {len(all_posts)} posts!</b>\n"
            f"🆕 New to send   : <code>{total_new}</code>\n"
            f"⏭️ Already sent  : <code>{skipped}</code>\n\n"
            f"📤 Sending oldest to newest...\nSend /stop anytime to pause.",
            parse_mode="HTML"
        )

        save_progress(0, total_new)
        await _send_all_posts(update, context, start_from=0)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Failed to fetch posts</b>\n\n"
            f"⚠️ Error: <code>{str(e)[:200]}</code>\n\n"
            f"Make sure your Page Access Token is valid.",
            parse_mode="HTML"
        )

async def _send_all_posts(update: Update, context: ContextTypes.DEFAULT_TYPE, start_from: int):
    chat_id = update.effective_chat.id
    posts   = context.bot_data.get("posts", [])

    if not posts:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ No posts to send.")
        return

    total = len(posts)
    save_progress(start_from, total)

    for i in range(start_from, total):
        if context.bot_data.get("stop"):
            save_progress(i, total)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏹️ <b>Stopped at post #{i + 1} of {total}</b>\nUse /resume to continue.",
                parse_mode="HTML"
            )
            return

        post  = posts[i]
        index = i + 1

        await send_post(context, chat_id, post, index, total)
        save_sent_id(post["id"])       # ← saved to Supabase
        save_progress(index, total)    # ← saved to Supabase

        await asyncio.sleep(1.5)

    clear_progress()
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 <b>All {total} new post(s) sent!</b>\n\n"
             f"✅ Next time you run /getposts, only brand-new posts will be sent.",
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════
# AUTO DAILY FETCH — runs at 11:59 PM PH time
# ═══════════════════════════════════════════════
async def auto_get_posts(context: ContextTypes.DEFAULT_TYPE):
    """Automatically fetch and send new posts every night."""
    chat_id = ADMIN_ID  # sends to admin's chat

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🌙 <b>Nightly Auto-Fetch Started</b>\n\n"
             "[░░░░░░░░░░]\n"
             "📦 Pages fetched : <code>0</code>\n"
             "📝 Posts so far  : <code>0</code>",
        parse_mode="HTML"
    )

    try:
        all_posts = await fetch_all_posts(msg=msg)
        sent_ids  = load_sent_ids()

        new_posts = [p for p in all_posts if p["id"] not in sent_ids]
        total_new = len(new_posts)
        skipped   = len(all_posts) - total_new

        context.bot_data["posts"]  = new_posts
        context.bot_data["stop"]   = False

        if total_new == 0:
            await msg.edit_text(
                f"🌙 <b>Nightly Auto-Fetch Done</b>\n\n"
                f"✅ No new posts tonight.\n"
                f"📦 Total posts: <code>{len(all_posts)}</code>\n"
                f"⏭️ All <code>{skipped}</code> already sent.",
                parse_mode="HTML"
            )
            return

        await msg.edit_text(
            f"🌙 <b>Nightly Auto-Fetch</b>\n\n"
            f"✅ Found <code>{total_new}</code> new post(s)!\n"
            f"⏭️ Skipping <code>{skipped}</code> already sent.\n\n"
            f"📤 Sending now...",
            parse_mode="HTML"
        )

        save_progress(0, total_new)

        # Send all new posts
        total = len(new_posts)
        for i in range(total):
            if context.bot_data.get("stop"):
                save_progress(i, total)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏹️ Auto-fetch stopped at #{i + 1} of {total}.",
                    parse_mode="HTML"
                )
                return

            post = new_posts[i]
            await send_post(context, chat_id, post, i + 1, total)
            save_sent_id(post["id"])
            save_progress(i + 1, total)
            await asyncio.sleep(1.5)

        clear_progress()
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎉 <b>Nightly Auto-Fetch Complete!</b>\n\n"
                 f"✅ Sent <code>{total_new}</code> new post(s).\n"
                 f"🕛 Next run: tomorrow at 11:59 PM PH time.",
            parse_mode="HTML"
        )

    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ <b>Nightly Auto-Fetch Failed</b>\n\n"
                 f"⚠️ Error: <code>{str(e)[:200]}</code>",
            parse_mode="HTML"
        )

# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Schedule auto fetch at 11:59 PM PH time (UTC+8) ──
    ph_tz = pytz.timezone("Asia/Manila")
    app.job_queue.run_daily(
        auto_get_posts,
        time=datetime.now(ph_tz).replace(hour=23, minute=59, second=0).timetz()
    )

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("getposts",    get_posts))
    app.add_handler(CommandHandler("resume",      resume_posts))
    app.add_handler(CommandHandler("status",      status))
    app.add_handler(CommandHandler("tokenstatus", token_status))
    app.add_handler(CommandHandler("stop",        stop_posts))
    app.add_handler(CommandHandler("reset",       reset))
    app.add_handler(CommandHandler("resetall",    reset_all))

    print("🤖 Bot started! Auto-fetch scheduled at 11:59 PM PH time.")
    app.run_polling()

if __name__ == "__main__":
    main()
