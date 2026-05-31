import os
import json
import base64
import tempfile
import subprocess
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

UPLOAD_FOLDER = tempfile.gettempdir()


def extract_frame(video_path, time_sec=1.5):
    """Extract a frame from video at given time."""
    frame_path = video_path + "_frame.jpg"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-ss", str(time_sec), "-vframes", "1",
            "-q:v", "2", frame_path
        ], capture_output=True, timeout=30)
        return frame_path if os.path.exists(frame_path) else None
    except Exception:
        return None


def extract_hook_frame(video_path):
    """Extract frame from first 1.5 seconds (hook)."""
    return extract_frame(video_path, 1.5)


def get_video_duration(video_path):
    """Get video duration in seconds."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", video_path
        ], capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except Exception:
        return 0


def transcribe_audio(video_path):
    """Extract and transcribe audio from video."""
    audio_path = video_path + "_audio.mp3"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "mp3", "-q:a", "5",
            audio_path
        ], capture_output=True, timeout=60)

        if not os.path.exists(audio_path):
            return None

        import whisper
        model = whisper.load_model("tiny")
        result = model.transcribe(audio_path, fp16=False)
        return result.get("text", "").strip() or None
    except Exception as e:
        print(f"Transcribe error: {e}")
        return None
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


def analyze_with_claude(duration, transcript, frame_base64, niche):
    """Send everything to Claude for analysis."""

    duration_str = f"{int(duration)} ثانية" if duration > 0 else "غير معروف"
    transcript_str = transcript if transcript else "لا يوجد كلام مسموع"

    content = [
        {
            "type": "text",
            "text": f"""أنت خبير في خوارزميات TikTok والمحتوى التسويقي.

حلل هذا الفيديو التسويقي وأعطني تقريراً شاملاً بالعربية.

معلومات الفيديو:
- المجال: {niche}
- المدة: {duration_str}
- النص المنطوق: {transcript_str}

قيّم الفيديو وأعطني النتائج بصيغة JSON فقط بهذا الشكل:
{{
  "score": (رقم من 0 إلى 100 يمثل احتمالية الوصول للترند),
  "hook_rating": (تقييم الثواني الأولى: "ممتاز" أو "جيد" أو "يحتاج تحسين"),
  "duration_rating": (تقييم المدة: "مثالية" أو "طويلة جداً" أو "قصيرة جداً"),
  "strengths": [(قائمة نقاط القوة بالعربية, 3 نقاط كحد أقصى)],
  "improvements": [(قائمة التحسينات المطلوبة بالعربية, 3 نقاط)],
  "caption": (كابشن جاهز للنشر بالعربية مناسب للمجال),
  "hashtags": (هاشتاقات مناسبة مفصولة بمسافة, 10 هاشتاقات),
  "best_time": (أفضل وقت للنشر مثل: "8 مساءً - 10 مساءً"),
  "verdict": (حكم نهائي قصير: هل ينصح بنشره الآن أم يحتاج تعديل)
}}

أعطني JSON فقط بدون أي نص إضافي."""
        }
    ]

    # Add frame image if available
    if frame_base64:
        content.insert(0, {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": frame_base64
            }
        })

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": content}]
    )

    response_text = message.content[0].text.strip()
    # Extract JSON if wrapped in markdown
    if "```" in response_text:
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
    return json.loads(response_text)


@app.route('/analyze', methods=['POST'])
def analyze():
    if 'video' not in request.files:
        return jsonify({"error": "لم يتم إرسال فيديو"}), 400

    video_file = request.files['video']
    niche = request.form.get('niche', 'تسويق عام')

    # Save video temporarily
    tmp = tempfile.NamedTemporaryFile(
        suffix='.mp4', delete=False,
        dir=UPLOAD_FOLDER
    )
    video_path = tmp.name
    tmp.close()
    video_file.save(video_path)

    try:
        # 1. Get duration
        duration = get_video_duration(video_path)

        # 2. Extract hook frame
        frame_path = extract_hook_frame(video_path)
        frame_base64 = None
        if frame_path and os.path.exists(frame_path):
            with open(frame_path, 'rb') as f:
                frame_base64 = base64.b64encode(f.read()).decode('utf-8')
            os.unlink(frame_path)

        # 3. Transcribe audio
        transcript = transcribe_audio(video_path)

        # 4. Analyze with Claude
        result = analyze_with_claude(duration, transcript, frame_base64, niche)
        result['transcript'] = transcript or "🔇 بدون كلام"
        result['duration'] = int(duration)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
