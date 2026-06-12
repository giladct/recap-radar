#!/usr/bin/env python3
import os, json, re, time, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
IL = timezone(timedelta(hours=3))
now = datetime.now(IL)
yesterday = now - timedelta(days=1)
TODAY = now.strftime('%B %d, %Y')
YESTERDAY = yesterday.strftime('%B %d, %Y')
YESTERDAY_DATE = yesterday.strftime('%Y-%m-%d')
SNAPSHOT = now.strftime('%H:%M')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'he-IL,he;q=0.9,en;q=0.8',
}


def strip_html(html):
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>',  '', text,  flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


BASE_URL = 'https://www.livegames.co.il/livegames.aspx'

def fetch_pages():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page()

        # Today: main scores page — extract #GameResultView directly
        page.goto(BASE_URL, wait_until='networkidle', timeout=30000)
        game_view = page.evaluate("() => { const el = document.getElementById('GameResultView'); return el ? el.innerText.trim() : null; }")
        scores_text = (re.sub(r'\s+', ' ', game_view)[:8000] if game_view and len(game_view) > 100
                       else strip_html(page.content())[:5000])
        print(f'Today URL: {page.url}')

        # Today: שידורים (TV broadcasts) tab
        tv_text = ''
        try:
            page.click('a:has-text("שידורים")', timeout=5000)
            page.wait_for_load_state('networkidle', timeout=10000)
            tv_text = strip_html(page.content())[:4000]
        except Exception as e:
            print(f'TV tab not found: {e}')

        # Yesterday: use changeDateGames() JS function — found via window inspection
        yesterday_text = ''
        try:
            page.goto(BASE_URL, wait_until='networkidle', timeout=20000)

            # Capture which element classes hold today's game results
            # (so we can target the same container after date change)
            game_container_classes = page.evaluate('''() => {
                const candidates = ['#gameResults', '#gamesDiv', '.games-container',
                    '#tblGames', '#tblResults', '#gamesList', '.gamesList',
                    '#divGames', '#divResults', '#mainContent', '#mainResults'];
                for (const sel of candidates) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim().length > 100) return sel + ' (found)';
                }
                // fallback: find div with most text content
                let best = null, bestLen = 0;
                document.querySelectorAll('div[id]').forEach(d => {
                    const t = d.innerText.trim();
                    if (t.length > bestLen && t.length < 50000) {
                        bestLen = t.length; best = '#' + d.id + ' (' + t.length + ')';
                    }
                });
                return best || 'not found';
            }''')
            print(f'Game container: {game_container_classes}')

            # Get the integer day counter and winner_program, then call showDay(yesterday)
            page_data = page.evaluate('''() => ({
                todayDate: typeof todayDate !== "undefined" ? todayDate : null,
                winnerProgram: ($(".WinnerArea").data("program") || $(".GamesResultsTable").data("winner-program") || ""),
            })''')
            today_date_int = page_data.get('todayDate')
            winner_program = page_data.get('winnerProgram', '')
            print(f'todayDate={today_date_int}, winnerProgram={winner_program!r}')

            if today_date_int is not None:
                yest_date_int = int(today_date_int) - 1
                print(f'Fetching Days.ashx?date={yest_date_int} via in-page fetch()...')

                # Use fetch() from within the page context — carries session cookies + correct Origin
                ashx_html = page.evaluate(f'''async () => {{
                    try {{
                        const resp = await fetch('/handlers/Days.ashx?date={yest_date_int}&winner_program={winner_program}');
                        return await resp.text();
                    }} catch(e) {{
                        return 'FETCH_ERROR: ' + String(e);
                    }}
                }}''')
                print(f'Days.ashx response: {len(ashx_html)} chars, starts: {ashx_html[:300]!r}')

                if ashx_html and not ashx_html.startswith('FETCH_ERROR') and len(ashx_html) > 200 and 'Runtime Error' not in ashx_html:
                    yesterday_text = strip_html(ashx_html)[:8000]
                    print(f'Got yesterday from Days.ashx in-page fetch')
                else:
                    # Fallback: try day-2 (maybe numbering is different)
                    yest2 = int(today_date_int) - 2
                    print(f'Trying day-2 ({yest2})...')
                    ashx_html2 = page.evaluate(f'''async () => {{
                        try {{
                            const resp = await fetch('/handlers/Days.ashx?date={yest2}&winner_program={winner_program}');
                            return await resp.text();
                        }} catch(e) {{ return 'FETCH_ERROR: ' + String(e); }}
                    }}''')
                    print(f'Day-2 response: {len(ashx_html2)} chars, starts: {ashx_html2[:200]!r}')
                    if ashx_html2 and not ashx_html2.startswith('FETCH_ERROR') and len(ashx_html2) > 200 and 'Runtime Error' not in ashx_html2:
                        yesterday_text = strip_html(ashx_html2)[:8000]
                        print('Got yesterday from day-2 fetch')
                page.evaluate(f'showDay({yest_date_int}, null, 1)')
                # Wait for .ShowResultTablediv to be populated
                try:
                    page.wait_for_function(
                        "() => document.querySelector('.ShowResultTablediv') && document.querySelector('.ShowResultTablediv').innerText.trim().length > 500",
                        timeout=20000
                    )
                    print('ShowResultTablediv loaded')
                except Exception as we:
                    print(f'Wait timed out: {we}')

                game_text = page.evaluate("() => { const el = document.querySelector('.ShowResultTablediv'); return el ? el.innerText.trim() : null; }")
                grv_len = len(game_text) if game_text else 0
                print(f'.ShowResultTablediv: {grv_len} chars')
                if game_text and grv_len > 200:
                    yesterday_text = re.sub(r'\s+', ' ', game_text)[:8000]
                    print(f'Got yesterday from .ShowResultTablediv')
                else:
                    print('ShowResultTablediv empty; trying direct Days.ashx fetch')
                    # Direct fetch of the data endpoint
                    ashx_url = f'https://www.livegames.co.il/handlers/Days.ashx?date={yest_date_int}&winner_program={winner_program}'
                    print(f'Fetching: {ashx_url}')
                    page.goto(ashx_url, wait_until='networkidle', timeout=20000)
                    yesterday_text = strip_html(page.content())[:8000]
                    print(f'Direct ashx fetch: {len(yesterday_text)} chars')
            else:
                print('todayDate not found on page')

            print(f'Yesterday sample: {yesterday_text[:400]!r}')
        except Exception as e:
            print(f'Yesterday fetch failed: {e}')

        browser.close()
    return scores_text, tv_text, yesterday_text


