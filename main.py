import os
import subprocess
import asyncio
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from yt_dlp import YoutubeDL
from typing import Dict
import signal
import uvicorn
from web_server import app
import threading
import requests

# נוצר ע"י @the_joker121 בטלגרם. לערוץ https://t.me/bot_sratim_sdarot
# אל תמחק את הקרדיט הזה🥹
# לבוט דוגמא חפש בטלגרם @Music_Yt_RoBot

TOKEN = os.getenv('TOKEN')
AUDIO_CACHE_CHANNEL = int(os.getenv('AUDIO_CACHE_CHANNEL'))
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')

message_searches = {}
audio_cache = {}
active_downloads: Dict[int, Dict] = {}

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
        "🔍 מחפש את השיר, אנא המתן...",
        reply_to_message_id=message_id
    )
    
    ydl_opts = {
        'quiet': False,
        'no_warnings': False,
        'extract_flat': True,
        'default_search': 'ytsearch50',
        'cookies': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
        'verbose': True,
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
            "מוריד את השיר, אנא המתן...\nהורדה: 0%\nהעלאה: 0%",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        download_info = {
            'video_id': video_id,
            'status_message': query.message,
            'original_message_id': message_searches[message_id]['original_message_id'],
            'query_message': query.message,
            'process': None,
            'filename': None,
            'download_progress': 0,
            'upload_progress': 0,
            'last_update': 0
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

async def update_progress(download_info):
    while download_info['download_progress'] < 100 or download_info['upload_progress'] < 100:
        current_time = time.time()
        if current_time - download_info['last_update'] >= 3:
            try:
                await download_info['status_message'].edit_text(
                    f"מוריד את השיר, אנא המתן...\nהורדה: {download_info['download_progress']}%\nהעלאה: {download_info['upload_progress']}%",
                    reply_markup=download_info['status_message'].reply_markup
                )
                download_info['last_update'] = current_time
            except:
                break
        await asyncio.sleep(0.5)

def progress_hook(d, download_info):
    if d['status'] == 'downloading':
        if 'total_bytes' in d and 'downloaded_bytes' in d:
            progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
            download_info['download_progress'] = min(round(progress), 100)

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
        
        link = f"https://www.youtube.com/watch?v={video_id}"
        
        # הגדרות yt-dlp להורדה ישירה של MP3
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': '%(title)s.%(ext)s',
            'cookies': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': False,
            'no_warnings': False,
            'progress_hooks': [lambda d: progress_hook(d, download_info)],
        }

        with YoutubeDL(ydl_opts) as ydl:
            download_info['last_update'] = time.time()
            asyncio.create_task(update_progress(download_info))
            
            info = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ydl.extract_info(link, download=True)
            )
            
            song_title = info.get('title', 'Unknown Title')
            duration = info.get('duration_string', 'N/A')
            thumbnail_url = info.get('thumbnail', None)
            clean_title = song_title.replace('"', '')
            filename = f"{clean_title}.mp3"
            download_info['filename'] = filename
            download_info['download_progress'] = 100
            
            if not os.path.exists(filename):
                raise Exception("הקובץ לא נוצר")

            caption = f"🎵 שם: {clean_title}\n" \
                     f"⏱ משך: {duration}\n\n" \
                     f"Uploaded by @Music_Yt_RoBot"
            
            # הורדת התמונה הממוזערת
            thumbnail_file = None
            if thumbnail_url:
                try:
                    response = requests.get(thumbnail_url)
                    if response.status_code == 200:
                        thumbnail_file = f"{clean_title}_thumb.jpg"
                        with open(thumbnail_file, 'wb') as f:
                            f.write(response.content)
                except:
                    thumbnail_file = None
            
            with open(filename, 'rb') as audio_file:
                # חישוב התקדמות ההעלאה
                file_size = os.path.getsize(filename)
                chunk_size = 1024 * 1024  # 1MB
                sent_bytes = 0
                
                async def send_with_progress():
                    nonlocal sent_bytes
                    cache_message = await bot.send_audio(
                        chat_id=AUDIO_CACHE_CHANNEL,
                        audio=audio_file,
                        caption=caption,
                        title=clean_title,
                        thumbnail=open(thumbnail_file, 'rb') if thumbnail_file else None
                    )
                    download_info['upload_progress'] = 100
                    return cache_message
                
                cache_message = await send_with_progress()
                
                audio_cache[video_id] = cache_message.message_id
                
                await bot.copy_message(
                    chat_id=query.message.chat_id,
                    from_chat_id=AUDIO_CACHE_CHANNEL,
                    message_id=cache_message.message_id,
                    reply_to_message_id=original_message_id
                )
            
            # ניקוי קבצים
            if os.path.exists(filename):
                os.remove(filename)
            if thumbnail_file and os.path.exists(thumbnail_file):
                os.remove(thumbnail_file)
            await status_message.delete()
            
    except Exception as e:
        error_message = str(e)
        if "Sign in to confirm you’re not a bot" in error_message:
            error_message = "שגיאת קוקיז: אנא ודא שקובץ הקוקיז תקין. ראה https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
        await status_message.edit_text(f"התרחשה שגיאה בהורדת השיר: {error_message}")
        
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

# נוצר ע"י @the_joker121 בטלגרם. לערוץ https://t.me/bot_sratim_sdarot
# אל תמחק את הקרדיט הזה🥹
# לבוט דוגמא חפש בטלגרם @Music_Yt_RoBot
