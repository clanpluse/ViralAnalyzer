import os
import sys
import json

# Fix encoding on Windows so Arabic/emoji in print() don't crash the process
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import base64
import tempfile
import subprocess
import threading
import schedule
import time
import requests
from flask import Flask, request, jsonify, send_file
from anthropic import Anthropic
from datetime import datetime

app = Flask(__name__)
ANTHROPIC_API_KEY = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()


def call_claude(messages_content, max_tokens=1500):
    """Call Anthropic API directly via requests (bypasses SDK HTTP/2 issues)."""
    for attempt in range(3):
        try:
            response = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': ANTHROPIC_API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json'
                },
                json={
                    'model': 'claude-haiku-4-5-20251001',
                    'max_tokens': max_tokens,
                    'messages': [{'role': 'user', 'content': messages_content}]
                },
                timeout=30
            )
            data = response.json()
            if 'content' in data:
                return data['content'][0]['text']
            else:
                raise Exception(f"API error: {data}")
        except Exception as e:
            print(f"Claude attempt {attempt+1} failed: {e}")
            if attempt == 2:
                return None
            time.sleep(3)
    return None

# Resolve ffmpeg binary. Prefer the SYSTEM ffmpeg (nixpacks: ffmpeg-full) because it
# ships fontconfig + fonts so the drawtext filter works. static-ffmpeg is a static
# ffbuild binary WITHOUT fontconfig, so drawtext fails on it — use it only as fallback.
import shutil as _shutil
import glob as _glob


def _find_system_ffmpeg():
    """Find a system ffmpeg that is NOT the fontconfig-less static_ffmpeg build."""
    # 1. Scan PATH for any ffmpeg not under static_ffmpeg
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d, "ffmpeg")
        if os.path.isfile(cand) and "static_ffmpeg" not in cand:
            return cand
    # 2. Common system locations
    for cand in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/bin/ffmpeg"):
        if os.path.isfile(cand):
            return cand
    # 3. Nix store (nixpacks installs ffmpeg-full here)
    for cand in _glob.glob("/nix/store/*ffmpeg*/bin/ffmpeg"):
        if os.path.isfile(cand):
            return cand
    return None


_FFMPEG_BIN = _find_system_ffmpeg()
if _FFMPEG_BIN:
    print(f"Using system ffmpeg (fontconfig OK): {_FFMPEG_BIN}")
else:
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        _FFMPEG_BIN = _shutil.which("ffmpeg") or "ffmpeg"
        print(f"Falling back to static-ffmpeg (no fontconfig): {_FFMPEG_BIN}")
        subprocess.run([_FFMPEG_BIN, "-version"], capture_output=True, timeout=120)
    except Exception as e:
        _FFMPEG_BIN = "ffmpeg"
        print(f"static-ffmpeg not available: {e}")
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _setup_fontconfig():
    """The static-ffmpeg build has fontconfig compiled in but ships no config file.
    Only relevant when there is NO system fontconfig. On Debian (our Dockerfile)
    /etc/fonts/fonts.conf already exists and works with the installed fonts, so we
    must NOT override it — doing so would hide the system fonts and break drawtext."""
    if os.path.isfile("/etc/fonts/fonts.conf"):
        print("System fontconfig present (/etc/fonts/fonts.conf) — not overriding")
        return
    try:
        cache_dir = os.path.join(tempfile.gettempdir(), "fontcache")
        os.makedirs(cache_dir, exist_ok=True)
        conf_path = os.path.join(tempfile.gettempdir(), "fonts.conf")
        conf = f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>{ASSETS_DIR}</dir>
  <cachedir>{cache_dir}</cachedir>
  <match target="pattern">
    <test name="family"><string>sans-serif</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Cairo</string></edit>
  </match>
</fontconfig>
"""
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(conf)
        os.environ["FONTCONFIG_FILE"] = conf_path
        os.environ["FONTCONFIG_PATH"] = os.path.dirname(conf_path)
        print(f"fontconfig configured: {conf_path}")
    except Exception as e:
        print(f"fontconfig setup failed: {e}")


_setup_fontconfig()

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = "clanpluse/ViralAnalyzer"
UPLOAD_FOLDER = tempfile.gettempdir()

# Cache for trend data
_trends_cache = {}
_trends_loaded_at = None

# Diagnostics for last enhance call
_last_ffmpeg_diag = ""


def github_get_file(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            return content
    except Exception:
        pass
    return None


def load_trend_data(niche):
    """Load latest trend data for a niche from GitHub."""
    global _trends_cache, _trends_loaded_at

    # Refresh cache every hour
    if not _trends_cache or not _trends_loaded_at or \
       (datetime.now() - _trends_loaded_at).seconds > 3600:
        content = github_get_file('data/trends.json')
        if content:
            try:
                _trends_cache = json.loads(content)
                _trends_loaded_at = datetime.now()
                print("Trend data loaded from GitHub")
            except Exception:
                pass

    return _trends_cache.get(niche) or _trends_cache.get("عام")


def get_ffmpeg_path():
    """Get ffmpeg binary path (system ffmpeg preferred for fontconfig support)."""
    return _FFMPEG_BIN


def extract_frame(video_path, time_sec=1.5):
    """Extract a frame from video at given time."""
    frame_path = video_path + "_frame.jpg"
    try:
        ffmpeg = get_ffmpeg_path()
        subprocess.run([
            ffmpeg, "-y", "-i", video_path,
            "-ss", str(time_sec), "-vframes", "1",
            "-q:v", "2", frame_path
        ], capture_output=True, timeout=30)
        return frame_path if os.path.exists(frame_path) else None
    except Exception:
        return None


def get_ffprobe_path():
    """Derive ffprobe path from the ffmpeg binary's directory."""
    ffmpeg = get_ffmpeg_path()
    d = os.path.dirname(ffmpeg)
    cand = os.path.join(d, "ffprobe") if d else "ffprobe"
    return cand if (not d or os.path.isfile(cand)) else "ffprobe"