def call_llm(prompt):
    # GitHub Models — uses the GITHUB_TOKEN already present in every Actions run
    for model in ['gpt-4o-mini', 'gpt-4o', 'Llama-3.1-70B-Instruct']:
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
                        'max_tokens': 10000,
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
    scores_text, tv_text, yesterday_text = fetch_pages()
    print(f'Scores: {len(scores_text)} chars, TV: {len(tv_text)} chars, Yesterday: {len(yesterday_text)} chars')
    source_desc = 'livegames.co.il'
except Exception as e:
    print(f'Fetch failed ({e}), using empty content')
    scores_text, tv_text, yesterday_text = '', '', ''
    source_desc = 'fallback'

TODAY_PROMPT = f"""Today is {TODAY} Israel time.

Below is content from livegames.co.il. Extract ALL sports events and return ONLY raw JSON (no markdown fences):

{{"games":[{{"id":"away-home-kebab","league":"League name","home_he":"קבוצת בית","away_he":"קבוצת חוץ","status":"upcoming|live|finished|postponed","score":"X-Y or null","period":"HT|FT|Q2|20:45","heat":2,"note":"under 8 words no score numbers","tv":false,"channel":null,"started_at":"ISO8601+03:00 or null","sport":"football|basketball|baseball|tennis|other"}}]}}

Rules:
- id: unique short kebab-case string
- status: live=in play, finished=full time, upcoming=not started, postponed=cancelled
- heat 1=low drama 2=decent 3=must-watch — only for live/finished
- note: max 8 words, no score numbers
- CRITICAL — tv field: Cross-reference TV BROADCASTS. Any game there MUST have tv=true and channel set to the channel name (e.g. "כאן 11", "ספורט 1")
- sport=football for soccer

=== SCORES ===
{scores_text}

=== TV BROADCASTS ===
{tv_text}"""

