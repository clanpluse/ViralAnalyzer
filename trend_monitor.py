import os
import sys
import json
import time
import base64
import requests
from datetime import datetime, timezone, timedelta

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = "clanpluse/ViralAnalyzer"
ANTHROPIC_API_KEY = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
APIFY_TOKEN = (os.environ.get('APIFY_TOKEN') or '').strip()

# Apify TikTok scraper actor (clockworks). run-sync returns dataset items directly.
APIFY_ACTOR = os.environ.get('APIFY_ACTOR', 'clockworks~tiktok-scraper')

# Minimum views in the last 24h to consider a video "viral"
MIN_VIEWS = int(os.environ.get('TREND_MIN_VIEWS', '50000'))
RECENT_HOURS = int(os.environ.get('TREND_RECENT_HOURS', '24'))

NICHES = [
    "تصاميم منزلية وديكور",
    "تسويق منتجات",
]

# Hashtags to search per niche. English/global tags first because the scraper
# returns far more results for them than for Arabic tags; Arabic kept as fallback.
NICHE_HASHTAGS = {
    "تصاميم منزلية وديكور": ["homedecor", "interiordesign", "homedesign", "ديكور"],
    "تسويق منتجات": ["marketing", "smallbusiness", "digitalmarketing", "تسويق"],
    "أزياء وموضة": ["fashion", "ootd", "موضة"],
    "طعام ومطاعم": ["food", "recipe", "طبخ"],
    "تقنية وإلكترونيات": ["tech", "gadgets", "تقنية"],
    "رياضة ولياقة": ["fitness", "gym", "رياضة"],
    "سفر وسياحة": ["travel", "tourism", "سفر"],
    "عام": ["fyp", "viral", "اكسبلور"],
}


def call_claude(prompt, max_tokens=1200):
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
            time.sleep(3)
    return None