def get_video_duration(video_path):
    """Get video duration in seconds."""
    try:
        ffprobe = get_ffprobe_path()
        result = subprocess.run([
            ffprobe, "-v", "quiet",
            "-print_format", "json",
            "-show_format", video_path
        ], capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except Exception:
        # Fallback: use cv2 if available
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()
            return frames / fps if fps > 0 else 0
        except Exception:
            return 0


_whisper_model = None


def _get_whisper_model():
    """Load the whisper model once and reuse it (reloading per request spikes memory)."""
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            _whisper_model = whisper.load_model("tiny")
            print("Whisper model loaded (cached)")
        except Exception as e:
            print(f"Whisper load failed: {e}")
            return None
    return _whisper_model


def transcribe_audio(video_path):
    """Extract and transcribe audio from video."""
    audio_path = video_path + "_audio.mp3"
    try:
        ffmpeg = get_ffmpeg_path()
        # Extract only first 60 seconds to save time
        result = subprocess.run([
            ffmpeg, "-y", "-i", video_path,
            "-vn", "-acodec", "mp3", "-q:a", "5",
            "-t", "60",
            audio_path
        ], capture_output=True, timeout=30)

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            return None

        model = _get_whisper_model()
        if model is None:
            return None
        result = model.transcribe(audio_path, fp16=False, language="ar")
        text = result.get("text", "").strip()
        print(f"Transcript: {text[:50]}...")
        return text or None
    except Exception as e:
        print(f"Transcribe error: {e}")
        return None
    finally:
        if os.path.exists(audio_path):
            try:
                os.unlink(audio_path)
            except Exception:
                pass


def analyze_with_claude(duration, transcript, frame_base64, niche):
    """Send everything to Claude for analysis, enriched with real trend data."""

    duration_str = f"{int(duration)} ثانية" if duration > 0 else "غير معروف"
    transcript_str = transcript if transcript else "لا يوجد كلام مسموع"

    # Load real trend data
    trend_data = load_trend_data(niche)
    trend_context = ""
    if trend_data:
        trend_context = f"""
بيانات الترند الحقيقية لمجال "{niche}" (محدّثة {trend_data.get('last_updated', '')[:10]}):
- المدة المثالية للمجال: {trend_data.get('optimal_duration_seconds', 'غير محدد')} ثانية
- أنماط الـ Hook الناجحة: {', '.join(trend_data.get('hook_patterns', []))}
- نصائح المحتوى: {', '.join(trend_data.get('content_tips', []))}
- صيغة الكابشن الناجح: {trend_data.get('caption_formula', '')}
- أفضل وقت النشر: {trend_data.get('best_posting_times', '')}
- محفزات التفاعل: {', '.join(trend_data.get('engagement_triggers', []))}
- معادلة الانتشار: {trend_data.get('virality_formula', '')}
- تجنب: {', '.join(trend_data.get('avoid', []))}
- هاشتاقات رائجة فعلاً: {' '.join(['#'+h for h in trend_data.get('trending_hashtags', [])])}
"""
    else:
        trend_context = "ملاحظة: لم تُحلَّل بيانات الترند بعد، استخدم معرفتك العامة."

    content = [
        {
            "type": "text",
            "text": f"""أنت خبير متخصص في خوارزميات TikTok والمحتوى التسويقي.

حلل هذا الفيديو التسويقي بدقة وأعطني تقريراً شاملاً بالعربية.

معلومات الفيديو:
- المجال: {niche}
- المدة: {duration_str}
- النص المنطوق: {transcript_str}

{trend_context}

بناءً على بيانات الترند الحقيقية أعلاه وتحليلك للفيديو، أعطني النتائج بصيغة JSON فقط:
{{
  "score": (رقم من 0 إلى 100 بناءً على مقارنة الفيديو ببيانات الترند الحقيقية),
  "hook_rating": (تقييم الثواني الأولى: "ممتاز" أو "جيد" أو "يحتاج تحسين"),
  "duration_rating": (مقارنة مع المدة المثالية للمجال),
  "strengths": [(3 نقاط قوة محددة بناءً على بيانات الترند)],
  "improvements": [(3 تحسينات محددة مبنية على ما ينجح فعلاً في الترند)],
  "caption": (كابشن مكتوب بصيغة الكابشن الناجح في هذا المجال),
  "hashtags": (هاشتاقات من القائمة الرائجة الحقيقية + هاشتاقات إضافية مناسبة),
  "best_time": (أفضل وقت للنشر بناءً على البيانات),
  "verdict": (حكم نهائي: هل يناسب الترند الحالي أم يحتاج تعديل ولماذا),
  "trend_match": (نسبة توافق الفيديو مع الترند الحالي كنسبة مئوية)
}}

أعطني JSON فقط بدون أي نص إضافي."""
        }
    ]

    if frame_base64:
        content.insert(0, {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": frame_base64
            }
        })

    response_text = call_claude(content, max_tokens=1500)
    if not response_text:
        raise Exception("No response from Claude")
    response_text = response_text.strip()
    if "```" in response_text:
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
    return json.loads(response_text)


