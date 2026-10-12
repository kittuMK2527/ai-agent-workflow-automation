"""Design tokens and the global stylesheet: curated palettes x light/dark.

Colour roles are CSS variables, so every component (and Streamlit's own widgets, restyled below) follows one palette.

* **Neutrals** (page, surfaces, text, status colours) depend only on light/dark. Light/dark comes from
  ``st.context.theme.type`` (Settings -> Theme), so the page follows the user's Streamlit theme.
* **Accent palette** (Indigo, Emerald, Champagne, Sunset) is chosen in the sidebar and sets the hero gradient,
  buttons, highlights and ambient glow.

Accessibility is part of the design, not an afterthought: text colours (``--accent-text``) and the text on filled
accent surfaces (``--on-accent`` over ``--btn-from``/``--btn-to``) are chosen to meet WCAG AA (4.5:1), and the
white hero text is checked against every hero gradient stop. ``tests/test_ui_components.py`` verifies all of it for
every palette in both modes. The stylesheet makes no external requests, and motion is disabled for users who ask for
reduced motion.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- neutrals
LIGHT = """
  --bg:#F4F6FB; --surface:#FFFFFF; --surface-2:#F7F9FD; --surface-3:#EEF1F8; --card-top:#FFFFFF; --card-bottom:#FBFCFF;
  --border:#E2E7F1; --border-strong:#C9D2E4;
  --text:#0C1424; --muted:#4F5A74; --faint:#626C84;
  --success:#0A7450; --success-bg:#E5F6EE; --success-bd:#B5E3CE;
  --warning:#A5500A; --warning-bg:#FEF2D9; --warning-bd:#F4D395;
  --danger:#BE2434; --danger-bg:#FCE9EB; --danger-bd:#F3BFC5;
  --info:#2150CC; --info-bg:#E8EFFD; --info-bd:#BCCFF5;
  --violet:#6B3FD1; --violet-bg:#EFE9FD; --violet-bd:#D3C5F6;
  --neutral-bg:#EDF0F7; --neutral-bd:#DCE2EE;
  --shadow-sm:0 1px 2px rgba(12,20,36,.06), 0 1px 1px rgba(12,20,36,.03);
  --shadow-md:0 14px 34px -14px rgba(40,50,130,.30), 0 3px 8px rgba(12,20,36,.05);
  --shadow-lg:0 34px 70px -28px rgba(30,40,110,.42), 0 8px 18px rgba(12,20,36,.06);
  --code-bg:#F1F3FA;
"""

DARK = """
  --bg:#070B18; --surface:#0F1730; --surface-2:#0B1226; --surface-3:#16203F; --card-top:#141F42; --card-bottom:#0F1730;
  --border:#212C50; --border-strong:#33426F;
  --text:#E8EDFB; --muted:#A7B2CF; --faint:#8695BA;
  --success:#4ADE9A; --success-bg:rgba(34,197,94,.13); --success-bd:rgba(74,222,154,.34);
  --warning:#FBBF5C; --warning-bg:rgba(251,191,92,.12); --warning-bd:rgba(251,191,92,.34);
  --danger:#FF8A94; --danger-bg:rgba(255,99,115,.13); --danger-bd:rgba(255,138,148,.36);
  --info:#86A9FF; --info-bg:rgba(99,140,255,.14); --info-bd:rgba(134,169,255,.36);
  --violet:#BBA2FF; --violet-bg:rgba(167,139,250,.15); --violet-bd:rgba(187,162,255,.36);
  --neutral-bg:rgba(255,255,255,.06); --neutral-bd:rgba(255,255,255,.12);
  --shadow-sm:0 1px 2px rgba(0,0,0,.35);
  --shadow-md:0 18px 40px -16px rgba(0,0,0,.65), 0 3px 10px rgba(0,0,0,.35);
  --shadow-lg:0 40px 80px -30px rgba(0,0,0,.8), 0 8px 20px rgba(0,0,0,.4);
  --code-bg:#0A1124;
