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
    """Start background scheduler for daily trend updates."""
    schedule.every().day.at("03:00").do(run_trend_monitor)
    schedule.every().day.at("15:00").do(run_trend_monitor)

    def run():
        # Run once at startup after 2 minutes
        time.sleep(120)
        run_trend_monitor()
        while True:
            schedule.run_pending()
            time.sleep(60)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    print("Trend monitor scheduler started (runs at 3AM and 3PM daily)")


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


def generate_algorithm_enhancements(niche, title, transcript, duration):
    """Generate algorithm-based video enhancements using Claude's knowledge."""
    trend_data = load_trend_data(niche)
    trend_context = ""
    if trend_data:
        trend_context = f"""
Real trend data for "{niche}":
- Optimal duration: {trend_data.get('optimal_duration_seconds', 30)}s
- Successful hook patterns: {', '.join(trend_data.get('hook_patterns', [])[:3])}
- Engagement triggers: {', '.join(trend_data.get('engagement_triggers', [])[:3])}
"""

    prompt = f"""You are a TikTok algorithm expert (2024-2025).

TikTok algorithm factors:
1. Completion Rate - most important
2. Rewatch Rate
3. Comments & Saves stronger than likes
4. Shares multiply reach 10x
5. First 1.5 seconds determine if viewer stays

Video info:
- Niche: {niche}
- Title: {title or 'unknown'}
- Speech: {transcript or 'no speech'}
- Duration: {duration}s
{trend_context}

Generate ENGLISH text overlays for the video (must be simple ASCII, no special chars).
Return JSON only:
{{
  "hook_text": "Short hook text for first 3 seconds (max 6 words, starts with number/question/challenge)",
  "hook_reason": "Why this hook boosts completion rate",
  "engagement_text": "Middle text to trigger comments/saves (max 5 words)",
  "engagement_reason": "Why this boosts engagement",
  "cta_text": "End CTA to boost shares (max 4 words)",
  "visual_tip": "Key visual tip for this video",
  "algorithm_score_boost": "How these improvements help the algorithm specifically"
}}"""

    response = call_claude(prompt, max_tokens=600)
    if not response:
        return None

    try:
        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception:
        return None


@app.route('/enhance', methods=['POST'])
def enhance():
    """Enhance video with text overlays based on trend analysis."""
    if 'video' not in request.files:
        return jsonify({"error": "لم يتم إرسال فيديو"}), 400

    video_file = request.files['video']
    niche = request.form.get('niche', 'عام')
    hook_text = request.form.get('hook_text', '')
    caption_text = request.form.get('caption_text', '')
    title = request.form.get('title', '')
    transcript = request.form.get('transcript', '')

    # Save input video
    tmp_in = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False, dir=UPLOAD_FOLDER)
    video_path = tmp_in.name
    tmp_in.close()
    video_file.save(video_path)

    output_path = video_path + "_enhanced.mp4"

    try:
        # Get video duration
        duration = get_video_duration(video_path)

        # Generate algorithm-based enhancements
        print("Generating algorithm-based enhancements...")
        enhancements = generate_algorithm_enhancements(niche, title, transcript, int(duration))

        if not enhancements:
            enhancements = {
                "hook_text": "Watch till the end!",
                "engagement_text": "Save this video",
                "cta_text": "Share with a friend"
            }

        print(f"Hook: {enhancements.get('hook_text')}")
        print(f"Engagement: {enhancements.get('engagement_text')}")
        print(f"CTA: {enhancements.get('cta_text')}")

        # Apply enhancements to video
        print("Applying enhancements to video...")
        success = enhance_video(video_path, enhancements, output_path, duration)

        if success:
            # Return video + enhancement report as headers
            response = send_file(
                output_path,
                mimetype='video/mp4',
                as_attachment=True,
                download_name='enhanced_video.mp4'
            )
            # Add enhancement info to response headers
            response.headers['X-Hook-Text'] = clean_text(enhancements.get('hook_text', ''), 80)
            response.headers['X-Hook-Reason'] = clean_text(enhancements.get('hook_reason', ''), 80)
            response.headers['X-Engage-Text'] = clean_text(enhancements.get('engagement_text', ''), 80)
            response.headers['X-CTA-Text'] = clean_text(enhancements.get('cta_text', ''), 80)
            response.headers['X-Algorithm-Boost'] = clean_text(enhancements.get('algorithm_score_boost', ''), 80)
            response.headers['X-Diag'] = clean_text(_last_ffmpeg_diag, 200)
            return response
        else:
            return jsonify({"error": "فشل تحسين الفيديو"}), 500

    except Exception as e:
        print(f"Enhancement error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except Exception:
                pass


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
        "version": "mem-2",
        "ffmpeg": _FFMPEG_BIN,
        "trends_loaded": bool(trend_data),
        "trends_updated": trend_data.get('last_updated', 'N/A')[:10] if trend_data else 'N/A'
    })


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
