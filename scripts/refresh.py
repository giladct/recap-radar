#!/usr/bin/env python3
import os, json, re, time, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
IL = timezone(timedelta(hours=3))
now = datetime.now(IL)
TODAY = now.strftime('%B %d, %Y')
SNAPSHOT = now.strftime('%H:%M')

WC_START = datetime(2026, 6, 11, tzinfo=timezone.utc)
WC_END   = datetime(2026, 7, 19, tzinfo=timezone.utc)
ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world'


# ── ESPN data ─────────────────────────────────────────────────────────────────

def fetch_world_cup():
    events = []
    current = WC_START
    while current <= WC_END:
        date_str = current.strftime('%Y%m%d')
        try:
            r = requests.get(f'{ESPN_BASE}/scoreboard?dates={date_str}', timeout=15)
            if r.ok:
                for ev in r.json().get('events', []):
                    parsed = parse_event(ev)
                    if parsed:
                        events.append(parsed)
        except Exception as e:
            print(f'ESPN fetch {date_str} failed: {e}')
        current += timedelta(days=1)
    return events


def parse_event(ev):
    comp = ev['competitions'][0]
    home_c = next((c for c in comp['competitors'] if c['homeAway'] == 'home'), {})
    away_c = next((c for c in comp['competitors'] if c['homeAway'] == 'away'), {})
    home_team = home_c.get('team', {})
    away_team = away_c.get('team', {})

    st = comp.get('status', {}).get('type', {})
    st_name = st.get('name', 'STATUS_SCHEDULED')
    if st_name in ('STATUS_FULL_TIME', 'STATUS_FINAL', 'STATUS_FINAL_AET', 'STATUS_FINAL_PEN'):
        status = 'finished'
    elif st_name in ('STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_END_PERIOD',
                     'STATUS_EXTRA_TIME', 'STATUS_PENALTY'):
        status = 'live'
    elif st_name == 'STATUS_POSTPONED':
        status = 'postponed'
    else:
        status = 'upcoming'

    h_score = home_c.get('score', '')
    a_score = away_c.get('score', '')
    score = f'{a_score}-{h_score}' if h_score != '' and a_score != '' else None

    # Group/round label from notes
    group = ''
    for note in comp.get('notes', []):
        hl = note.get('headline', '')
        if hl:
            group = hl
            break
    if not group:
        group = ev.get('season', {}).get('displayName', 'FIFA World Cup 2026')

    period = st.get('shortDetail', '')

    return {
        'id': str(ev.get('id', '')),
        'league': group,
        'home': home_team.get('displayName', ''),
        'away': away_team.get('displayName', ''),
        'home_flag': home_team.get('flag', {}).get('href', ''),
        'away_flag': away_team.get('flag', {}).get('href', ''),
        'status': status,
        'score': score,
        'period': period,
        'date': ev.get('date', ''),
        'heat': 1,
        'note': '',
        'tv': False,
        'channel': '',
    }


# ── ESPN match summary ────────────────────────────────────────────────────────

def fetch_summary(event_id):
    try:
        r = requests.get(f'{ESPN_BASE}/summary?event={event_id}', timeout=15)
        if not r.ok:
            return {}
        d = r.json()

        # Per-team stats from boxscore
        team_stats = {}
        for ts in d.get('boxscore', {}).get('teams', []):
            abbr = ts.get('team', {}).get('abbreviation', '?')
            team_stats[abbr] = {s['name']: s.get('displayValue', '')
                                for s in ts.get('statistics', [])}

        # Goals and red cards from keyEvents
        goals, reds = [], []
        for ev in d.get('keyEvents', []):
            ev_type = ev.get('type', {}).get('type', '')
            clock   = ev.get('clock', {}).get('displayValue', '?')
            abbr    = ev.get('team', {}).get('abbreviation', '?')
            if ev.get('scoringPlay'):
                suffix = '(pen)' if 'penalty' in ev_type else ''
                goals.append(f"{clock}({abbr}){suffix}")
            elif ev_type in ('red-card', 'yellow-red-card'):
                reds.append(f"{clock}({abbr})")

        result = {'goals': goals, 'reds': reds}
        teams = list(team_stats.keys())
        if len(teams) >= 2:
            def g(t, k): return team_stats.get(t, {}).get(k, '?')
            result['sot']  = {t: g(t, 'shotsOnTarget') for t in teams}
            result['shots'] = {t: g(t, 'totalShots')   for t in teams}
            result['poss'] = {t: g(t, 'possessionPct') for t in teams}
        return result
    except Exception as e:
        print(f'Summary {event_id}: {e}')
        return {}