def run_trend_monitor():
    """Run trend monitor in background."""
    try:
        from trend_monitor import run_trend_analysis
        print("Running scheduled trend analysis...")
        run_trend_analysis()
    except Exception as e:
        print(f"Trend monitor error: {e}")


def start_scheduler():
    """Auto trend updates are DISABLED by default to protect the Apify budget.
    Trends are refreshed only via manual /run-trends. To re-enable a daily
    schedule, set env TREND_AUTO=1."""
    if (os.environ.get('TREND_AUTO') or '').strip() != '1':
        print("Trend auto-scheduler disabled (manual /run-trends only)")
        return

    schedule.every().day.at("03:00").do(run_trend_monitor)

    def run():
        while True:
            schedule.run_pending()
            time.sleep(60)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    print("Trend auto-scheduler enabled (daily 03:00)")


@app.route('/analyze', methods=['POST'])
def analyze():
    if 'video' not in request.files:
        return jsonify({"error": "لم يتم إرسال فيديو"}), 400

    video_file = request.files['video']
    niche = request.form.get('niche', 'عام')

    tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False, dir=UPLOAD_FOLDER)
    video_path = tmp.name
    tmp.close()
    video_file.save(video_path)

    try:
        # Get duration safely
        duration = 0
        try:
            duration = get_video_duration(video_path)
        except Exception as e:
            print(f"Duration error: {e}")

        # Extract frame safely
        frame_base64 = None
        try:
            frame_path = extract_frame(video_path)
            if frame_path and os.path.exists(frame_path):
                with open(frame_path, 'rb') as f:
                    frame_base64 = base64.b64encode(f.read()).decode('utf-8')
                os.unlink(frame_path)
        except Exception as e:
            print(f"Frame error: {e}")

        # Transcribe audio safely
        transcript = None
        try:
            transcript = transcribe_audio(video_path)
        except Exception as e:
            print(f"Transcribe error: {e}")

        print(f"Analyzing: niche={niche}, duration={duration}, has_frame={frame_base64 is not None}, has_transcript={transcript is not None}")

        # Analyze with Claude (always works)
        try:
            result = analyze_with_claude(duration, transcript, frame_base64, niche)
        except Exception as e:
            print(f"Claude analysis error: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"خطأ في التحليل: {str(e)}"}), 500

        result['transcript'] = transcript or "🔇 بدون كلام"
        result['duration'] = int(duration)

        # Add trend data freshness info
        trend_data = load_trend_data(niche)
        if trend_data:
            result['trend_updated'] = trend_data.get('last_updated', '')[:10]
            result['videos_analyzed'] = trend_data.get('videos_analyzed', 0)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except Exception:
                pass


def download_arabic_font():
    """Return path to bundled font, or download as fallback."""
    # Prefer the font bundled in the repo (reliable on Railway)
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "Cairo-Bold.ttf")
    if os.path.exists(bundled):
        return bundled

    font_path = os.path.join(tempfile.gettempdir(), "arabic_font.ttf")
    if os.path.exists(font_path):
        return font_path
    try:
        r = requests.get(
            "https://github.com/google/fonts/raw/main/ofl/cairo/Cairo-Bold.ttf",
            timeout=30
        )
        with open(font_path, 'wb') as f:
            f.write(r.content)
        print("✅ Arabic font downloaded")
        return font_path
    except Exception as e:
        print(f"Font download failed: {e}")
        return None


def clean_text(text, max_len=55):
    """Clean text for ffmpeg drawtext filter and HTTP headers (ASCII only)."""
    if not text:
        return ""
    # Keep only printable ASCII (32-126), replace everything else
    text = "".join(c if 32 <= ord(c) <= 126 else " " for c in text)
    text = text[:max_len]
    for ch in ["'", '"', "\\", ":", "\n", "\r"]:
        text = text.replace(ch, " ")
    return text.strip()


