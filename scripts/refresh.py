#!/usr/bin/env python3
import os, json, re, sys, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
IL = timezone(timedelta(hours=3))
now = datetime.now(IL)
TODAY = now.strftime('%B %d, %Y')
SNAPSHOT = now.strftime('%H:%M')

PROMPT = f"""Search livegames.co.il for ALL sports events today, {TODAY} Israel time.

Return ONLY raw JSON (no markdown fences, no other text):
{{"games":[{{"id":"home-away","league":"League name","home_he":"קבוצת בית","away_he":"קבוצת חוץ","status":"upcoming|live|finished|postponed","score":"X-Y or null","period":"HT|FT|Q2|20:45 etc","heat":2,"note":"under 8 words no score numbers","tv":false,"channel":null,"started_at":"2026-06-12T18:00:00+03:00 or null","sport":"football|basketball|baseball|tennis|other"}}]}}

Rules:
- status live = currently in play, finished = full time, upcoming = not started yet
- heat 1=quiet 2=decent 3=must-watch, only set for live or finished games
- note must NOT contain any score numbers
- tv=true if broadcast on Israeli TV channel, include channel name
- sport=football for soccer, other values for other sports
- include every single game listed on livegames.co.il today"""


def call_gemini(prompt):
    import time
    models = ['gemini-1.5-flash', 'gemini-2.0-flash']
    last_err = None
    for model in models:
        for attempt in range(3):
            try:
                r = requests.post(
                    f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}',
                    json={
                        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                        'tools': [{'google_search': {}}],
                        'generationConfig': {'maxOutputTokens': 4000, 'temperature': 0.1}
                    },
                    timeout=90
                )
                if r.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f'{model} rate-limited, retrying in {wait}s...')
                    time.sleep(wait)
                    last_err = f'429 on {model}'
                    continue
                r.raise_for_status()
                parts = r.json()['candidates'][0]['content']['parts']
                return '\n'.join(p.get('text', '') for p in parts if p.get('text'))
            except Exception as e:
                last_err = str(e)
                time.sleep(5)
    raise RuntimeError(f'All models failed: {last_err}')


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
    if lid:     attrs += f' data-id="{lid}"'
    if status == 'live': attrs += ' data-live="true"'
    if tv:      attrs += ' data-tv="true"'
    if started: attrs += f' data-started-at="{started}"'

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
    return (f'<div class="section">\n'
            f'  <div class="section-title">{title}</div>\n'
            f'  {cards}\n'
            f'</div>\n')


# ── fetch & parse ──────────────────────────────────────────────────────────────
print('Calling Gemini...')
raw = call_gemini(PROMPT)
data = extract_json(raw)
games = data.get('games', [])
print(f'Got {len(games)} games')

football   = [g for g in games if g.get('sport', 'football') == 'football']
other      = [g for g in games if g.get('sport', 'football') != 'football']

live_sec     = section_html('<span class="dot"></span> Live Now',
                            [g for g in football if g['status'] == 'live'])
finished_sec = section_html('&#9989; Full Time — Recap Heat',
                            [g for g in football if g['status'] in ('finished', 'postponed')])
upcoming_sec = section_html('&#9200; Kicking Off Soon',
                            [g for g in football if g['status'] == 'upcoming'])
other_sec    = section_html('&#128250; Other Sports — On Israeli TV', other)

# ── HTML template (no f-string — avoids escaping every CSS/JS brace) ──────────
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
print(f'Wrote index.html')