YEST_PROMPT = f"""Yesterday was {YESTERDAY} Israel time.

Below is yesterday's sports content from livegames.co.il. Extract ALL finished/postponed events and return ONLY raw JSON:

{{"games":[{{"id":"away-home-kebab","league":"League name","home_he":"קבוצת בית","away_he":"קבוצת חוץ","status":"finished|postponed","score":"X-Y or null","heat":2,"note":"under 8 words no score numbers","tv":false,"channel":null,"sport":"football|basketball|baseball|tennis|other"}}]}}

Rules:
- id: unique short kebab-case string
- status: only finished or postponed (yesterday's games are over)
- heat 1=low drama 2=decent 3=must-watch
- note: max 8 words, no score numbers
- sport=football for soccer

=== YESTERDAY SCORES ===
{yesterday_text}"""

print('Calling LLM for today...')
raw_today = call_llm(TODAY_PROMPT)
today_games = extract_json(raw_today).get('games', [])
for g in today_games:
    g['day'] = 'today'
print(f'Today: {len(today_games)} games')

yest_games = []
if yesterday_text:
    print('Yesterday text sample:', yesterday_text[:300])
    print('Calling LLM for yesterday...')
    raw_yest = call_llm(YEST_PROMPT)
    yest_games = extract_json(raw_yest).get('games', [])
    for g in yest_games:
        g['day'] = 'yesterday'
    print(f'Yesterday: {len(yest_games)} games')

games = today_games + yest_games
print(f'Total: {len(games)} games')

football = [g for g in games if g.get('sport', 'football') == 'football']
other    = [g for g in games if g.get('sport', 'football') != 'football']

today_fb     = [g for g in football if g.get('day', 'today') == 'today']
yest_fb      = [g for g in football if g.get('day', 'today') == 'yesterday']

live_sec     = section_html('<span class="dot"></span> Live Now',
                            [g for g in today_fb if g['status'] == 'live'])
finished_sec = section_html('&#9989; Full Time — Recap Heat',
                            [g for g in today_fb if g['status'] in ('finished', 'postponed')])
upcoming_sec = section_html('&#9200; Kicking Off Soon',
                            [g for g in today_fb if g['status'] == 'upcoming'])
other_sec    = section_html('&#128250; Other Sports — On Israeli TV', other)
yesterday_sec = section_html(f'&#128197; Yesterday ({YESTERDAY}) — Recap Heat',
                             [g for g in yest_fb if g['status'] in ('finished', 'postponed')])

TEMPLATE = open(os.path.join(os.path.dirname(__file__), 'template.html'), encoding='utf-8').read()

html = (TEMPLATE
        .replace('%%LIVE%%',      live_sec)
        .replace('%%FINISHED%%',  finished_sec)
        .replace('%%UPCOMING%%',  upcoming_sec)
        .replace('%%OTHER%%',     other_sec)
        .replace('%%YESTERDAY%%', yesterday_sec)
        .replace('%%TODAY%%',     TODAY)
        .replace('%%SNAPSHOT%%',  SNAPSHOT))

out = os.path.join(os.path.dirname(__file__), '..', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'Wrote index.html ({len(games)} games, source: {source_desc})')