def enhance_video(video_path, enhancements, output_path, duration=30):
    """Apply algorithm-based text overlays and visual enhancements."""
    ffmpeg = get_ffmpeg_path()

    hook_text = clean_text(enhancements.get("hook_text", ""), 40)
    engage_text = clean_text(enhancements.get("engagement_text", ""), 40)
    cta_text = clean_text(enhancements.get("cta_text", ""), 40)

    # Calculate timing based on video duration
    hook_end = min(3.0, duration * 0.2) if duration > 0 else 3.0
    engage_start = duration * 0.4 if duration > 0 else 8.0
    engage_end = duration * 0.7 if duration > 0 else 14.0
    cta_start = (duration - 3) if duration > 3 else max(duration * 0.8, 0)

    # drawtext needs an explicit font file — no system fonts on Railway
    font_path = download_arabic_font()
    # No surrounding quotes — avfilter treats them as literal chars in the path.
    # Escape any ':' (none in our Linux path) per avfilter rules just in case.
    font_arg = f":fontfile={font_path.replace(':', r'\:')}" if font_path else ""

    filters = []
    if hook_text:
        filters.append(
            f"drawtext=text='{hook_text}'{font_arg}:x=(w-text_w)/2:y=h*0.1"
            f":fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6"
            f":enable='between(t,0,{hook_end:.1f})'"
        )
    if engage_text:
        filters.append(
            f"drawtext=text='{engage_text}'{font_arg}:x=(w-text_w)/2:y=h*0.85"
            f":fontsize=24:fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=5"
            f":enable='between(t,{engage_start:.1f},{engage_end:.1f})'"
        )
    if cta_text:
        filters.append(
            f"drawtext=text='{cta_text}'{font_arg}:x=(w-text_w)/2:y=h*0.1"
            f":fontsize=26:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=6"
            f":enable='gte(t,{cta_start:.1f})'"
        )

    global _last_ffmpeg_diag
    _last_ffmpeg_diag = f"ffmpeg={ffmpeg};font={font_path or 'NONE'};filters={len(filters)}"

    overlay_ok = False
    if filters:
        # Cap resolution to keep encode fast + low-memory on Railway (avoids 502/OOM).
        # Scale longest side to <=720 (TikTok-friendly), preserve aspect, even dims.
        scale = "scale='if(gt(iw,ih),min(720,iw),-2)':'if(gt(iw,ih),-2,min(720,ih))'"
        vf = scale + "," + ",".join(filters)
        cmd = [ffmpeg, "-y", "-i", video_path, "-vf", vf,
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
               "-threads", "2", "-max_muxing_queue_size", "1024",
               "-c:a", "copy", output_path]
        print(f"Applying text overlays...")
        result = subprocess.run(cmd, capture_output=True, timeout=110)
        print(f"FFmpeg return code: {result.returncode}")
        overlay_ok = result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if not overlay_ok:
            err = result.stderr.decode(errors='replace')
            print(f"FFmpeg error: {err[-700:]}")
            _last_ffmpeg_diag += f";err={clean_text(err[-220:], 220)}"

    _last_ffmpeg_diag += f";overlay={'yes' if overlay_ok else 'no'}"

    if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
        import shutil
        shutil.copy2(video_path, output_path)
        print("Fallback: used original video")

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def generate_algorithm_enhancements(niche, title, transcript, duration, reference=None):
    """Generate algorithm-based video enhancements using Claude's knowledge,
    optionally mimicking a user-analyzed reference (viral) video."""
    trend_data = load_trend_data(niche)
    trend_context = ""
    if trend_data:
        trend_context = f"""
Real trend data for "{niche}":
- Optimal duration: {trend_data.get('optimal_duration_seconds', 30)}s
- Successful hook patterns: {', '.join(trend_data.get('hook_patterns', [])[:3])}
- Engagement triggers: {', '.join(trend_data.get('engagement_triggers', [])[:3])}
"""

    if reference:
        trend_context += f"""

فيديو مرجعي ناجح حلّلناه في هذا المجال — **حاكِ وصفته الفائزة**:
- لماذا نجح: {reference.get('why_viral', '')}
- أسلوب الـ Hook: {reference.get('hook_style', '')}
- نصوص ظهرت عليه: {' | '.join(reference.get('onscreen_texts', [])[:5])}
- أسلوب النصوص: {reference.get('text_style', '')}
- الإيقاع: {reference.get('pacing', '')}
- الوصفة: {reference.get('summary', '')}
اجعل النصوص والتوقيت والمواضع تحاكي هذا المرجع الناجح (مع تكييفها لمحتوى فيديو المستخدم)."""

    prompt = f"""أنت خبير في خوارزميات TikTok و Instagram Reels لعام 2025.

أحدث عوامل الخوارزمية (2025):
1. نسبة إكمال المشاهدة (Completion Rate) — الأهم على الإطلاق
2. إعادة المشاهدة (Rewatch)
3. التعليقات والحفظ (Saves) أقوى من الإعجابات
4. المشاركة (Shares) تضاعف الوصول 10 أضعاف
5. أول 1.5 ثانية تحدّد بقاء المشاهد
6. تغيير النص على الشاشة كل بضع ثوانٍ يرفع الإكمال (يمنع الملل)

معلومات الفيديو:
- المجال: {niche}
- العنوان: {title or 'غير معروف'}
- الكلام المنطوق: {transcript or 'لا يوجد'}
- المدة: {duration} ثانية
{trend_context}

قرّر بنفسك — بناءً على المدة وما ينجح في الترند — **العدد الأمثل** للنصوص التي تُعرض على الفيديو
(عادة بين 2 و 5)، و**توقيت** كل نص و**موضعه**، لتحقيق أعلى إكمال وتفاعل ومشاركة.

⚠️ قاعدة صارمة: **كل النصوص بالعربية الفصحى فقط**. ممنوع تماماً أي كلمة أو حرف إنجليزي/لاتيني داخل النصوص — حتى لو كان الفيديو المرجعي بالإنجليزية، تَرجِم الفكرة والأسلوب إلى العربية ولا تنسخ كلمات إنجليزية. النصوص قصيرة وقوية، بدون رموز خاصة أو إيموجي.
- start_pct و end_pct نسبة من مدة الفيديو بين 0 و 1 (مثال: 0.0 إلى 0.15 = أول 15%).
- position إحدى: "top" أو "bottom" أو "center".
- أول نص يجب أن يكون Hook قوي يبدأ من 0.

أعِد JSON فقط بهذه الصيغة:
{{
  "overlays": [
    {{"text": "نص عربي", "start_pct": 0.0, "end_pct": 0.15, "position": "top", "purpose": "Hook"}},
    {{"text": "نص عربي", "start_pct": 0.4, "end_pct": 0.6, "position": "bottom", "purpose": "تفاعل"}},
    {{"text": "نص عربي", "start_pct": 0.85, "end_pct": 1.0, "position": "top", "purpose": "CTA"}}
  ],
  "visual_tip": "نصيحة بصرية مهمة (بالعربية)",
  "algorithm_score_boost": "كيف تساعد هذه الطبقات الخوارزمية تحديداً (بالعربية)"
}}"""

    response = call_claude(prompt, max_tokens=1400)
    if not response:
        return None

    try:
        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        # Robustly isolate the JSON object even if Claude adds prose around it
        if not text.lstrip().startswith("{"):
            s, e = text.find("{"), text.rfind("}")
            if s != -1 and e != -1:
                text = text[s:e + 1]
        return json.loads(text)
    except Exception as e:
        print(f"enhance JSON parse failed: {e}")
        return None


