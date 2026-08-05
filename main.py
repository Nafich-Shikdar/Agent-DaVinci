import os
import zipfile
import shutil
import logging
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# কনফিগারেশন
BOT_TOKEN = os.getenv("BOT_TOKEN", "8697502823:AAHgc1DKCMk4xFHCvwzf45PEChv9OpmE_qw")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1670991660"))
DATA_CENTER_GROUP_ID = int(os.getenv("DATA_CENTER_GROUP_ID", "-5450233775"))
GOOGLE_SCRIPT_URL = os.getenv("GOOGLE_SCRIPT_URL", "") # Google Apps Script Web App URL

# লগিং সেটআপ
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI অ্যাপ
app = FastAPI()

# অস্থায়ী ফাইল স্টোরেজ
TEMP_DIR = Path("temp_files")
TEMP_DIR.mkdir(exist_ok=True)

user_sessions = {}
bot_stats = {"total_files_zipped": 0, "active_users": set()}

def send_datacenter_log(message: str):
    """ডেটা সেন্টার গ্রুপে লাইভ নোটিফিকেশন পাঠানোর ফাংশন"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": DATA_CENTER_GROUP_ID,
            "text": f"📊 **Data Center Live Update**\n\n{message}",
            "parse_mode": "Markdown"
        }
        requests.post(url, json=payload)
    except Exception as e:
        logger.error(f"Failed to send datacenter log: {e}")

def log_to_google_sheet(data: dict):
    """গুগল শিটে ডেটা পাঠানোর ফাংশন"""
    if GOOGLE_SCRIPT_URL:
        try:
            requests.post(GOOGLE_SCRIPT_URL, json=data)
        except Exception as e:
            logger.error(f"Failed to log to Google Sheet: {e}")

# টেলিগ্রাম বট হ্যান্ডলারসমূহ
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot_stats["active_users"].add(user.id)
    
    welcome_text = (
        f"স্বাগতম, {user.first_name}!\n\n"
        "আমি আপনার ফাইল জিপ্রো বট। আপনি যত খুশি ফাইল (যেমন: ফন্ট ফাইল .ttf, .otf ইত্যাদি) একসাথে পাঠান। "
        "সব ফাইল পাঠানো শেষ হলে `/zip <zip_name>` লিখে কমান্ড দিন, আমি সবগুলো ফাইল একসাথে করে জিপ ফাইল বানিয়ে আপনাকে ফরোয়ার্ড করে দেব।"
    )
    await update.message.reply_text(welcome_text)
    send_datacenter_log(f"👤 নতুন ব্যবহারকারী শুরু করেছে: {user.first_name} (ID: `{user.id}`)")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_stats["active_users"].add(user_id)
    
    if user_id not in user_sessions:
        user_sessions[user_id] = []
        
    document = update.message.document
    file = await context.bot.get_file(document.file_id)
    
    user_folder = TEMP_DIR / str(user_id)
    user_folder.mkdir(exist_ok=True)
    
    file_path = user_folder / document.file_name
    await file.download_to_drive(custom_path=file_path)
    
    user_sessions[user_id].append(file_path)
    await update.message.reply_text(f"✅ ফাইল যুক্ত হয়েছে: `{document.file_name}`\nমোট ফাইল: {len(user_sessions[user_id])}\n\nএখন `/zip আপনার_ফাইলের_নাম` লিখে জিপ করুন।", parse_mode="Markdown")

async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("❌ আপনার কোনো ফাইল আপলোড করা নেই। প্রথমে কিছু ফাইল পাঠান।")
        return
        
    if not context.args:
        await update.message.reply_text("❌ দয়া করে জিপ ফাইলের নাম দিন। উদাহরণ: `/zip my_fonts`", parse_mode="Markdown")
        return
        
    zip_name = context.args[0]
    if not zip_name.endswith('.zip'):
        zip_name += '.zip'
        
    user_folder = TEMP_DIR / str(user_id)
    output_zip_path = user_folder / zip_name
    
    try:
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in user_sessions[user_id]:
                zipf.write(file_path, arcname=file_path.name)
                
        with open(output_zip_path, 'rb') as zip_file:
            await update.message.reply_document(document=zip_file, caption=f"🎉 আপনার জিপ ফাইল প্রস্তুত: `{zip_name}`", parse_mode="Markdown")
            
        bot_stats["total_files_zipped"] += 1
        send_datacenter_log(f"📦 জিপ ফাইল তৈরি হয়েছে!\nব্যবহারকারী ID: `{user_id}`\nফাইল নাম: `{zip_name}`")
        log_to_google_sheet({"event": "zip_created", "user_id": user_id, "zip_name": zip_name})
        
    except Exception as e:
        await update.message.reply_text(f"❌ জিপ ফাইল তৈরিতে সমস্যা হয়েছে: {e}")
    finally:
        # ক্লিনআপ
        shutil.rmtree(user_folder, ignore_errors=True)
        if user_id in user_sessions:
            del user_sessions[user_id]

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।")
        return
        
    stats_text = (
        f"⚙️ **Bot Admin Panel (Telegram)**\n\n"
        f"📦 মোট জিপ ফাইল তৈরি: {bot_stats['total_files_zipped']}\n"
        f"👥 মোট সক্রিয় ব্যবহারকারী: {len(bot_stats['active_users'])}\n\n"
        f"ওয়েব প্যানেল থেকে কন্ট্রোল করতে রেন্ডার ড্যাশবোর্ড লিংক ব্যবহার করুন।"
    )
    await update.message.reply_text(stats_text, parse_mode="Markdown")

# FastAPI রুটসমূহ (ব্রাউজার অ্যাডমিন প্যানেল)
@app.get("/", response_class=HTMLResponse)
async def web_admin_home():
    return f"""
    <html>
        <head><title>Bot Admin Dashboard</title></head>
        <body style="font-family: Arial; padding: 20px; background: #f4f4f9;">
            <h2>🚀 DaVinci Bot Management Dashboard</h2>
            <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                <p><b>Total Zipped Files:</b> {bot_stats['total_files_zipped']}</p>
                <p><b>Active Users:</b> {len(bot_stats['active_users'])}</p>
                <p><b>Bot Status:</b> <span style="color: green;">🟢 Online & Running</span></p>
                <hr>
                <h3>Broadcast Notification to Data Center</h3>
                <form action="/broadcast" method="POST">
                    <textarea name="message" rows="4" style="width: 100%; padding: 8px;" placeholder="Write notice here..."></textarea><br><br>
                    <button type="submit" style="padding: 10px 20px; background: #0088cc; color: white; border: none; border-radius: 4px; cursor: pointer;">Send to Data Center</button>
                </form>
            </div>
        </body>
    </html>
    """

@app.post("/broadcast")
async def broadcast_notice(message: str = Form(...)):
    send_datacenter_log(f"📢 **Admin Notice:**\n{message}")
    return {"status": "success", "message": "Broadcast sent to Data Center Group!"}

# অ্যাপ রান করার জন্য (Webhook অথবা Polling কনফিগারেশন)
if __name__ == "__main__":
    import uvicorn
    # লোকাল টেস্ট বা রেন্ডার প্ল্যাটফর্মের জন্য
    port = int(os.environ.get("PORT", 8080))
    
    # টেলিগ্রাম বট রান করা (ব্যাকগ্রাউন্ড থ্রেড বা আলাদা প্রসেস হিসেবে অথবা রেন্ডারের জন্য Webhook ব্যবহার করা ভালো)
    # সহজ রান সেটআপের জন্য Polling মোড:
    from threading import Thread
    def run_telegram_bot():
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("zip", zip_command))
        application.add_handler(CommandHandler("admin", admin_panel_command))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        application.run_polling()

    Thread(target=run_telegram_bot).start()
    uvicorn.run(app, host="0.0.0.0", port=port)
