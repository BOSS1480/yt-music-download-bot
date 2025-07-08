import os
import json
import subprocess
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from yt_dlp import YoutubeDL
from typing import Dict
import signal
import uvicorn
import requests
from web_server import app
import threading

# הגדרות
TOKEN = os.getenv('TOKEN')
AUDIO_CACHE_CHANNEL = int(os.getenv('AUDIO_CACHE_CHANNEL'))
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'audio_cache.json')

message_searches = {}
audio_cache = {}
active_downloads: Dict[int, Dict] = {}

# טעינת הקאש מקובץ JSON
def load_audio_cache():
    global audio_cache
    try:
        with open(CACHE_FILE, 'r') as f:
            audio_cache = json.load(f)
    except FileNotFoundError:
        audio_cache = {}

# שמירת הקאש לקובץ JSON
def save_audio_cache():
    with open(CACHE_FILE, 'w') as f:
        json.dump(audio_cache, f, indent=4)

# טעינה ראשונית של הקאש
load_audio_cache()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("הצטרף לקבוצה 📢", url="https://t.me/+LceT_sT3WK0xZmM0")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        'שלום! שלח לי שם של שיר ואחפש אותו ביוטיוב.',
        reply_markup=reply_markup,
        reply_to_message_id=update.message.message_id
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    message_id = update.message.message_id
    user_id = update.message.from_user.id
    
    search_message = await update.message.reply_text(
        "🔍",
        reply_to_message_id=message_id
    )
    
    ydl_opts = {
        'quiet': False,
        'no_warnings': False,
        'extract_flat': True,
        'default_search': 'ytsearch50',
        'cookies': COOKIES_FILE,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
        }
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            results = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: ydl.extract_info(f"ytsearch50:{query}", download=False)['entries']
            )
            
            if not results:
                await search_message.edit_text("לא נמצאו תוצאות לחיפוש זה.")
                return
                
            message_searches[message_id] = {
                'results': results,
                'page': 0,
                'query': query,
                'original_message_id': message_id,
                'user_id': user_id
            }
            
            await show_results_page(search_message, message_id, edit=True)
    except Exception as e:
        await search_message.edit_text(f"התרחשה שגיאה בחיפוש: {str(e)}")