def _strip_latin(text):
    """Safety net: drop Latin letters so overlays stay Arabic-only, tidy spaces."""
    import re
    text = re.sub(r'[A-Za-z]+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


def _sanitize_overlays(raw):
    """Validate/clamp the overlay list coming from Claude (Arabic-only)."""
    out = []
    for o in (raw or [])[:6]:
        text = _strip_latin((o.get("text") or "").strip())
        # need at least a couple of Arabic characters to be meaningful
        if len([c for c in text if '؀' <= c <= 'ۿ']) < 2:
            continue
        try:
            sp = max(0.0, min(1.0, float(o.get("start_pct", 0))))
            ep = max(0.0, min(1.0, float(o.get("end_pct", 1))))
        except Exception:
            sp, ep = 0.0, 1.0
        if ep <= sp:
            ep = min(1.0, sp + 0.15)
        pos = o.get("position", "top")
        if pos not in ("top", "bottom", "center"):
            pos = "top"
        out.append({"text": text[:60], "start_pct": round(sp, 3),
                    "end_pct": round(ep, 3), "position": pos,
                    "purpose": (o.get("purpose") or "")[:30]})
    return out


# ---------------------------------------------------------------------------
# Reference (trend) video analysis: user supplies a TikTok/Reels URL; we resolve
# it for free via tikwm, download the mp4, sample frames, and let Claude (vision)
# read the on-screen text + figure out why it went viral. The resulting "profile"
# is then used to make the user's own videos mimic that winning style.
# ---------------------------------------------------------------------------

def tikwm_resolve(url):
    """Resolve a TikTok/Reels URL to a downloadable mp4 + metadata (free, no key)."""
    try:
        r = requests.get('https://www.tikwm.com/api/',
                         params={'url': url, 'hd': 1}, timeout=40)
        j = r.json()
        if j.get('code') == 0:
            return j.get('data')
        print(f"tikwm error: {j.get('msg')}")
    except Exception as e:
        print(f"tikwm request failed: {e}")
    return None


def _download_to_temp(mp4_url):
    path = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False, dir=UPLOAD_FOLDER).name
    try:
        with requests.get(mp4_url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        return path
    except Exception as e:
        print(f"download failed: {e}")
        return None


def _extract_frames_b64(video_path, duration, n=4):
    """Extract n evenly-spaced frames as base64 jpegs."""
    if duration <= 0:
        duration = 15
    fracs = [0.05, 0.35, 0.65, 0.95][:n]
    out = []
    for fr in fracs:
        fp = extract_frame(video_path, max(0.3, duration * fr))
        if fp and os.path.exists(fp):
            try:
                with open(fp, 'rb') as f:
                    out.append(base64.b64encode(f.read()).decode('utf-8'))
            except Exception:
                pass
            try:
                os.unlink(fp)
            except Exception:
                pass
    return out


def analyze_reference_video(url, niche):
    """Download a viral reference video and build a 'winning profile' via Claude vision."""
    data = tikwm_resolve(url)
    if not data or not data.get('play'):
        return None, "تعذّر قراءة الرابط (تأكد أنه رابط TikTok صحيح)"

    meta = {
        "title": data.get('title', ''),
        "play_count": data.get('play_count'),
        "digg_count": data.get('digg_count'),
        "comment_count": data.get('comment_count'),
        "share_count": data.get('share_count'),
        "duration": data.get('duration'),
        "author": (data.get('author') or {}).get('unique_id', ''),
    }

    video_path = _download_to_temp(data['play'])
    if not video_path:
        return None, "فشل تنزيل الفيديو المرجعي"

    try:
        duration = meta.get('duration') or int(get_video_duration(video_path))
        frames = _extract_frames_b64(video_path, duration, n=4)
        transcript = None
        try:
            transcript = transcribe_audio(video_path)
        except Exception:
            pass

        content = []
        for fb in frames:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": fb}})
        content.append({"type": "text", "text": f"""أنت خبير تحليل محتوى TikTok الفيروسي.

هذا فيديو **رائج حقيقي** في مجال "{niche}". الصور أعلاه لقطات منه بالترتيب الزمني.
بيانات الفيديو:
- العنوان/الوصف: {meta['title']}
- المشاهدات: {meta['play_count']} | إعجابات: {meta['digg_count']} | تعليقات: {meta['comment_count']} | مشاركات: {meta['share_count']}
- المدة: {duration} ثانية
- الكلام المنطوق: {transcript or 'غير متوفر'}

حلّل **لماذا نجح** هذا الفيديو. اقرأ النصوص المكتوبة على الشاشة في اللقطات، ولاحظ الأسلوب والإيقاع.
أعِد JSON عربياً فقط:
{{
  "why_viral": "أهم أسباب نجاحه (جملتان)",
  "hook_style": "كيف يمسك المشاهد في أول ثانيتين",
  "onscreen_texts": ["النصوص التي رأيتها مكتوبة على الفيديو كما هي"],
  "text_style": "وصف أسلوب النصوص (الطول، النبرة، الموضع)",
  "pacing": "وصف الإيقاع وتغيّر اللقطات",
  "caption_formula": "صيغة الكابشن/العنوان الناجح",
  "overlay_template": [
    {{"purpose": "Hook", "position": "top", "start_pct": 0.0, "end_pct": 0.15, "style_note": "..."}},
    {{"purpose": "...", "position": "...", "start_pct": 0.4, "end_pct": 0.6, "style_note": "..."}}
  ],
  "summary": "ملخص الوصفة الفائزة لتطبيقها على فيديوهات مشابهة"
}}
JSON فقط."""})

        resp = call_claude(content, max_tokens=1500)
        if not resp:
            return None, "فشل تحليل Claude"
        text = resp.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        profile = json.loads(text)
        profile['source_url'] = url
        profile['source_meta'] = meta
        profile['analyzed_at'] = datetime.now().isoformat()
        return profile, None
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, f"خطأ: {str(e)[:200]}"
    finally:
        if os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except Exception:
                pass


def save_reference_profile(niche, profile):
    try:
        from trend_monitor import github_get_file, github_update_file
        existing, sha = github_get_file('data/reference_profiles.json')
        try:
            profiles = json.loads(existing) if existing else {}
        except Exception:
            profiles = {}
        profiles[niche] = profile
        return github_update_file('data/reference_profiles.json',
                                  json.dumps(profiles, ensure_ascii=False, indent=2),
                                  sha, f"reference profile: {niche}")
    except Exception as e:
        print(f"save reference failed: {e}")
        return False


def load_reference_profile(niche):
    content = github_get_file('data/reference_profiles.json')
    if not content:
        return None
    try:
        return json.loads(content).get(niche)
    except Exception:
        return None


_ref_jobs = {}
_ref_jobs_lock = threading.Lock()


def _run_reference_job(job_id, url, niche):
    with _ref_jobs_lock:
        _ref_jobs[job_id] = {"status": "processing"}
    try:
        profile, err = analyze_reference_video(url, niche)
        if not profile:
            with _ref_jobs_lock:
                _ref_jobs[job_id] = {"status": "error", "error": err or "فشل التحليل"}
            return
        save_reference_profile(niche, profile)
        with _ref_jobs_lock:
            _ref_jobs[job_id] = {"status": "done", "profile": profile}
    except Exception as e:
        with _ref_jobs_lock:
            _ref_jobs[job_id] = {"status": "error", "error": str(e)[:200]}


@app.route('/analyze-reference', methods=['POST'])
def analyze_reference():
    """Start async analysis of a viral video URL; poll /reference-result/<job_id>."""
    url = request.form.get('url', '').strip()
    niche = request.form.get('niche', 'عام')
    if not url:
        return jsonify({"error": "أرسل رابط الفيديو"}), 400
    import uuid
    job_id = uuid.uuid4().hex
    threading.Thread(target=_run_reference_job, args=(job_id, url, niche), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "processing"}), 200


