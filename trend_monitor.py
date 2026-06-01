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
ANTHROPIC_API_KEY = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()

NICHES = [
    "تصاميم منزلية وديكور",
    "تسويق منتجات",
    "أزياء وموضة",
    "طعام ومطاعم",
    "تقنية وإلكترونيات",
    "رياضة ولياقة",
    "سفر وسياحة",
    "عام"
]


def call_claude(prompt, max_tokens=1000):
    """Call Claude API directly via requests."""
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
                    'messages': [{'role': 'user', 'content': prompt}]
                },
                timeout=60
            )
            data = response.json()
            if 'content' in data:
                return data['content'][0]['text']
            raise Exception(f"API error: {data}")
        except Exception as e:
            print(f"  Claude attempt {attempt+1} failed: {e}")
            if attempt == 2:
                return None
            import time
            time.sleep(3)
    return None


def github_get_file(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            return content, data['sha']
    except Exception:
        pass
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
    try:
        response = requests.put(url, headers=headers, json=body)
        return response.status_code in [200, 201]
    except Exception:
        return False


def discover_trending_accounts(niche):
    """Use Claude to discover trending TikTok accounts for a niche."""
    print(f"  Discovering accounts for: {niche}")
    prompt = f"""أنت خبير في TikTok ومتابع للحسابات الرائجة.

اعطني قائمة بـ 8 حسابات TikTok ناجحة ورائجة في مجال "{niche}".

الشروط:
- حسابات حقيقية وموجودة على TikTok
- لديها متابعين كثيرين وتفاعل عالٍ
- تنشر محتوى باللغة العربية أو المحتوى المناسب للجمهور العربي
- ليس بالضرورة حسابات عربية، يمكن أن تكون عالمية ناجحة

أعطني أسماء المستخدمين فقط، بدون @ أو أي شرح، كل اسم في سطر."""

    response = call_claude(prompt, max_tokens=500)
    if not response:
        return []

    accounts = []
    for line in response.strip().split('\n'):
        line = line.strip().strip('@').strip('-').strip('*').strip('•').strip()
        line = line.split(' ')[0].split('\t')[0]
        if line and 2 < len(line) < 50 and ' ' not in line:
            accounts.append(line)

    print(f"  Discovered {len(accounts)} accounts: {accounts[:5]}")
    return accounts[:8]


def get_account_videos(username, count=10):
    """Get recent videos from a TikTok account."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp",
             "--flat-playlist", "--dump-json",
             "--playlist-end", str(count),
             "--no-warnings", "--no-cache-dir",
             f"https://www.tiktok.com/@{username}"],
            capture_output=True, text=True, timeout=60
        )
        videos = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                try:
                    videos.append(json.loads(line))
                except Exception:
                    pass
        return videos
    except Exception as e:
        print(f"  Error fetching @{username}: {e}")
        return []


def analyze_account_patterns(niche, accounts_data):
    """Analyze patterns from multiple accounts' videos."""
    if not accounts_data:
        return None

    summaries = []
    for account, videos in accounts_data.items():
        if videos:
            summaries.append({
                "account": account,
                "video_count": len(videos),
                "sample_titles": [v.get('title', '')[:80] for v in videos[:3]],
                "avg_duration": sum(v.get('duration', 0) for v in videos) / max(len(videos), 1)
            })

    if not summaries:
        return None

    prompt = f"""أنت خبير في خوارزميات TikTok.

حلل هذه البيانات من حسابات رائجة في مجال "{niche}":

{json.dumps(summaries, ensure_ascii=False, indent=2)}

أعطني تحليلاً دقيقاً بصيغة JSON فقط:
{{
  "optimal_duration_seconds": (المدة المثالية بالثواني كرقم),
  "hook_patterns": ["نمط الـ Hook 1", "نمط 2", "نمط 3"],
  "hook_text_examples": ["مثال جملة افتتاحية 1", "مثال 2", "مثال 3"],
  "content_tips": ["نصيحة 1", "نصيحة 2", "نصيحة 3"],
  "caption_formula": "صيغة الكابشن الناجح",
  "text_overlay_tips": ["نصيحة نص على الشاشة 1", "نصيحة 2"],
  "trending_hashtags": ["هاشتاق1", "هاشتاق2", "هاشتاق3", "هاشتاق4", "هاشتاق5"],
  "best_posting_times": "أفضل أوقات النشر",
  "engagement_triggers": ["محفز 1", "محفز 2", "محفز 3"],
  "avoid": ["تجنب 1", "تجنب 2"],
  "virality_formula": "معادلة الانتشار لهذا المجال"
}}

JSON فقط."""

    response = call_claude(prompt, max_tokens=1200)
    if not response:
        return None

    try:
        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"  JSON parse error: {e}")
        return None


def run_trend_analysis():
    """Main function: discover accounts, analyze trends, save report."""
    print(f"\n{'='*50}")
    print(f"Trend Analysis: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    trends_data = {}
    report = {
        "generated_at": datetime.now().isoformat(),
        "niches": {}
    }

    for niche in NICHES:
        print(f"\n📊 Analyzing: {niche}")

        # 1. Discover trending accounts
        accounts = discover_trending_accounts(niche)
        if not accounts:
            print(f"  ⚠️ No accounts found")
            continue

        # 2. Fetch videos from accounts
        accounts_data = {}
        successful_accounts = []
        for account in accounts:
            print(f"  📥 Fetching @{account}...")
            videos = get_account_videos(account, count=8)
            if videos:
                accounts_data[account] = videos
                successful_accounts.append({
                    "username": account,
                    "videos_analyzed": len(videos)
                })
                print(f"  ✅ Got {len(videos)} videos from @{account}")
            else:
                print(f"  ❌ No videos from @{account}")

        if not accounts_data:
            print(f"  ⚠️ No data collected")
            continue

        # 3. Analyze patterns
        print(f"  🧠 Analyzing patterns from {len(accounts_data)} accounts...")
        patterns = analyze_account_patterns(niche, accounts_data)

        if patterns:
            patterns['last_updated'] = datetime.now().isoformat()
            patterns['accounts_analyzed'] = len(accounts_data)
            patterns['videos_analyzed'] = sum(len(v) for v in accounts_data.values())
            trends_data[niche] = patterns

            report["niches"][niche] = {
                "accounts": successful_accounts,
                "total_videos": patterns['videos_analyzed'],
                "key_finding": patterns.get('virality_formula', ''),
                "optimal_duration": patterns.get('optimal_duration_seconds', 0),
                "top_hashtags": patterns.get('trending_hashtags', [])[:5]
            }
            print(f"  ✅ Done: {niche}")

    # Save trends data
    if trends_data:
        content = json.dumps(trends_data, ensure_ascii=False, indent=2)
        _, sha = github_get_file('data/trends.json')
        github_update_file('data/trends.json', content, sha,
                          f"Update trends: {datetime.now().strftime('%Y-%m-%d')}")
        print(f"\n✅ Trends saved")

    # Save report
    if report["niches"]:
        report_content = json.dumps(report, ensure_ascii=False, indent=2)
        _, sha = github_get_file('data/trend_report.json')
        github_update_file('data/trend_report.json', report_content, sha,
                          f"Update report: {datetime.now().strftime('%Y-%m-%d')}")
        print(f"✅ Report saved")

    print(f"\nDone: {datetime.now().strftime('%H:%M')}")


if __name__ == '__main__':
    run_trend_analysis()