async def show_results_page(message, message_id, edit=False):
    search_data = message_searches[message_id]
    results = search_data['results']
    page = search_data['page']
    original_message_id = search_data['original_message_id']
    
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(results))
    current_results = results[start_idx:end_idx]
    
    keyboard = []
    for idx, video in enumerate(current_results, start=1):
        title = video['title'][:50] + "..." if len(video['title']) > 50 else video['title']
        keyboard.append([InlineKeyboardButton(f"{idx}. {title}", 
                                            callback_data=f"download_{video['id']}_{message_id}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"prev_page_{message_id}"))
    if end_idx < len(results):
        nav_buttons.append(InlineKeyboardButton("הבא ➡️", callback_data=f"next_page_{message_id}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    total_pages = (len(results) + 9) // 10
    page_indicator = InlineKeyboardButton(f"❌ סגור הודעה || 📄 עמוד {page + 1}/{total_pages}", callback_data=f"close_{message_id}")
    keyboard.append([page_indicator])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"תוצאות שנמצאו ל: '{search_data['query']}'"
    
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.reply_text(text, 
                               reply_markup=reply_markup,
                               reply_to_message_id=original_message_id)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data_parts = query.data.split('_')
    
    if data_parts[-1].isdigit():
        message_id = int(data_parts[-1])
        if message_id in message_searches and message_searches[message_id]['user_id'] != user_id:
            await query.answer("אינך יכול להשתמש בכפתורים של חיפוש זה!", show_alert=True)
            return
    
    if query.data.startswith("close"):
        message_id = int(data_parts[1])
        if message_id in message_searches:
            del message_searches[message_id]
        await query.message.delete()
        return
    
    if query.data.startswith("next_page"):
        message_id = int(data_parts[2])
        message_searches[message_id]['page'] += 1
        await show_results_page(query.message, message_id, edit=True)
    
    elif query.data.startswith("prev_page"):
        message_id = int(data_parts[2])
        message_searches[message_id]['page'] -= 1
        await show_results_page(query.message, message_id, edit=True)
    
    elif query.data.startswith("download"):
        video_id = data_parts[1]
        message_id = int(data_parts[2])
        
        if user_id in active_downloads:
            await query.answer("יש לך הורדה פעילה כרגע. אנא המתן לסיומה או בטל אותה.", show_alert=True)
            return
            
        keyboard = [[InlineKeyboardButton("❌ ביטול הורדה", callback_data=f"cancel_{user_id}_{video_id}")]]
        await query.message.edit_text(
            "מוריד את השיר, אנא המתן...",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        download_info = {
            'video_id': video_id,
            'status_message': query.message,
            'original_message_id': message_searches[message_id]['original_message_id'],
            'query_message': query.message,
            'process': None,
            'filename': None
        }
        active_downloads[user_id] = download_info
        
        asyncio.create_task(
            download_and_send_song(query, context.bot, download_info)
        )
    
    elif query.data.startswith("cancel"):
        cancel_user_id = int(data_parts[1])
        video_id = data_parts[2]
        
        if query.from_user.id != cancel_user_id:
            await query.answer("אתה לא יכול לבטל הורדה של משתמש אחר", show_alert=True)
            return
            
        if cancel_user_id in active_downloads:
            download_info = active_downloads[cancel_user_id]
            if download_info['process']:
                try:
                    os.kill(download_info['process'].pid, signal.SIGTERM)
                except:
                    pass
            
            if download_info['filename'] and os.path.exists(download_info['filename']):
                try:
                    os.remove(download_info['filename'])
                except:
                    pass
            
            await download_info['status_message'].edit_text("ההורדה בוטלה.")
            del active_downloads[cancel_user_id]
    
    await query.answer()

async def download_and_send_song(query, bot, download_info):
    user_id = query.from_user.id
    video_id = download_info['video_id']
    status_message = download_info['status_message']
    original_message_id = download_info['original_message_id']
    
    try:
        if video_id in audio_cache:
            cached_message_id = audio_cache[video_id]
            try:
                await bot.copy_message(
                    chat_id=query.message.chat_id,
                    from_chat_id=AUDIO_CACHE_CHANNEL,
                    message_id=cached_message_id,
                    reply_to_message_id=original_message_id
                )
                await status_message.delete()
                return
            except Exception:
                del audio_cache[video_id]
                save_audio_cache()
        
        link = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': '%(title)s.%(ext)s',
            'cookies': COOKIES_FILE,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'writethumbnail': True,  # הורדת התמונה הממוזערת
            'embedthumbnail': True,  # הטמעת התמונה בקובץ
            'quiet': False,
            'no_warnings': False,
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: ydl.extract_info(link, download=True)
            )
            
            song_title = info.get('title', 'Unknown Title')
            artist = info.get('uploader', 'Unknown Artist')
            duration = info.get('duration_string', 'N/A')
            clean_title = song_title.replace('"', '')
            filename = f"{clean_title}.mp3"
            thumbnail = f"{clean_title}.jpg"  # התמונה הממוזערת שנשמרת

        if not os.path.exists(filename):
            raise Exception("הקובץ לא נוצר")

        caption = f"🎵 שם: {clean_title}\n" \
                 f"🎤 אמן: {artist}\n" \
                 f"⏱ משך: {duration}\n\n" \
                 f"Uploaded by @Music_Yt_RoBot"
        
        with open(filename, 'rb') as audio_file:
            cache_message = await bot.send_audio(
                chat_id=AUDIO_CACHE_CHANNEL,
                audio=audio_file,
                caption=caption,
                title=clean_title,
                performer=artist,
                thumbnail=open(thumbnail, 'rb') if os.path.exists(thumbnail) else None
            )
            
            audio_cache[video_id] = cache_message.message_id
            save_audio_cache()  # שמירת הקאש
            
            await bot.copy_message(
                chat_id=query.message.chat_id,
                from_chat_id=AUDIO_CACHE_CHANNEL,
                message_id=cache_message.message_id,
                reply_to_message_id=original_message_id
            )
        
        if os.path.exists(filename):
            os.remove(filename)
        if os.path.exists(thumbnail):
            os.remove(thumbnail)
        await status_message.delete()
            
    except asyncio.CancelledError:
        await status_message.edit_text("ההורדה בוטלה.")
        if download_info['filename'] and os.path.exists(download_info['filename']):
            os.remove(download_info['filename'])
            
    except Exception as e:
        await status_message.edit_text(f"התרחשה שגיאה בהורדת השיר: {str(e)}")
        
    finally:
        if user_id in active_downloads:
            del active_downloads[user_id]
        
        if download_info['filename'] and os.path.exists(download_info['filename']):
            try:
                os.remove(download_info['filename'])
            except:
                pass

def run_web_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)

def main():
    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()

    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    print("הבוט התחיל לפעול...")
    application.run_polling()

if __name__ == '__main__':
    main()