@app.route('/reference-result/<job_id>', methods=['GET'])
def reference_result(job_id):
    with _ref_jobs_lock:
        job = _ref_jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    if job["status"] == "processing":
        return jsonify({"status": "processing"}), 202
    if job["status"] == "error":
        return jsonify({"status": "error", "error": job.get("error", "")}), 500
    return jsonify({"status": "done", "profile": job.get("profile", {})})


@app.route('/enhance', methods=['POST'])
def enhance():
    """Return algorithm-based text suggestions as JSON. The video is now rendered
    ON THE DEVICE (Media3 Transformer), so the server only generates the text —
    fast, lightweight, and immune to the 502/timeout that video re-encoding caused."""
    niche = request.form.get('niche', 'عام')
    title = request.form.get('title', '')
    transcript = request.form.get('transcript', '')
    try:
        duration = int(float(request.form.get('duration', '0') or 0))
    except Exception:
        duration = 0

    reference = load_reference_profile(niche)
    enhancements = generate_algorithm_enhancements(niche, title, transcript, duration, reference) or {}
    overlays = _sanitize_overlays(enhancements.get("overlays"))

    # Fallback: a sensible default template if Claude returned nothing usable
    if not overlays:
        overlays = [
            {"text": "شاهد حتى النهاية", "start_pct": 0.0, "end_pct": 0.15,
             "position": "top", "purpose": "Hook"},
            {"text": "احفظ هذا الفيديو", "start_pct": 0.45, "end_pct": 0.65,
             "position": "bottom", "purpose": "تفاعل"},
            {"text": "شاركه مع صديق", "start_pct": 0.85, "end_pct": 1.0,
             "position": "top", "purpose": "CTA"},
        ]

    return jsonify({
        "overlays": overlays,
        "visual_tip": enhancements.get("visual_tip", ""),
        "algorithm_score_boost": enhancements.get("algorithm_score_boost", ""),
    })