"""

# --------------------------------------------------------------------------- palettes
# accent/accent2/accent3: fills, glows and decorative colour.  text: accent-coloured TEXT (AA on the surfaces).
# btn_from/btn_to: gradient behind filled controls; on: the text colour used on them (AA on both stops).
PALETTES: dict[str, dict] = {
    "Indigo": {
        "dot": "🟣",
        "tagline": "Calm, technical, trustworthy",
        "hero": ("#0E1136", "#252082", "#4A35C9"),
        "glow": ("rgba(167,139,250,.60)", "rgba(56,189,248,.42)"),
        "light": dict(accent="#5148E8", accent2="#8B5CF6", accent3="#0EA5E9", text="#4338CA", btn_from="#4F46E5", btn_to="#7C3AED", on="#FFFFFF",
                      bgglow=("rgba(99,102,241,.13)", "rgba(14,165,233,.10)")),
        "dark": dict(accent="#8B8BFF", accent2="#B197FC", accent3="#38BDF8", text="#A5A5FF", btn_from="#5B54EE", btn_to="#7C3AED", on="#FFFFFF",
                     bgglow=("rgba(99,102,241,.24)", "rgba(14,165,233,.13)")),
    },
    "Emerald": {
        "dot": "🟢",
        "tagline": "Fresh, growth, finance",
        "hero": ("#04201C", "#0A4A42", "#0D6B64"),
        "glow": ("rgba(52,211,153,.50)", "rgba(34,211,238,.38)"),
        "light": dict(accent="#0F9D77", accent2="#14B8A6", accent3="#38BDF8", text="#0B6B53", btn_from="#047857", btn_to="#0F766E", on="#FFFFFF",
                      bgglow=("rgba(16,185,129,.13)", "rgba(56,189,248,.10)")),
        "dark": dict(accent="#34D399", accent2="#2DD4BF", accent3="#38BDF8", text="#5EEAD4", btn_from="#047857", btn_to="#0F766E", on="#FFFFFF",
                     bgglow=("rgba(16,185,129,.22)", "rgba(56,189,248,.12)")),
    },
    "Champagne": {
        "dot": "🟡",
        "tagline": "Refined, executive, luxury",
        "hero": ("#0A0F1F", "#1B2447", "#4F3B12"),
        "glow": ("rgba(245,196,81,.52)", "rgba(99,102,241,.30)"),
        "light": dict(accent="#C28A1B", accent2="#E3B341", accent3="#F59E0B", text="#7A5200", btn_from="#D9A441", btn_to="#F0CB73", on="#2B1B00",
                      bgglow=("rgba(224,169,58,.15)", "rgba(99,102,241,.07)")),
        "dark": dict(accent="#F2C14E", accent2="#FFD98A", accent3="#F59E0B", text="#F5CF6B", btn_from="#D9A441", btn_to="#F0CB73", on="#2B1B00",
                     bgglow=("rgba(224,169,58,.20)", "rgba(99,102,241,.13)")),
    },
    "Sunset": {
        "dot": "🟠",
        "tagline": "Warm, energetic, creative",
        "hero": ("#240818", "#701340", "#A32F0C"),
        "glow": ("rgba(251,113,133,.50)", "rgba(251,191,36,.35)"),
        "light": dict(accent="#EA580C", accent2="#F59E0B", accent3="#F43F5E", text="#9A3412", btn_from="#C2410C", btn_to="#B45309", on="#FFFFFF",
                      bgglow=("rgba(249,115,22,.12)", "rgba(244,63,94,.08)")),
        "dark": dict(accent="#FB923C", accent2="#FBBF24", accent3="#FB7185", text="#FDBA74", btn_from="#C2410C", btn_to="#B45309", on="#FFFFFF",
                     bgglow=("rgba(249,115,22,.20)", "rgba(244,63,94,.12)")),
    },
}
DEFAULT_PALETTE = "Indigo"
PALETTE_NAMES = tuple(PALETTES)


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def palette_tokens(name: str, dark: bool) -> str:
    """CSS variable declarations for the accent palette ``name`` (falls back to the default)."""
    p = PALETTES.get(name) or PALETTES[DEFAULT_PALETTE]
    c = p["dark" if dark else "light"]
    soft, ring = (0.18, 0.34) if dark else (0.10, 0.24)
    h1, h2, h3 = p["hero"]
    return (
        f"--accent:{c['accent']}; --accent-2:{c['accent2']}; --accent-3:{c['accent3']}; --accent-text:{c['text']};"
        f" --accent-soft:{_rgba(c['accent'], soft)}; --accent-ring:{_rgba(c['accent'], ring)};"
        f" --btn-from:{c['btn_from']}; --btn-to:{c['btn_to']}; --on-accent:{c['on']};"
        f" --hero-1:{h1}; --hero-2:{h2}; --hero-3:{h3}; --glow-1:{p['glow'][0]}; --glow-2:{p['glow'][1]};"
        f" --bg-glow-1:{c['bgglow'][0]}; --bg-glow-2:{c['bgglow'][1]};"
    )


CSS = """
:root {
  /*TOKENS*/
  --radius:20px; --radius-md:15px; --radius-sm:11px;
  --font:-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, Inter, "Helvetica Neue", Arial, sans-serif;
  --mono:ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --btn-grad:linear-gradient(135deg, var(--btn-from), var(--btn-to));
}

/* ---------- page: ambient aurora background ---------- */
html, body, .stApp, [data-testid="stAppViewContainer"] { font-family:var(--font) !important; -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }
.stApp { color:var(--text);
  background: radial-gradient(1100px 520px at 6% -12%, var(--bg-glow-1), transparent 62%), radial-gradient(900px 480px at 104% -8%, var(--bg-glow-2), transparent 58%), var(--bg);
  background-attachment: fixed; }
[data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stHeader"] { background:transparent !important; }
[data-testid="stAppDeployButton"], [data-testid="stStatusWidget"], footer, [data-testid="stFooter"] { display:none !important; }
.block-container { max-width:1240px; padding-top:1.1rem; padding-bottom:3rem; }
h1, h2, h3, h4 { letter-spacing:-.015em; }
p, li { color:var(--text); }
.stApp a { color:var(--accent-text); }
::selection { background:var(--accent-ring); }
:focus-visible { outline:2px solid var(--accent) !important; outline-offset:2px; border-radius:6px; }
.sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
.muted { color:var(--muted); font-size:.9rem; }
code, .mono { font-family:var(--mono); }

/* ---------- hero ---------- */
.hero { position:relative; overflow:hidden; border-radius:28px; padding:34px 38px 28px; color:#fff; isolation:isolate; margin-bottom:26px;
  background:
    radial-gradient(900px 440px at 90% -30%, var(--glow-1), transparent 62%),
    radial-gradient(720px 380px at -6% 128%, var(--glow-2), transparent 60%),
    linear-gradient(135deg, var(--hero-1) 0%, var(--hero-2) 54%, var(--hero-3) 100%);
  box-shadow:var(--shadow-lg); border:1px solid rgba(255,255,255,.10); }
.hero::before { content:""; position:absolute; inset:0; z-index:-1; opacity:.85;
  background-image:linear-gradient(rgba(255,255,255,.055) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.055) 1px, transparent 1px); background-size:44px 44px;
  -webkit-mask-image:linear-gradient(115deg, rgba(0,0,0,.9), transparent 62%); mask-image:linear-gradient(115deg, rgba(0,0,0,.9), transparent 62%); }