# ── LLM heat ratings ──────────────────────────────────────────────────────────

def call_llm(prompt):
    for model in ['gpt-4o-mini', 'gpt-4o', 'Llama-3.1-70B-Instruct']:
        for attempt in range(3):
            try:
                r = requests.post(
                    'https://models.inference.ai.azure.com/chat/completions',
                    headers={'Authorization': f'Bearer {GITHUB_TOKEN}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                          'max_tokens': 4000, 'temperature': 0.1},
                    timeout=90
                )
                if r.status_code == 429:
                    time.sleep(15 * (attempt + 1)); continue
                if not r.ok:
                    print(f'{model} HTTP {r.status_code}: {r.text[:200]}'); break
                print(f'LLM OK: {model}')
                return r.json()['choices'][0]['message']['content']
            except Exception as e:
                print(f'{model} error: {e}'); time.sleep(5)
    raise RuntimeError('All models failed')


def rate_finished(games):
    if not games:
        return {}

    print('Fetching match summaries...')
    lines = []
    for g in games:
        s = fetch_summary(g['id'])
        parts = [f'{g["id"]}|{g["away"]} {g["score"] or "?"} {g["home"]}|{g["league"]}']
        if s.get('goals'):
            parts.append('goals:' + ','.join(s['goals']))
        sot   = s.get('sot', {})
        shots = s.get('shots', {})
        if sot:
            parts.append('SoT:' + ' '.join(f'{t}={sot[t]}({shots.get(t,"?")} shots)' for t in sot))
        poss = s.get('poss', {})
        if poss:
            parts.append('poss:' + ' '.join(f'{t}={poss[t]}%' for t in poss))
        if s.get('reds'):
            parts.append('reds:' + ','.join(s['reds']))
        lines.append(' | '.join(parts))

    prompt = f"""FIFA World Cup 2026. Rate each finished match for recap watchability.

Return ONLY raw JSON (no markdown): {{"ratings":[{{"id":"...","heat":2,"note":"under 8 words"}}]}}

heat: 1=unwatchable 2=forgettable 3=decent 4=great game 5=all-time classic
note: max 8 words, no score numbers (e.g. "Stunning comeback in stoppage time")

Key signals: late goals = drama, red cards = chaos, comeback = excitement, high SoT = open game.
Surprising result (underdog wins, heavy favorite loses or barely survives) adds to the rating.
A 0-0 with many chances can rate higher than a comfortable 3-0.

Matches:
{chr(10).join(lines)}"""
    try:
        raw = call_llm(prompt)
        raw = re.sub(r'```json|```', '', raw).strip()
        s, e = raw.find('{'), raw.rfind('}')
        data = json.loads(raw[s:e+1])
        return {r['id']: r for r in data.get('ratings', [])}
    except Exception as ex:
        print(f'Rating failed: {ex}')
        return {}


# ── HTML rendering ────────────────────────────────────────────────────────────

def stars(heat):
    h = max(1, min(5, int(heat or 1)))
    return f'<div class="stars">{"★" * h}<span class="stars-empty">{"☆" * (5 - h)}</span></div>'


def recap_link(away, home):
    q = quote(f'{away} {home} recap highlights')
    return f'<a class="recap-link" href="https://www.youtube.com/results?search_query={q}" target="_blank" rel="noopener">&#9654; Watch recap</a>'


def card_html(g):
    lid    = g.get('id', '')
    league = g.get('league', '')
    home   = g.get('home', '')
    away   = g.get('away', '')
    status = g.get('status', 'upcoming')
    score  = g.get('score') or '?-?'
    period = g.get('period', '')
    heat   = g.get('heat', 1)
    note   = g.get('note', '')

    card_class = 'card finished' if status == 'finished' else 'card'
    attrs = f' data-id="{lid}"' if lid else ''
    if status == 'live':  attrs += ' data-live="true"'

    # Format kickoff time in Israel timezone
    time_str = ''
    date_str = ''
    if g.get('date'):
        try:
            dt = datetime.fromisoformat(g['date'].replace('Z', '+00:00')).astimezone(IL)
            time_str = dt.strftime('%H:%M')
            today_il = datetime.now(IL).date()
            if dt.date() == today_il:
                date_str = 'Today'
            elif dt.date() == today_il + timedelta(days=1):
                date_str = 'Tomorrow'
            else:
                date_str = dt.strftime('%b %d')
            attrs += f' data-started-at="{dt.isoformat()}"'
        except Exception:
            pass

    kickoff = f'{date_str} {time_str}'.strip() if (date_str or time_str) else 'TBD'
    left = (f'<span class="card-left">'
            f'<span class="league">{league}</span>'
            f'<span class="meta">{kickoff}</span>'
            f'</span>')

    if status == 'live':
        right = (f'<span class="live-meta-wrap">'
                 f'<button class="reveal-btn">&#128065; Score</button>'
                 f'<span class="score">{score}</span>'
                 f'<span class="meta live"><span class="dot"></span> {period or "Live"}</span>'
                 f'<span class="started-ago"></span>'
                 f'</span>')
        body = f'{stars(heat)}<div class="note">{note}</div>'
    elif status == 'finished':
        right = (f'<span class="live-meta-wrap">'
                 f'<span class="score-final">{score}</span>'
                 f'<button class="reveal-btn-score">&#128065; Score</button>'
                 f'</span>')
        body = f'{stars(heat)}<div class="note">{note}</div>{recap_link(away, home)}'
    elif status == 'postponed':
        right = ''
        body  = '<div class="note postponed">Postponed.</div>'
    else:
        right = ''
        body  = ''

    return (f'<div class="{card_class}"{attrs}>\n'
            f'  <div class="card-top">{left}{right}</div>\n'
            f'  <div class="teams">{away} vs {home}</div>\n'
            f'  {body}\n'
            f'</div>')


def section_html(title, games):
    if not games:
        return ''
    cards = '\n'.join(card_html(g) for g in games)
    return f'<div class="section">\n  <div class="section-title">{title}</div>\n{cards}\n</div>\n'


# ── main ──────────────────────────────────────────────────────────────────────

print('Fetching World Cup schedule from ESPN...')
games = fetch_world_cup()
statuses = {s: sum(1 for g in games if g['status'] == s) for s in set(g['status'] for g in games)}
print(f'Fetched {len(games)} games: {statuses}')

# Apply heat ratings to finished games
finished = [g for g in games if g['status'] == 'finished']
if finished:
    print(f'Rating {len(finished)} finished games...')
    ratings = rate_finished(finished)
    for g in finished:
        r = ratings.get(g['id'], {})
        g['heat']  = r.get('heat', 1)
        g['note']  = r.get('note', '')

live_sec     = section_html('<span class="dot"></span> Live Now',
                            [g for g in games if g['status'] == 'live'])
finished_sec = section_html('&#9989; Full Time — Recap Heat',
                            [g for g in games if g['status'] in ('finished', 'postponed')])
upcoming_sec = section_html('&#9200; Upcoming Matches',
                            [g for g in games if g['status'] == 'upcoming'])

TEMPLATE = open(os.path.join(os.path.dirname(__file__), 'template.html'), encoding='utf-8').read()

html = (TEMPLATE
        .replace('%%LIVE%%',     live_sec)
        .replace('%%FINISHED%%', finished_sec)
        .replace('%%UPCOMING%%', upcoming_sec)
        .replace('%%TODAY%%',    TODAY)
        .replace('%%SNAPSHOT%%', SNAPSHOT))

# Remove placeholders that no longer apply
for ph in ['%%OTHER%%', '%%YESTERDAY%%']:
    html = html.replace(ph, '')

out = os.path.join(os.path.dirname(__file__), '..', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'Wrote index.html ({len(games)} games)')