@app.route('/diag-env', methods=['GET'])
def diag_env():
    """One-shot environment diagnostics for ffmpeg/font setup."""
    info = {}
    info["PATH"] = os.environ.get("PATH", "")[:1000]
    info["nix_exists"] = os.path.isdir("/nix")
    info["nix_ffmpeg_glob"] = _glob.glob("/nix/store/*ffmpeg*/bin/ffmpeg")[:10]
    info["usr_bin_ffmpeg"] = os.path.isfile("/usr/bin/ffmpeg")
    info["which_ffmpeg"] = _shutil.which("ffmpeg")
    info["resolved_ffmpeg"] = _FFMPEG_BIN
    # Check if the resolved ffmpeg supports fontconfig/freetype
    try:
        ver = subprocess.run([_FFMPEG_BIN, "-version"], capture_output=True, text=True, timeout=20)
        line = next((l for l in ver.stdout.split("\n") if "configuration" in l), "")
        info["fontconfig_in_build"] = "fontconfig" in line
        info["freetype_in_build"] = "freetype" in line or "libfreetype" in line
    except Exception as e:
        info["ffmpeg_version_error"] = str(e)
    return jsonify(info)


@app.route('/diag-draw', methods=['GET'])
def diag_draw():
    """Run drawtext in isolation on a generated source and return full stderr."""
    ffmpeg = get_ffmpeg_path()
    font_path = download_arabic_font()
    out = os.path.join(tempfile.gettempdir(), "diag_draw.mp4")
    results = {}
    variants = {
        "fontfile_noquote": f"drawtext=text=Hello:fontfile={font_path}:x=10:y=10:fontsize=24:fontcolor=white",
        "fontfile_quote": f"drawtext=text=Hello:fontfile='{font_path}':x=10:y=10:fontsize=24:fontcolor=white",
        "font_cairo": "drawtext=text=Hello:font=Cairo:x=10:y=10:fontsize=24:fontcolor=white",
    }
    for name, vf in variants.items():
        try:
            cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1",
                   "-vf", vf, "-frames:v", "1", out]
            r = subprocess.run(cmd, capture_output=True, timeout=40)
            err = r.stderr.decode(errors="replace")
            results[name] = {"rc": r.returncode,
                             "ok": r.returncode == 0 and os.path.exists(out),
                             "err": clean_text(err[-300:], 300)}
        except Exception as e:
            results[name] = {"error": str(e)}
    return jsonify({"font_path": font_path,
                    "font_exists": bool(font_path and os.path.isfile(font_path)),
                    "fontconfig_file": os.environ.get("FONTCONFIG_FILE"),
                    "variants": results})


@app.route('/trend-report', methods=['GET'])
def trend_report():
    """Get latest trend report."""
    content = github_get_file('data/trend_report.json')
    if content:
        return jsonify(json.loads(content))
    return jsonify({
        "message": "لم يتم إنشاء تقرير بعد",
        "next_run": "سيتم تحليل الترند تلقائياً"
    }), 404


@app.route('/trends', methods=['GET'])
def get_trends():
    """Return current trend data."""
    content = github_get_file('data/trends.json')
    if content:
        return jsonify(json.loads(content))
    return jsonify({"message": "لم تُحلَّل بيانات الترند بعد"}), 404


@app.route('/health', methods=['GET'])
def health():
    trend_data = load_trend_data("عام")
    return jsonify({
        "status": "ok",
        "version": "ar-font-1",
        "ffmpeg": _FFMPEG_BIN,
        "apify_configured": bool((os.environ.get('APIFY_TOKEN') or '').strip()),
        "github_configured": bool((os.environ.get('GITHUB_TOKEN') or '').strip()),
        "trends_loaded": bool(trend_data),
        "trends_updated": trend_data.get('last_updated', 'N/A')[:10] if trend_data else 'N/A'
    })


