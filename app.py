import os
import sys
import json
import base64
import tempfile
import subprocess
import threading
import schedule
import time
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic
from datetime import datetime

app = Flask(__name__)
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')


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
                timeout=60
            )
            data = response.json()
            if 'content' in data:
                return data['content'][0]['text']
            else:
                raise Exception(f"API error: {data}")
        except Exception as e:
            print(f"Claude attempt {attempt+1} failed: {e}")
            if attempt == 2:
                raise
            time.sleep(3)
    return None

# Add ffmpeg to PATH automatically
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    print("✅ ffmpeg added to PATH via static-ffmpeg")
except Exception as e:
    print(f"⚠️ static-ffmpeg not available: {e}")
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = "clanpluse/ViralAnalyzer"
UPLOAD_FOLDER = tempfile.gettempdir()

# Cache for trend data
_trends_cache = {}
_trends_loaded_at = None


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
    """Get ffmpeg binary path."""
    return "ffmpeg"


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


def get_video_duration(video_path):
    """Get video duration in seconds."""
    try:
        import imageio_ffmpeg
        ffprobe = get_ffmpeg_path().replace("ffmpeg", "ffprobe")
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

        import whisper
        model = whisper.load_model("tiny")
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
