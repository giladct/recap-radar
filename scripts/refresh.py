#!/usr/bin/env python3
import os, json, re, time, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
IL = timezone(timedelta(hours=3))
now = datetime.now(IL)
TODAY = now.strftime('%B %d, %Y')
SNAPSHOT = now.strftime('%H:%M')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
}


def fetch_page(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def call_llm(prompt):
    # GitHub Models — uses the GITHUB_TOKEN already present in every Actions run
    for model in ['gpt-4o-mini', 'meta-llama-3.1-70b-instruct']:
        for attempt in range(3):
            try:
                r = requests.post(
                    'https://models.inference.ai.azure.com/chat/completions',
                    headers={
                        'Authorization': f'Bearer {GITHUB_TOKEN}',
                        'Content-Type': 'application/json',
                    },
                    json={
                        'model': model,
                        'messages': [{'role': 'user', 'content': prompt}],
                        'max_tokens': 4000,
                        'temperature': 0.1,
                    },
                    timeout=90
                )
                if r.status_code == 429:
                    wait = 15 * (attempt + 1)
                    print(f'{model} rate-limited, waiting {wait}s...')
                    time.sleep(wait)
                    continue
                if not r.ok:
                    print(f'{model} HTTP {r.status_code}: {r.text[:300]}')
                    break
                print(f'OK: {model}')
                return r.json()['choices'][0]['message']['content']
            except Exception as e:
                print(f'{model} attempt {attempt+1} error: {e}')
                time.sleep(5)
    raise RuntimeError('All models failed')


def extract_json(text):
    text = re.sub(r'```json|```', '', text).strip()
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        raise ValueError('No JSON in response')
    return json.loads(text[start:end + 1])


def heatbar(heat):
    h = max(1, min(3, int(heat or 1)))
    cls = 'on-high' if h >= 3 else ('on-mid' if h == 2 else 'on-low')
    segs = ''.join(
        f'<div class="seg {cls}"></div>' if i < h else '<div class="seg"></div>'
        for i in range(3)
    )
    return f'<div class="heatbar">{segs}</div>'


def recap_link(home_he, away_he):
    q = quote(f'{away_he} {home_he} תקציר')
    return f'<a class="recap-link" href="https://www.youtube.com/results?search_query={q}" target="_blank" rel="noopener">&#9654; Watch recap</a>'


def card_html(g):
    lid     = g.get('id', '')
    league  = g.get('league', '')
    home    = g.get('home_he', '')
    away    = g.get('away_he', '')
    status  = g.get('status', 'upcoming')
    score   = g.get('score') or '?-?'
    period  = g.get('period', '')
    heat    = g.get('heat', 1)
    note    = g.get('note', '')
    tv      = g.get('tv', False)
    channel = g.get('channel', '') or ''
    started = g.get('started_at', '') or ''

    attrs = ''
    if lid:              attrs += f' data-id="{lid}"'
    if status == 'live': attrs += ' data-live="true"'
    if tv:               attrs += ' data-tv="true"'
    if started:          attrs += f' data-started-at="{started}"'

    ch = f'<span class="channel">&#128250; {channel}</span>' if tv and channel else ''

    if status == 'live':
        right = (f'<span class="live-meta-wrap">'
                 f'<button class="reveal-btn">&#128065; Score</button>'
                 f'<span class="score">{score}</span>'
                 f'<span class="meta live"><span class="dot"></span> {period or "Live"}</span>'
                 f'<span class="started-ago"></span>'
                 f'</span>{ch}')
        body = (f'<div class="meta" style="margin-bottom:3px;">HEAT SO FAR (LIVE)</div>'
                f'{heatbar(heat)}<div class="note">{note}</div>')
    elif status == 'finished':
        right = (f'<span class="live-meta-wrap">'
                 f'<button class="reveal-btn">&#128065; Score</button>'
                 f'<span class="score">{score}</span>'
                 f'<span class="started-ago"></span>'
                 f'</span>{ch}')
        body = f'{heatbar(heat)}<div class="note">{note}</div>{recap_link(home, away)}'
    elif status == 'postponed':
        right = ch
        body  = '<div class="note postponed">Postponed — not played today.</div>'
    else:
        right = f'<span class="live-meta-wrap"><span class="meta">{period or "TBD"}</span>{ch}</span>'
        body  = '<div class="note">Not started yet — heat rating will appear after full time.</div>'

    return (f'<div class="card"{attrs}>\n'
            f'  <div class="card-top"><span class="league">{league}</span>{right}</div>\n'
            f'  <div class="teams">{away} - {home}</div>\n'
            f'  {body}\n'
            f'</div>')


def section_html(title, games):
    if not games:
        return ''
    cards = '\n'.join(card_html(g) for g in games)
    return f'<div class="section">\n  <div class="section-title">{title}</div>\n{cards}\n</div>\n'


# ── fetch livegames.co.il ──────────────────────────────────────────────────────
print('Fetching livegames.co.il...')
try:
    page_html = fetch_page('https://www.livegames.co.il/')
    # Strip scripts/styles to save tokens, keep visible text structure
    page_text = re.sub(r'<script[^>]*>.*?</script>', '', page_html, flags=re.DOTALL)
    page_text = re.sub(r'<style[^>]*>.*?</style>', '', page_text, flags=re.DOTALL)
    page_text = page_text[:50000]
    source_desc = 'HTML from livegames.co.il'
except Exception as e:
    print(f'Fetch failed ({e}), falling back to knowledge cutoff data')
    page_text = f'Could not fetch livegames.co.il: {e}'
    source_desc = 'fallback'

PROMPT = f"""Today is {TODAY} Israel time.

Below is content fetched from livegames.co.il (Israeli sports scores site).
Extract ALL sports events listed for today and return ONLY raw JSON (no markdown fences):

{{"games":[{{"id":"home-away","league":"League name","home_he":"קבוצת בית","away_he":"קבוצת חוץ","status":"upcoming|live|finished|postponed","score":"X-Y or null","period":"HT|FT|Q2|20:45","heat":2,"note":"under 8 words no score numbers","tv":false,"channel":null,"started_at":"ISO8601 or null","sport":"football|basketball|baseball|tennis|other"}}]}}

Rules:
- status: live=in play now, finished=full time, upcoming=not started, postponed=cancelled
- heat 1=low drama 2=decent 3=must-watch — only for live/finished games
- note must NOT contain score numbers
- tv=true if shown on Israeli TV, include channel name
- sport=football for soccer

Page content:
{page_text}"""

print('Calling LLM...')
raw = call_llm(PROMPT)
data = extract_json(raw)
games = data.get('games', [])
print(f'Parsed {len(games)} games')

football = [g for g in games if g.get('sport', 'football') == 'football']
other    = [g for g in games if g.get('sport', 'football') != 'football']

live_sec     = section_html('<span class="dot"></span> Live Now',
                            [g for g in football if g['status'] == 'live'])
finished_sec = section_html('&#9989; Full Time — Recap Heat',
                            [g for g in football if g['status'] in ('finished', 'postponed')])
upcoming_sec = section_html('&#9200; Kicking Off Soon',
                            [g for g in football if g['status'] == 'upcoming'])
other_sec    = section_html('&#128250; Other Sports — On Israeli TV', other)

TEMPLATE = open(os.path.join(os.path.dirname(__file__), 'template.html'), encoding='utf-8').read()

html = (TEMPLATE
        .replace('%%LIVE%%',     live_sec)
        .replace('%%FINISHED%%', finished_sec)
        .replace('%%UPCOMING%%', upcoming_sec)
        .replace('%%OTHER%%',    other_sec)
        .replace('%%TODAY%%',    TODAY)
        .replace('%%SNAPSHOT%%', SNAPSHOT))

out = os.path.join(os.path.dirname(__file__), '..', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'Wrote index.html ({len(games)} games, source: {source_desc})')