@app.route('/diag-apify', methods=['GET'])
def diag_apify():
    """Run one Apify hashtag search and report raw result for debugging."""
    tag = request.args.get('tag', 'fyp')
    token = (os.environ.get('APIFY_TOKEN') or '').strip()
    actor = os.environ.get('APIFY_ACTOR', 'clockworks~tiktok-scraper')
    if not token:
        return jsonify({"error": "APIFY_TOKEN not set"}), 400
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}"
    payload = {
        "hashtags": [tag],
        "resultsPerPage": 5,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSubtitles": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=240)
        info = {"actor": actor, "http_status": r.status_code}
        try:
            data = r.json()
        except Exception:
            return jsonify({**info, "raw_text": r.text[:500]})
        if isinstance(data, list):
            info["item_count"] = len(data)
            if data:
                first = data[0]
                info["first_item_keys"] = sorted(list(first.keys()))[:40]
                info["sample"] = {
                    "playCount": first.get("playCount"),
                    "createTimeISO": first.get("createTimeISO"),
                    "text": (first.get("text") or "")[:80],
                    "hashtags_count": len(first.get("hashtags") or []),
                }
        else:
            info["non_list_response"] = str(data)[:500]
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {str(e)[:300]}"}), 500


@app.route('/run-one', methods=['GET'])
def run_one():
    """Run trend analysis for ONE niche synchronously and report every step."""
    niche = request.args.get('niche', 'تصاميم منزلية وديكور')
    steps = {"niche": niche}
    if request.args.get('force') != '1':
        hrs = _hours_since_last_trend()
        if hrs is not None and hrs < TREND_COOLDOWN_HOURS:
            return jsonify({"status": "cooldown",
                            "message": f"آخر تحديث قبل {hrs:.1f} ساعة (الحد {TREND_COOLDOWN_HOURS}). أضف force=1 للتجاوز."}), 429
    try:
        import trend_monitor as tm
        videos, basis = tm.collect_viral_videos(niche)
        steps["videos_collected"] = len(videos)
        steps["basis"] = basis
        steps["top_views"] = [v["views"] for v in videos[:3]]
        if not videos:
            return jsonify({**steps, "stopped": "no videos"})

        patterns = tm.analyze_patterns(niche, videos, basis)
        steps["patterns_ok"] = bool(patterns)
        if not patterns:
            return jsonify({**steps, "stopped": "no patterns from Claude"})

        patterns['last_updated'] = datetime.now().isoformat()
        patterns['videos_analyzed'] = len(videos)
        patterns['basis'] = basis

        existing, _ = tm.github_get_file('data/trends.json')
        try:
            trends = json.loads(existing) if existing else {}
        except Exception:
            trends = {}
        trends[niche] = patterns
        content = json.dumps(trends, ensure_ascii=False, indent=2)
        _, sha = tm.github_get_file('data/trends.json')
        ok = tm.github_update_file('data/trends.json', content, sha, f"trends {niche}")
        steps["saved"] = ok
        steps["hook_examples"] = patterns.get("hook_text_examples")
        steps["trending_hashtags"] = patterns.get("trending_hashtags")
        return jsonify(steps)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({**steps, "error": f"{type(e).__name__}: {str(e)[:300]}"}), 500


_trend_run_state = {"running": False, "last": "", "error": ""}

# Minimum hours between trend refreshes (protects the Apify budget)
TREND_COOLDOWN_HOURS = int(os.environ.get('TREND_COOLDOWN_HOURS', '12'))


def _hours_since_last_trend():
    """Return hours since the most recent trends.json update, or None if never."""
    try:
        from trend_monitor import github_get_file
        content, _ = github_get_file('data/trends.json')
        if not content:
            return None
        data = json.loads(content)
        newest = None
        for v in data.values():
            lu = v.get('last_updated')
            if lu:
                t = datetime.fromisoformat(lu)
                newest = t if (newest is None or t > newest) else newest
        if newest is None:
            return None
        return (datetime.now() - newest).total_seconds() / 3600.0
    except Exception:
        return None


@app.route('/run-trends', methods=['GET', 'POST'])
def run_trends():
    """Trigger Apify trend analysis (POST) or read last-run status (GET)."""
    if request.method == 'GET':
        return jsonify({**_trend_run_state,
                        "hours_since_last": _hours_since_last_trend(),
                        "cooldown_hours": TREND_COOLDOWN_HOURS})

    # Budget guard: refuse if refreshed too recently (override with ?force=1)
    if request.args.get('force') != '1':
        hrs = _hours_since_last_trend()
        if hrs is not None and hrs < TREND_COOLDOWN_HOURS:
            return jsonify({
                "status": "cooldown",
                "message": f"آخر تحديث قبل {hrs:.1f} ساعة. الحد الأدنى {TREND_COOLDOWN_HOURS} ساعة. أضف force=1 للتجاوز.",
            }), 429

    if _trend_run_state["running"]:
        return jsonify({"status": "already_running"}), 202

    def _job():
        _trend_run_state["running"] = True
        _trend_run_state["error"] = ""
        try:
            from trend_monitor import run_trend_analysis
            run_trend_analysis()
            _trend_run_state["last"] = datetime.now().isoformat()
        except Exception as e:
            import traceback
            traceback.print_exc()
            _trend_run_state["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            _trend_run_state["running"] = False

    threading.Thread(target=_job, daemon=True).start()
    return jsonify({"status": "started"}), 200


@app.route('/test-claude', methods=['GET'])
def test_claude():
    """Test Anthropic API connectivity."""
    results = {}

    # Test 1: GET to Anthropic
    try:
        r = requests.get("https://api.anthropic.com", timeout=10)
        results["get_anthropic"] = f"HTTP {r.status_code}"
    except Exception as e:
        results["get_anthropic"] = f"Failed: {type(e).__name__}: {e}"

    # Test 2: POST to httpbin (test if POST works at all)
    try:
        r = requests.post("https://httpbin.org/post", json={"test": "ok"}, timeout=10)
        results["post_httpbin"] = f"HTTP {r.status_code}"
    except Exception as e:
        results["post_httpbin"] = f"Failed: {type(e).__name__}: {e}"

    # Test 3: POST to Anthropic
    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY[:10] + '...' if ANTHROPIC_API_KEY else 'None',
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 5,
                  'messages': [{'role': 'user', 'content': 'hi'}]},
            timeout=15
        )
        results["post_anthropic"] = f"HTTP {r.status_code}: {r.text[:100]}"
    except Exception as e:
        results["post_anthropic"] = f"Failed: {type(e).__name__}: {str(e)[:200]}"

    # Test 4: API key check
    results["api_key_set"] = bool(ANTHROPIC_API_KEY) and len(ANTHROPIC_API_KEY) > 10

    return jsonify(results)


if __name__ == '__main__':
    start_scheduler()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
