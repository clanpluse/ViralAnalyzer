import os
import sys
import json
import subprocess
import requests
import base64
from datetime import datetime
from anthropic import Anthropic

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = "clanpluse/ViralAnalyzer"
client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

NICHE_HASHTAGS = {
    "تصاميم منزلية وديكور": ["homedecor", "interiordesign", "homedesign", "roomtour", "ديكور"],
    "تسويق منتجات": ["productreview", "musthave", "shopping", "unboxing", "تسويق"],
    "أزياء وموضة": ["fashion", "ootd", "style", "outfit", "موضة"],
    "طعام ومطاعم": ["food", "recipe", "foodtiktok", "cooking", "مطعم"],
    "تقنية وإلكترونيات": ["tech", "gadgets", "iphone", "تقنية"],
    "رياضة ولياقة": ["fitness", "workout", "gym", "رياضة"],
    "سفر وسياحة": ["travel", "wanderlust", "trip", "سفر"],
    "عام": ["viral", "foryou", "trending", "fyp"]
}


def github_get_file(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        content = base64.b64decode(data['content']).decode('utf-8')
        return content, data['sha']
    return None, None


def github_update_file(path, content, sha, message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    body = {"message": message, "content": encoded}
    if sha:
        body["sha"] = sha
    response = requests.put(url, headers=headers, json=body)
    return response.status_code in [200, 201]


def get_trending_videos(hashtag, count=15):
    """Get trending videos for a hashtag using yt-dlp."""
    try:
        result = subprocess.run([
            sys.executable, "-m", "yt_dlp",
            "--flat-playlist",
            "--dump-json",
            "--playlist-end", str(count),
            "--no-warnings",
            "--no-cache-dir",
            f"https://www.tiktok.com/tag/{hashtag}"
        ], capture_output=True, text=True, timeout=60)

        videos = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                try:
                    videos.append(json.loads(line))
                except Exception:
                    pass
        return videos
    except Exception as e:
        print(f"  Error fetching #{hashtag}: {e}")
        return []


def analyze_niche_patterns(niche, all_videos):
    """Use Claude to analyze patterns from trending videos."""
    if not all_videos:
        return None

    summaries = []
    for v in all_videos[:20]:
        summaries.append({
            "title": v.get('title', '')[:100],
            "description": (v.get('description', '') or '')[:150],
            "duration": v.get('duration', 0),
            "view_count": v.get('view_count', 0),
        })

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": f"""أنت خبير في خوارزميات TikTok ومحتوى "{niche}".

حلل هذه الفيديوهات الرائجة فعلاً واستخرج الأنماط التي تجعلها تنتشر:

{json.dumps(summaries, ensure_ascii=False, indent=2)}

أعطني تحليلاً دقيقاً بصيغة JSON فقط:
{{
  "optimal_duration_seconds": (المدة المثالية بالثواني كرقم),
  "hook_patterns": ["نمط الجملة الافتتاحية 1", "نمط 2", "نمط 3"],
  "content_tips": ["نصيحة محتوى 1", "نصيحة 2", "نصيحة 3"],
  "caption_formula": "صيغة الكابشن الناجح",
  "trending_hashtags": ["هاشتاق1", "هاشتاق2", "هاشتاق3", "هاشتاق4", "هاشتاق5"],
  "best_posting_times": "أفضل أوقات النشر",
  "engagement_triggers": ["محفز تفاعل 1", "محفز 2", "محفز 3"],
  "avoid": ["تجنب 1", "تجنب 2"],
  "virality_formula": "معادلة الانتشار لهذا المجال بجملة واحدة"
}}

JSON فقط."""
            }]
        )

        text = message.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)

    except Exception as e:
        print(f"  Claude analysis error: {e}")
        return None


def run_trend_analysis():
    """Main function: analyze trends for all niches and save to GitHub."""
    print(f"\n{'='*50}")
    print(f"Starting trend analysis: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    trends_data = {}

    for niche, hashtags in NICHE_HASHTAGS.items():
        print(f"\nAnalyzing: {niche}")
        all_videos = []

        for hashtag in hashtags[:3]:
            print(f"  Fetching #{hashtag}...")
            videos = get_trending_videos(hashtag, count=10)
            all_videos.extend(videos)
            print(f"  Got {len(videos)} videos")

        if all_videos:
            print(f"  Analyzing {len(all_videos)} videos with Claude...")
            patterns = analyze_niche_patterns(niche, all_videos)
            if patterns:
                patterns['last_updated'] = datetime.now().isoformat()
                patterns['videos_analyzed'] = len(all_videos)
                trends_data[niche] = patterns
                print(f"  ✅ Done: {niche}")
            else:
                print(f"  ⚠️ No patterns extracted")
        else:
            print(f"  ⚠️ No videos found")

    if trends_data:
        content = json.dumps(trends_data, ensure_ascii=False, indent=2)
        _, sha = github_get_file('data/trends.json')
        success = github_update_file(
            'data/trends.json', content, sha,
            f"Update trends: {datetime.now().strftime('%Y-%m-%d')}"
        )
        print(f"\n✅ Trends saved to GitHub: {success}")
    else:
        print("\n⚠️ No trend data to save")

    print(f"Trend analysis complete: {datetime.now().strftime('%H:%M')}")


if __name__ == '__main__':
    run_trend_analysis()