.orb { position:absolute; border-radius:50%; filter:blur(46px); opacity:.55; z-index:-1; pointer-events:none; }
.orb-a { width:340px; height:340px; right:-60px; top:-120px; background:var(--glow-1); animation:drift 16s ease-in-out infinite alternate; }
.orb-b { width:280px; height:280px; left:30%; bottom:-170px; background:var(--glow-2); animation:drift 19s ease-in-out infinite alternate-reverse; }
@keyframes drift { from { transform:translate(0,0) scale(1); } to { transform:translate(-70px,46px) scale(1.14); } }
.hero-eyebrow { display:inline-flex; align-items:center; gap:8px; font-size:.72rem; font-weight:720; letter-spacing:.16em; text-transform:uppercase; color:rgba(255,255,255,.80);
  padding:5px 12px 5px 10px; border-radius:999px; background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.18); }
.hero .hero-title { margin:.8rem 0 0 !important; padding:0 !important; font-size:clamp(1.9rem, 3.2vw, 2.8rem) !important; line-height:1.04 !important;
  letter-spacing:-.032em !important; font-weight:780 !important; color:#fff !important; text-shadow:0 2px 24px rgba(0,0,0,.28); }
.hero .hero-sub { max-width:70ch; margin:.85rem 0 0; color:rgba(255,255,255,.82) !important; font-size:1.04rem; line-height:1.6; }
.hero-chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:20px; }
.glass-chip { display:inline-flex; align-items:center; gap:7px; padding:6px 13px; border-radius:999px; font-size:.8rem; font-weight:640; color:#fff;
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.26); -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px); }
.g-dot { width:8px; height:8px; border-radius:50%; background:#cbd5e1; box-shadow:0 0 0 3px rgba(255,255,255,.14); }
.g-dot.success { background:#4ade9a; box-shadow:0 0 0 3px rgba(74,222,154,.25); } .g-dot.warning { background:#fbbf5c; box-shadow:0 0 0 3px rgba(251,191,92,.25); }
.g-dot.danger { background:#ff8a94; } .g-dot.violet { background:#c4b5fd; } .g-dot.info { background:#93c5fd; }
.hero-kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:14px; margin-top:26px; }
.hero-kpi { display:flex; align-items:center; gap:13px; padding:14px 16px; border-radius:18px; background:rgba(255,255,255,.09); border:1px solid rgba(255,255,255,.18);
  -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px); transition:transform .18s ease, background .18s ease; }
.hero-kpi:hover { transform:translateY(-2px); background:rgba(255,255,255,.13); }
.kpi-ic { width:38px; height:38px; flex:none; border-radius:12px; display:grid; place-items:center; background:rgba(255,255,255,.15); color:#fff; }
.hero-kpi b { display:block; font-size:1.7rem; line-height:1.05; letter-spacing:-.03em; font-variant-numeric:tabular-nums; }
.hero-kpi span.k { display:block; margin-top:3px; font-size:.68rem; letter-spacing:.1em; text-transform:uppercase; color:rgba(255,255,255,.78); font-weight:650; }

/* ---------- section headings ---------- */
.sec { display:flex; align-items:center; gap:13px; margin:34px 0 15px; }
.sec::after { content:""; flex:1; height:1px; margin-left:8px; background:linear-gradient(90deg, var(--border-strong), transparent); opacity:.7; }
.sec-num, .sec-ic { width:34px; height:34px; border-radius:12px; display:grid; place-items:center; flex:none; font-weight:800; font-size:.88rem; color:var(--on-accent);
  background:var(--btn-grad); box-shadow:0 10px 22px -10px var(--btn-from); }
.sec-title { font-size:1.2rem; font-weight:730; letter-spacing:-.018em; color:var(--text); }
.sec-sub { color:var(--muted); font-size:.86rem; margin-top:1px; }

/* ---------- cards (Streamlit containers keyed card_*): gradient edge ---------- */
[class*="st-key-card_"] { padding:1.2rem 1.35rem 1.25rem; border-radius:var(--radius); border:1px solid transparent; box-shadow:var(--shadow-sm);
  background:linear-gradient(180deg, var(--card-top), var(--card-bottom)) padding-box,
             linear-gradient(145deg, var(--accent-ring), var(--border) 34%, var(--border) 66%, var(--accent-ring)) border-box; }

/* ---------- chips ---------- */
.chips { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
.chip { display:inline-flex; align-items:center; gap:5px; padding:2px 10px; border-radius:999px; font-size:.74rem; font-weight:650; line-height:1.6;
  border:1px solid var(--neutral-bd); background:var(--neutral-bg); color:var(--muted); white-space:nowrap; }
.chip.mono { font-family:var(--mono); font-weight:600; font-size:.72rem; }
.chip .dot { width:7px; height:7px; border-radius:50%; background:currentColor; }
.chip.success { background:var(--success-bg); border-color:var(--success-bd); color:var(--success); }
.chip.warning { background:var(--warning-bg); border-color:var(--warning-bd); color:var(--warning); }
.chip.danger { background:var(--danger-bg); border-color:var(--danger-bd); color:var(--danger); }
.chip.info { background:var(--info-bg); border-color:var(--info-bd); color:var(--info); }
.chip.accent { background:var(--accent-soft); border-color:var(--accent-ring); color:var(--accent-text); }
.chip.violet { background:var(--violet-bg); border-color:var(--violet-bd); color:var(--violet); }
.ic { flex:none; vertical-align:-2px; }

/* ---------- outcome strip ---------- */
@keyframes rise { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
.outcome { position:relative; overflow:hidden; display:grid; grid-template-columns:60px 1fr; gap:20px; align-items:center; padding:22px 24px; border-radius:24px;
  border:1px solid var(--border); box-shadow:var(--shadow-md); margin:6px 0 4px; animation:rise .4s ease both; }
.outcome::after { content:""; position:absolute; width:300px; height:300px; right:-90px; top:-140px; border-radius:50%; opacity:.5; pointer-events:none; background:radial-gradient(circle, var(--o-glow, transparent), transparent 68%); }
.outcome .o-ic { width:60px; height:60px; border-radius:20px; display:grid; place-items:center; position:relative; z-index:1; }
.o-body { position:relative; z-index:1; }
.o-label { font-size:.72rem; font-weight:780; letter-spacing:.13em; text-transform:uppercase; }
.o-headline { font-size:clamp(1.15rem, 1.8vw, 1.5rem); font-weight:750; letter-spacing:-.02em; margin-top:4px; line-height:1.28; color:var(--text); overflow-wrap:anywhere; }
.o-line { color:var(--muted); margin-top:5px; font-size:.92rem; }
.o-chips { margin-top:14px; }
.outcome.success { --o-glow:var(--success-bd); background:linear-gradient(120deg, var(--success-bg), var(--card-top) 72%); border-color:var(--success-bd); }
.outcome.success .o-ic { background:linear-gradient(135deg, var(--success), #12A073); color:#fff; box-shadow:0 14px 28px -12px var(--success); } .outcome.success .o-label { color:var(--success); }
.outcome.warning { --o-glow:var(--warning-bd); background:linear-gradient(120deg, var(--warning-bg), var(--card-top) 72%); border-color:var(--warning-bd); }
.outcome.warning .o-ic { background:linear-gradient(135deg, #B45309, #D97706); color:#fff; box-shadow:0 14px 28px -12px var(--warning); } .outcome.warning .o-label { color:var(--warning); }
.outcome.danger { --o-glow:var(--danger-bd); background:linear-gradient(120deg, var(--danger-bg), var(--card-top) 72%); border-color:var(--danger-bd); }
.outcome.danger .o-ic { background:linear-gradient(135deg, var(--danger), #E5484D); color:#fff; box-shadow:0 14px 28px -12px var(--danger); } .outcome.danger .o-label { color:var(--danger); }
.outcome.info { --o-glow:var(--info-bd); background:linear-gradient(120deg, var(--info-bg), var(--card-top) 72%); border-color:var(--info-bd); }
.outcome.info .o-ic { background:linear-gradient(135deg, var(--info), #4F7BF0); color:#fff; box-shadow:0 14px 28px -12px var(--info); } .outcome.info .o-label { color:var(--info); }
.outcome.neutral { background:linear-gradient(180deg, var(--card-top), var(--card-bottom)); } .outcome.neutral .o-ic { background:var(--surface-3); color:var(--muted); } .outcome.neutral .o-label { color:var(--muted); }
.outcome .o-ic svg { color:#fff; } .outcome.neutral .o-ic svg { color:var(--muted); }

/* ---------- callouts ---------- */
.callout { display:flex; gap:12px; padding:13px 16px; border-radius:15px; border:1px solid var(--neutral-bd); background:var(--neutral-bg); margin:8px 0; }
.callout .co-ic { margin-top:2px; } .co-title { font-weight:690; } .co-text { color:var(--muted); font-size:.9rem; margin-top:2px; overflow-wrap:anywhere; }
.co-list { margin:6px 0 0 1.1rem; padding:0; color:var(--muted); font-size:.9rem; }
.callout.success { background:var(--success-bg); border-color:var(--success-bd); } .callout.success .co-ic, .callout.success .co-title { color:var(--success); }
.callout.warning { background:var(--warning-bg); border-color:var(--warning-bd); } .callout.warning .co-ic, .callout.warning .co-title { color:var(--warning); }
.callout.danger { background:var(--danger-bg); border-color:var(--danger-bd); } .callout.danger .co-ic, .callout.danger .co-title { color:var(--danger); }
.callout.info { background:var(--info-bg); border-color:var(--info-bd); } .callout.info .co-ic, .callout.info .co-title { color:var(--info); }

/* ---------- pipeline ---------- */
.pipe { list-style:none; margin:0; padding:0; }
.pipe-row { display:grid; grid-template-columns:42px 1fr; gap:15px; position:relative; padding-bottom:20px; }
.pipe-row:last-child { padding-bottom:0; }
.pipe-row::before { content:""; position:absolute; left:20px; top:44px; bottom:2px; width:2px; background:var(--border); border-radius:2px; }
.pipe-row:last-child::before { display:none; }
.pipe-row.done::before { background:linear-gradient(var(--success), var(--border)); }
.pipe-node { width:42px; height:42px; border-radius:14px; display:grid; place-items:center; background:var(--surface-2); border:1px solid var(--border); color:var(--faint); transition:all .25s ease; }
.pipe-row.done .pipe-node { background:var(--success-bg); border-color:var(--success-bd); color:var(--success); box-shadow:0 8px 18px -10px var(--success); }
.pipe-row.attention .pipe-node { background:var(--warning-bg); border-color:var(--warning-bd); color:var(--warning); animation:pulse 2.2s ease-in-out infinite; }
.pipe-row.error .pipe-node { background:var(--danger-bg); border-color:var(--danger-bd); color:var(--danger); box-shadow:0 8px 18px -10px var(--danger); }
@keyframes pulse { 0%,100% { box-shadow:0 0 0 0 transparent; } 50% { box-shadow:0 0 0 7px var(--warning-bg); } }
.pipe-title { font-weight:690; font-size:.96rem; padding-top:3px; }
.pipe-detail { color:var(--muted); font-size:.82rem; margin-top:1px; overflow-wrap:anywhere; }
.pipe-row.idle .pipe-title { color:var(--muted); font-weight:630; } .pipe-row.idle .pipe-detail { color:var(--faint); }

/* ---------- timeline ---------- */
.tl-wrap { max-height:660px; overflow:auto; padding:2px 8px 2px 2px; }
.tl { list-style:none; margin:0; padding:0; }
.tl-item { display:grid; grid-template-columns:34px 1fr; gap:13px; position:relative; padding-bottom:12px; }
.tl-item::before { content:""; position:absolute; left:16px; top:34px; bottom:0; width:2px; background:var(--border); }
.tl-item:last-child { padding-bottom:0; } .tl-item:last-child::before { display:none; }
.tl-dot { width:34px; height:34px; border-radius:50%; display:grid; place-items:center; background:var(--surface); border:2px solid var(--border-strong); color:var(--faint); z-index:1; }
.tl-card { background:linear-gradient(180deg, var(--card-top), var(--card-bottom)); border:1px solid var(--border); border-radius:15px; padding:10px 15px; box-shadow:var(--shadow-sm); transition:box-shadow .16s ease, border-color .16s ease, transform .16s ease; }
.tl-card:hover { box-shadow:var(--shadow-md); border-color:var(--accent-ring); transform:translateX(2px); }
.tl-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.tl-name { font-weight:670; letter-spacing:-.006em; }
.tl-meta { margin-left:auto; color:var(--faint); font-size:.78rem; font-variant-numeric:tabular-nums; }
.tl-msg { color:var(--muted); font-size:.84rem; margin-top:4px; overflow-wrap:anywhere; } .tl-msg.danger { color:var(--danger); font-weight:570; }
.tl-item.completed .tl-dot { background:var(--success-bg); border-color:var(--success); color:var(--success); box-shadow:0 0 0 4px var(--success-bg); }
.tl-item.failed .tl-dot { background:var(--danger-bg); border-color:var(--danger); color:var(--danger); box-shadow:0 0 0 4px var(--danger-bg); }
.tl-item.failed .tl-card { border-color:var(--danger-bd); }
.tl-item.awaiting_input .tl-dot { background:var(--warning-bg); border-color:var(--warning); color:var(--warning); box-shadow:0 0 0 4px var(--warning-bg); }
.tl-item.awaiting_input .tl-card { border-color:var(--warning-bd); background:linear-gradient(var(--warning-bg), var(--card-bottom)); }
.tl-item.running .tl-dot { background:var(--info-bg); border-color:var(--info); color:var(--info); }
.tl-item.skipped .tl-card, .tl-item.pending .tl-card { opacity:.62; box-shadow:none; background:var(--surface-2); }
.tl-item.completed::before { background:var(--success-bd); }

/* ---------- KPI cards ---------- */
.kpis { display:grid; grid-template-columns:repeat(auto-fill, minmax(176px,1fr)); gap:13px; margin:4px 0 6px; }
.kpi { padding:16px 18px; border-radius:18px; border:1px solid transparent; box-shadow:var(--shadow-sm); position:relative; overflow:hidden; transition:transform .16s ease, box-shadow .16s ease;
  background:linear-gradient(180deg, var(--card-top), var(--card-bottom)) padding-box, linear-gradient(145deg, var(--accent-ring), var(--border) 45%, var(--border)) border-box; }
.kpi:hover { transform:translateY(-2px); box-shadow:var(--shadow-md); }
.kpi::before { content:""; position:absolute; left:0; top:0; right:0; height:3px; background:linear-gradient(90deg, var(--accent), var(--accent-2), var(--accent-3)); opacity:.9; }
.k-label { font-size:.7rem; text-transform:uppercase; letter-spacing:.1em; color:var(--faint); font-weight:730; }
.k-value { font-size:1.7rem; font-weight:770; letter-spacing:-.03em; font-variant-numeric:tabular-nums; margin-top:6px; overflow-wrap:anywhere; line-height:1.2; color:var(--text); }
.k-hint { color:var(--muted); font-size:.78rem; margin-top:3px; }

/* ---------- selection panel ---------- */
.wf-title { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
.wf-id { font-family:var(--mono); font-size:.74rem; font-weight:730; color:var(--accent-text); background:var(--accent-soft); border:1px solid var(--accent-ring); border-radius:9px; padding:2px 9px; }
.wf-name-lg { font-size:1.3rem; font-weight:750; letter-spacing:-.022em; }
.why { margin:12px 0 0; padding:11px 15px; border-left:3px solid var(--accent); background:var(--accent-soft); border-radius:0 13px 13px 0; color:var(--text); font-size:.92rem; overflow-wrap:anywhere; }
.meter-row { display:flex; align-items:center; gap:10px; margin-top:13px; }
.meter { flex:1; height:9px; background:var(--surface-3); border-radius:999px; overflow:hidden; box-shadow:inset 0 1px 2px rgba(0,0,0,.08); }
.meter span { display:block; height:100%; border-radius:999px; background:linear-gradient(90deg, var(--accent), var(--accent-2), var(--accent-3)); }
.meter-row b { font-variant-numeric:tabular-nums; font-size:.86rem; }
.kvs { display:flex; flex-direction:column; gap:8px; }
.kv { display:grid; grid-template-columns:minmax(80px, 34%) 1fr auto; gap:10px; align-items:center; padding:9px 13px; border-radius:13px; background:var(--surface-2); border:1px solid var(--border); }
.kv-k { color:var(--muted); font-size:.8rem; font-weight:630; text-transform:capitalize; } .kv-v { font-weight:630; overflow-wrap:anywhere; font-size:.9rem; }

/* ---------- catalog cards ---------- */
.wf-card { position:relative; overflow:hidden; height:100%; min-height:216px; display:flex; flex-direction:column; padding:17px 19px; border-radius:var(--radius); border:1px solid transparent; box-shadow:var(--shadow-sm);
  transition:transform .18s ease, box-shadow .18s ease;
  background:linear-gradient(180deg, var(--card-top), var(--card-bottom)) padding-box, linear-gradient(145deg, var(--accent-ring), var(--border) 40%, var(--border)) border-box; }
.wf-card::before { content:""; position:absolute; left:0; right:0; top:0; height:3px; background:linear-gradient(90deg, var(--accent), var(--accent-2), var(--accent-3)); opacity:0; transition:opacity .18s ease; }
.wf-card:hover { transform:translateY(-3px); box-shadow:var(--shadow-md); } .wf-card:hover::before, .wf-card.selected::before { opacity:1; }
.wf-card.selected { box-shadow:0 0 0 4px var(--accent-soft), var(--shadow-md);
  background:linear-gradient(180deg, var(--card-top), var(--card-bottom)) padding-box, linear-gradient(135deg, var(--accent), var(--accent-2)) border-box; }
.wf-top { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.wf-lead { display:flex; align-items:center; gap:9px; }
.wf-ic { width:30px; height:30px; border-radius:10px; display:grid; place-items:center; background:var(--accent-soft); color:var(--accent-text); border:1px solid var(--accent-ring); }
.wf-tools { display:inline-flex; gap:5px; align-items:center; color:var(--faint); font-size:.72rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:58%; }
.wf-name { font-weight:750; font-size:1.06rem; letter-spacing:-.018em; margin:13px 0 4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.wf-trigger { color:var(--muted); font-size:.85rem; min-height:2.8em; line-height:1.4; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.wf-req { color:var(--muted); font-size:.78rem; margin:8px 0 10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; } .wf-req b { color:var(--text); font-weight:670; }
.wf-card .chips { margin-top:auto; }

/* ---------- excel / spec views ---------- */
.excel { border:1px solid var(--border); border-radius:var(--radius-md); overflow:hidden; background:var(--surface); box-shadow:var(--shadow-sm); }
.ex-row { display:grid; grid-template-columns:160px 1fr; border-bottom:1px solid var(--border); } .ex-row:last-child { border-bottom:0; }
.ex-k { padding:11px 15px; background:linear-gradient(90deg, var(--accent-soft), var(--surface-2)); color:var(--accent-text); font-weight:720; font-size:.76rem; text-transform:uppercase; letter-spacing:.08em; }
.ex-v { padding:11px 15px; font-size:.92rem; overflow-wrap:anywhere; }
.speclist { margin:4px 0 0 1.1rem; padding:0; color:var(--text); font-size:.9rem; } .speclist li { margin:5px 0; color:var(--text); }
code.expr { display:inline-block; font-family:var(--mono); font-size:.8rem; background:var(--code-bg); border:1px solid var(--border); padding:1px 8px; border-radius:8px; color:var(--text); overflow-wrap:anywhere; }

/* ---------- result sub-headings ---------- */
.subhead { display:flex; align-items:baseline; gap:10px; margin:20px 0 9px; }
.subhead-title { font-weight:720; font-size:1.02rem; letter-spacing:-.012em; } .subhead-meta { color:var(--faint); font-size:.8rem; font-variant-numeric:tabular-nums; }

/* ---------- empty state + how it works ---------- */
.empty { text-align:center; padding:26px 20px; border:1.5px dashed var(--border-strong); border-radius:var(--radius); background:var(--surface-2); }
.empty-ic { display:inline-grid; place-items:center; width:48px; height:48px; border-radius:16px; background:var(--accent-soft); color:var(--accent-text); margin-bottom:10px; }
.empty-title { font-weight:710; font-size:1.04rem; } .empty-text { color:var(--muted); font-size:.9rem; margin-top:4px; }
.how { display:grid; grid-template-columns:repeat(auto-fit, minmax(230px,1fr)); gap:14px; margin-top:6px; }
.how-card { position:relative; padding:20px 20px 18px; border-radius:var(--radius); border:1px solid transparent; box-shadow:var(--shadow-sm); transition:transform .18s ease, box-shadow .18s ease;
  background:linear-gradient(180deg, var(--card-top), var(--card-bottom)) padding-box, linear-gradient(145deg, var(--accent-ring), var(--border) 45%, var(--border)) border-box; }
.how-card:hover { transform:translateY(-3px); box-shadow:var(--shadow-md); }
.how-n { position:absolute; right:16px; top:12px; font-size:2.4rem; font-weight:800; letter-spacing:-.04em; color:var(--accent-soft); line-height:1; }
.how-ic { display:grid; place-items:center; width:42px; height:42px; border-radius:14px; background:var(--btn-grad); color:var(--on-accent); box-shadow:0 12px 24px -12px var(--btn-from); }
.how-t { font-weight:720; font-size:1.02rem; margin-top:13px; letter-spacing:-.012em; } .how-d { color:var(--muted); font-size:.88rem; margin-top:4px; line-height:1.5; }

/* ---------- sidebar chrome ---------- */
.brand { display:flex; align-items:center; gap:12px; padding:6px 2px 16px; }
.brand-mark { width:44px; height:44px; border-radius:15px; display:grid; place-items:center; color:var(--on-accent); background:var(--btn-grad); box-shadow:0 12px 26px -12px var(--btn-from); }
.brand-name { font-weight:770; letter-spacing:-.022em; font-size:1.06rem; } .brand-sub { color:var(--muted); font-size:.76rem; }
.side-label { font-size:.7rem; font-weight:770; letter-spacing:.13em; text-transform:uppercase; color:var(--faint); margin:18px 0 7px; }
.pstrip { margin:10px 0 2px; } .ps-bar { display:block; height:10px; border-radius:999px; box-shadow:0 6px 14px -6px var(--btn-from), inset 0 0 0 1px rgba(255,255,255,.25); }
.ps-tag { display:block; margin-top:7px; color:var(--muted); font-size:.78rem; }
.foot { text-align:center; color:var(--faint); font-size:.78rem; padding:30px 0 4px; }

/* ---------- Streamlit widgets, restyled ---------- */
[data-testid="stSidebar"] { background:linear-gradient(180deg, var(--card-top), var(--surface-2)); border-right:1px solid var(--border); }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top:.6rem; }
[data-testid="stWidgetLabel"] p, label[data-testid="stWidgetLabel"] { font-weight:650; font-size:.86rem; color:var(--text); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color:var(--muted); }

[data-testid="stTabs"] [role="tablist"] { gap:4px; background:var(--surface-3); border:1px solid var(--border); padding:5px; border-radius:17px; width:fit-content; max-width:100%; overflow-x:auto; margin-bottom:14px; box-shadow:inset 0 1px 2px rgba(0,0,0,.04); }
[data-testid="stTabs"] .react-aria-SelectionIndicator, [data-testid="stTabs"] [role="tablist"]::after { display:none !important; }
[data-testid="stTabs"] [role="tablist"] > div[aria-hidden="true"], [data-testid="stTabs"] [data-baseweb="tab-highlight"], [data-testid="stTabs"] [data-baseweb="tab-border"] { display:none !important; }
[data-testid="stTab"] { padding:9px 17px !important; height:auto !important; border-radius:13px; color:var(--muted); font-weight:650; transition:background .16s ease, color .16s ease, box-shadow .16s ease; }
[data-testid="stTab"] p { font-weight:650; font-size:.92rem; }
[data-testid="stTab"]:hover { color:var(--text); }
[data-testid="stTab"][aria-selected="true"] { background:var(--surface); color:var(--text); box-shadow:var(--shadow-sm), 0 0 0 1px var(--accent-ring); }
[data-testid="stTab"][aria-selected="true"] p { color:var(--text); }

[data-testid="stBaseButton-primary"] { position:relative; overflow:hidden; background:var(--btn-grad) !important; border:0 !important; color:var(--on-accent) !important; border-radius:14px !important;
  font-weight:700 !important; padding:.66rem 1.35rem !important; box-shadow:0 14px 30px -14px var(--btn-from), inset 0 1px 0 rgba(255,255,255,.22); transition:transform .16s ease, filter .16s ease, box-shadow .16s ease; }
[data-testid="stBaseButton-primary"] p { color:var(--on-accent) !important; font-weight:700; white-space:nowrap; }
[data-testid="stBaseButton-primary"]::after { content:""; position:absolute; inset:0; background:linear-gradient(110deg, transparent 30%, rgba(255,255,255,.38) 50%, transparent 70%); transform:translateX(-130%); transition:transform .75s ease; pointer-events:none; }
[data-testid="stBaseButton-primary"]:hover { filter:brightness(1.06); transform:translateY(-1px); box-shadow:0 20px 36px -14px var(--btn-from), inset 0 1px 0 rgba(255,255,255,.25); }
[data-testid="stBaseButton-primary"]:hover::after { transform:translateX(130%); }
[data-testid="stBaseButton-primary"]:active { transform:translateY(0); }
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-tertiary"] { border-radius:13px !important; border:1px solid var(--border-strong) !important; background:var(--surface) !important; color:var(--text) !important; font-weight:630 !important; transition:all .16s ease; }
[data-testid="stBaseButton-secondary"] p { color:var(--text); font-weight:630; white-space:nowrap; }
[data-testid="stBaseButton-secondary"]:hover { border-color:var(--accent) !important; background:var(--accent-soft) !important; transform:translateY(-1px); }
[data-testid="stBaseButton-secondary"]:hover p { color:var(--accent-text); }
[data-testid="stBaseButton-secondary"]:disabled { opacity:.55; transform:none; }

[data-testid="stButtonGroup"] button[data-variant="pills"] { border-radius:999px !important; border:1px solid var(--border-strong) !important; background:var(--surface) !important; padding:.3rem .95rem !important; transition:all .16s ease; }
[data-testid="stButtonGroup"] button[data-variant="pills"] p { font-size:.84rem; font-weight:620; color:var(--text); }
[data-testid="stButtonGroup"] button[data-variant="pills"]:hover { border-color:var(--accent) !important; background:var(--accent-soft) !important; transform:translateY(-1px); box-shadow:var(--shadow-sm); }
[data-testid="stButtonGroup"] button[data-variant="pills"][aria-checked="true"] { background:var(--btn-grad) !important; border-color:transparent !important; }
[data-testid="stButtonGroup"] button[data-variant="pills"][aria-checked="true"] p { color:var(--on-accent); }

[data-testid="stTextAreaRootElement"], [data-testid="stTextAreaRootElement"] > div { border-radius:16px !important; }
[data-testid="stTextAreaRootElement"] { background:var(--surface-2) !important; border:1.5px solid var(--border-strong) !important; transition:border-color .16s ease, box-shadow .16s ease; }
[data-testid="stTextAreaRootElement"]:focus-within { border-color:var(--accent) !important; box-shadow:0 0 0 4px var(--accent-ring); }
[data-testid="stTextAreaRootElement"] textarea { font-size:1.03rem !important; line-height:1.55 !important; color:var(--text) !important; background:transparent !important; }
[data-testid="stTextAreaRootElement"] textarea::placeholder { color:var(--faint) !important; }

[data-baseweb="select"] > div { border-radius:13px !important; border-color:var(--border-strong) !important; background:var(--surface-2) !important; min-height:44px; }
[data-baseweb="select"] > div:focus-within { border-color:var(--accent) !important; box-shadow:0 0 0 3px var(--accent-ring); }
[data-baseweb="select"] * { color:var(--text); }
[data-testid="stRadio"] label p, [data-testid="stCheckbox"] label p { color:var(--text); }
/* native radio / checkbox / progress follow the palette instead of the fixed Streamlit primary colour */
[data-testid="stRadio"] label:has(input:checked) > div:first-of-type > div { background:var(--accent) !important; }
[data-testid="stRadio"] label:has(input:checked) > div:first-of-type { border-color:var(--accent) !important; }
[data-testid="stCheckbox"] label:has(input:checked) > div:first-of-type { background:var(--btn-from) !important; border-color:var(--btn-from) !important; }
[data-testid="stProgress"] [role="progressbar"] > div, [data-testid="stProgress"] [data-baseweb="progress-bar"] > div > div { background:linear-gradient(90deg, var(--accent), var(--accent-2), var(--accent-3)) !important; }

[data-testid="stExpander"] details { border:1px solid var(--border) !important; border-radius:15px !important; background:var(--surface) !important; box-shadow:var(--shadow-sm); overflow:hidden; transition:border-color .16s ease, box-shadow .16s ease; }
[data-testid="stExpander"] details:hover { border-color:var(--accent-ring) !important; }
[data-testid="stExpander"] summary { padding:.72rem 1.05rem !important; font-weight:650; }
[data-testid="stExpander"] summary:hover { background:var(--surface-2); }
[data-testid="stExpander"] summary p { font-weight:650; color:var(--text); }
[data-testid="stFileUploaderDropzone"] { border-radius:15px; border:1.5px dashed var(--border-strong); background:var(--surface-2); transition:border-color .16s ease, background .16s ease; }
[data-testid="stFileUploaderDropzone"]:hover { border-color:var(--accent); background:var(--accent-soft); }
[data-testid="stDataFrame"] { border:1px solid var(--border); border-radius:15px; overflow:hidden; box-shadow:var(--shadow-sm); }
[data-testid="stAlert"] { border-radius:15px; }
[data-testid="stCode"], [data-testid="stJson"] { border-radius:13px; }
[data-testid="stProgress"] > div > div { border-radius:999px; }
[data-testid="stStatus"] details { border-radius:15px !important; }
[data-testid="stMarkdownContainer"] hr { border-color:var(--border); }
::-webkit-scrollbar { width:10px; height:10px; } ::-webkit-scrollbar-thumb { background:var(--border-strong); border-radius:999px; border:2px solid transparent; background-clip:padding-box; }

@media (max-width: 760px) {
  .hero { padding:24px 20px 20px; border-radius:22px; } .outcome { grid-template-columns:1fr; } .ex-row { grid-template-columns:1fr; }
  .kv { grid-template-columns:1fr; } .block-container { padding-left:1rem; padding-right:1rem; padding-top:3.4rem; }
  .hero-kpis { grid-template-columns:repeat(2, 1fr); }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation:none !important; transition:none !important; } }
"""


def stylesheet(dark: bool = False, palette: str = DEFAULT_PALETTE) -> str:
    """The ``<style>`` block for the page: neutrals for ``dark``/light plus the accent ``palette``."""
    tokens = (DARK if dark else LIGHT) + "\n  " + palette_tokens(palette, dark)
    return "<style>" + CSS.replace("/*TOKENS*/", tokens) + "</style>"