def github_get_file(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            return content, data['sha']
    except Exception:
        pass
    return None, None


def github_update_file(path, content, sha, message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/json"}
    encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    body = {"message": message, "content": encoded}
    if sha:
        body["sha"] = sha
    try:
        response = requests.put(url, headers=headers, json=body, timeout=30)
        return response.status_code in [200, 201]
    except Exception as e:
        print(f"  GitHub update failed: {e}")
        return False


def apify_search_hashtag(hashtag, limit=25):
    """Run the Apify TikTok scraper for a hashtag and return dataset items."""
    if not APIFY_TOKEN:
        print("  APIFY_TOKEN not set — skipping Apify call")
        return []
    url = (f"https://api.apify.com/v2/acts/{APIFY_ACTOR}"
           f"/run-sync-get-dataset-items?token={APIFY_TOKEN}")
    payload = {
        "hashtags": [hashtag],
        "resultsPerPage": limit,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSubtitles": False,
        "proxyCountryCode": "None",
    }
    try:
        r = requests.post(url, json=payload, timeout=240)
        if r.status_code in (200, 201):
            items = r.json()
            print(f"  Apify '{hashtag}': {len(items)} items")
            return items if isinstance(items, list) else []
        print(f"  Apify error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  Apify request failed: {e}")
    return []


def _parse_time(item):
    iso = item.get('createTimeISO') or ''
    try:
        return datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except Exception:
        ts = item.get('createTime')
        if ts:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                return None
        return None


def collect_viral_videos(niche):
    """Search the niche's hashtag(s) and return the highest-view videos.

    The hashtag scraper returns the top videos for the tag (not strictly the
    last 24h), so we rank by views and keep those above MIN_VIEWS. We still flag
    how many are genuinely recent for context."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)
    seen_ids = set()
    all_videos = []
    recent_count = 0

    tags = NICHE_HASHTAGS.get(niche, NICHE_HASHTAGS["عام"])
    for tag in tags:  # try tags in order, stop once we have enough results
        items = apify_search_hashtag(tag, limit=25)
        for it in items:
            vid = it.get('id') or it.get('webVideoUrl')
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            views = it.get('playCount') or 0
            ctime = _parse_time(it)
            if ctime and ctime >= cutoff:
                recent_count += 1
            all_videos.append({
                "caption": (it.get('text') or '')[:200],
                "views": views,
                "likes": it.get('diggCount') or 0,
                "comments": it.get('commentCount') or 0,
                "shares": it.get('shareCount') or 0,
                "duration": (it.get('videoMeta') or {}).get('duration', 0),
                "author": (it.get('authorMeta') or {}).get('name', ''),
                "hashtags": [h.get('name') for h in (it.get('hashtags') or []) if h.get('name')],
            })
        # Stop once we have enough usable results (keeps Apify cost/time low)
        if len(all_videos) >= 15:
            break
        time.sleep(1)

    viral = [v for v in all_videos if v["views"] >= MIN_VIEWS]
    chosen = sorted(viral or all_videos, key=lambda x: x["views"], reverse=True)[:12]
    if recent_count >= 3:
        basis = f"فيديوهات حديثة (آخر {RECENT_HOURS} ساعة) عالية المشاهدة"
    else:
        basis = "أعلى الفيديوهات مشاهدة في المجال حالياً"
    return chosen, basis


def analyze_patterns(niche, videos, basis):
    """Ask Claude to extract winning patterns (Arabic) from real viral videos."""
    if not videos:
        return None

    sample = [{
        "caption": v["caption"],
        "views": v["views"],
        "likes": v["likes"],
        "comments": v["comments"],
        "shares": v["shares"],
        "duration_sec": int(v["duration"] or 0),
        "hashtags": v["hashtags"][:8],
    } for v in videos]

    prompt = f"""أنت خبير في خوارزميات TikTok لعام 2025.

هذه بيانات فيديوهات حقيقية رائجة الآن في مجال "{niche}" ({basis}):

{json.dumps(sample, ensure_ascii=False, indent=2)}

ادرس الكابشن والمشاهدات والتفاعل والمدد والهاشتاقات، واستخرج الأنماط الفائزة الحقيقية.
أعطني تحليلاً عربياً دقيقاً بصيغة JSON فقط:
{{
  "optimal_duration_seconds": (المدة المثالية بالثواني كرقم بناءً على المتوسط المرجّح للأنجح),
  "hook_patterns": ["نمط افتتاحي ناجح 1", "نمط 2", "نمط 3"],
  "hook_text_examples": ["مثال جملة افتتاحية عربية مقتبسة/مستوحاة 1", "مثال 2", "مثال 3"],
  "content_tips": ["نصيحة محتوى مبنية على ما نجح 1", "نصيحة 2", "نصيحة 3"],
  "caption_formula": "صيغة الكابشن الناجح في هذا المجال",
  "trending_hashtags": ["هاشتاق1", "هاشتاق2", "هاشتاق3", "هاشتاق4", "هاشتاق5"],
  "best_posting_times": "أفضل أوقات النشر المقترحة",
  "engagement_triggers": ["محفز تفاعل 1", "محفز 2", "محفز 3"],
  "avoid": ["تجنّب 1", "تجنّب 2"],
  "virality_formula": "معادلة الانتشار المستخلصة لهذا المجال"
}}

JSON فقط بدون أي نص إضافي."""

    response = call_claude(prompt, max_tokens=1300)
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
    """Main: for each niche, fetch real viral videos via Apify, extract patterns, save."""
    print(f"\n{'='*50}")
    print(f"Trend Analysis (Apify): {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    if not APIFY_TOKEN:
        print("APIFY_TOKEN missing — aborting trend analysis.")
        return

    # Start from existing data so partial progress accumulates across restarts
    existing, _ = github_get_file('data/trends.json')
    try:
        trends_data = json.loads(existing) if existing else {}
    except Exception:
        trends_data = {}
    report = {"generated_at": datetime.now().isoformat(), "niches": {}}

    for niche in NICHES:
        print(f"\n📊 {niche}")
        try:
            videos, basis = collect_viral_videos(niche)
        except Exception as e:
            print(f"  collect error: {e}")
            continue
        if not videos:
            print("  no videos collected")
            continue

        patterns = analyze_patterns(niche, videos, basis)
        if not patterns:
            print("  no patterns extracted")
            continue

        patterns['last_updated'] = datetime.now().isoformat()
        patterns['videos_analyzed'] = len(videos)
        patterns['basis'] = basis
        trends_data[niche] = patterns

        top_authors = {}
        for v in videos:
            a = v.get('author') or ''
            if a:
                top_authors[a] = top_authors.get(a, 0) + 1
        report["niches"][niche] = {
            "accounts": [{"username": a, "videos_analyzed": c}
                         for a, c in sorted(top_authors.items(), key=lambda x: -x[1])[:5]],
            "total_videos": len(videos),
            "key_finding": patterns.get('virality_formula', ''),
            "optimal_duration": patterns.get('optimal_duration_seconds', 0),
            "top_hashtags": patterns.get('trending_hashtags', [])[:5],
        }
        print(f"  ✅ analyzed {len(videos)} videos")

        # Incremental save — keep progress even if the container restarts
        content = json.dumps(trends_data, ensure_ascii=False, indent=2)
        _, sha = github_get_file('data/trends.json')
        ok = github_update_file('data/trends.json', content, sha,
                                f"Update trends ({niche}): {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  {'✅ saved' if ok else '❌ save failed'}")

    if report["niches"]:
        rc = json.dumps(report, ensure_ascii=False, indent=2)
        _, sha = github_get_file('data/trend_report.json')
        github_update_file('data/trend_report.json', rc, sha,
                           f"Update report: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("✅ trend_report.json saved")

    print(f"\nDone: {datetime.now().strftime('%H:%M')}")


if __name__ == '__main__':
    run_trend_analysis()
