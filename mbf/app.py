"""
Hospet Steels - MBF burden optimiser (dashboard, V7).
Front end for optimiser.py (engine) and analytics.py (dashboard analyses).  Run with:  streamlit run app.py
"""
import copy
import inspect
import math
import re
import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from mbf import analytics as an
from mbf import optimiser as opt

st.set_page_config(page_title="MBF burden optimiser", page_icon="⚙️", layout="wide", initial_sidebar_state="expanded")
S = st.session_state

# Newer Streamlit replaces use_container_width=True with width="stretch"; support both.
_NEW_WIDTH_API = "width" in inspect.signature(st.button).parameters
W = {"width": "stretch"} if _NEW_WIDTH_API else {"use_container_width": True}

# ----------------------------------------------------------------------------- look
GROUP_LABEL = {"Iron_ore": "Iron ore", "Sinter": "Sinter", "Minor": "Minor", "Flux": "Flux",
               "Fuel_Coke": "Coke", "Fuel_NutCoke": "Nut coke", "Fuel_PCI": "PCI"}
GROUP_COLOR = {"Iron_ore": "#4C8DFF", "Sinter": "#3FD6B0", "Flux": "#A78BFA", "Minor": "#F5A85C",
               "Fuel_Coke": "#5B6B8C", "Fuel_NutCoke": "#7C8CAD", "Fuel_PCI": "#94A2C2"}
STATUS_KIND = {"OK": "g", "AT LIMIT": "a", "OUT OF LIMIT": "r", "NOTE": "a"}
KPI_ICON = {"c": "payments", "g": "trending_up", "p": "science", "a": "local_fire_department", "": "insights"}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap');
.material-symbols-outlined { font-family:'Material Symbols Outlined'; font-weight:normal; font-style:normal; font-size:20px;
  line-height:1; letter-spacing:normal; text-transform:none; white-space:nowrap; word-wrap:normal; direction:ltr;
  -webkit-font-smoothing:antialiased; vertical-align:middle; }
:root { --bg:#0B1220; --panel:#111A2E; --panel2:#16213A; --line:#22304C; --text:#EAF0FB; --muted:#8B9BC0;
        --blue:#4C8DFF; --teal:#3FD6B0; --purple:#A78BFA; --orange:#F5A85C; --good:#3FD6A8; --warn:#F0B94E; --bad:#FF6B6B;
        --side:#0C1424; --side-panel:#111C33; --side-line:#1E2A45; --side-text:#DDE6F7; --side-muted:#7488AD; --side-active:#173059; }
html, body, .stApp, [data-testid="stAppViewContainer"] { background:var(--bg); color:var(--text);
  font-family:'Manrope','Segoe UI',system-ui,-apple-system,sans-serif; font-feature-settings:'tnum' 1, 'lnum' 1; }
[data-testid="stHeader"] { background:transparent; }
[data-testid="stMainBlockContainer"], .block-container { max-width:1500px; padding:1.4rem 1.8rem 3rem; }
h1 { font-size:1.75rem !important; font-weight:700 !important; letter-spacing:0 !important; margin:0 0 .15rem !important; padding:0 !important; color:var(--text); }
h2, h3 { font-weight:700 !important; letter-spacing:0 !important; }
.sub { color:var(--muted); font-size:.95rem; margin:0 0 1.1rem; max-width:70ch; }

/* sidebar: dark navy, as in the reference */
[data-testid="stSidebar"] { background:var(--side); border-right:1px solid var(--side-line); min-width:252px !important; max-width:252px !important; }
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] div, [data-testid="stSidebar"] label { color:var(--side-text); }
[data-testid="stSidebar"] .brand { font-size:1.1rem; font-weight:800; color:#FFFFFF; margin-top:.2rem; display:flex; align-items:center; gap:.45rem; }
[data-testid="stSidebar"] .brand .chip { width:30px; height:30px; border-radius:8px; background:linear-gradient(135deg,var(--blue),var(--teal)); display:flex; align-items:center; justify-content:center; font-size:1rem; }
[data-testid="stSidebar"] .brand-sub { font-size:.78rem; letter-spacing:.04em; color:var(--side-muted); margin:.15rem 0 1rem 2.55rem; text-transform:uppercase; }
[data-testid="stSidebar"] .nav-group { font-size:.72rem; font-weight:700; letter-spacing:.06em; text-transform:uppercase; color:var(--side-muted); margin:1rem 0 .3rem .5rem; }
[data-testid="stSidebar"] .side-foot { font-size:.8rem; color:var(--side-muted); line-height:1.55; margin-top:1rem; border-top:1px solid var(--side-line); padding-top:.7rem; }
[data-testid="stSidebar"] .side-foot b { color:var(--side-text); font-weight:600; }
[data-testid="stSidebar"] button { justify-content:flex-start; text-align:left; border-radius:8px; min-height:2.3rem; padding:.15rem .7rem; font-size:.95rem; }
[data-testid="stSidebar"] button[kind="secondary"], [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] { background:transparent; border:1px solid transparent; color:var(--side-muted); }
[data-testid="stSidebar"] button[kind="secondary"]:hover, [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover { background:var(--side-panel); border-color:transparent; color:var(--side-text); }
[data-testid="stSidebar"] button[kind="primary"], [data-testid="stSidebar"] [data-testid="stBaseButton-primary"] { background:var(--side-active); border:1px solid #2A4A80; color:#FFFFFF; font-weight:600; }
[data-testid="stSidebar"] button p { color:inherit; }

/* cards */
.kpi { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:.85rem .95rem .75rem; min-height:104px; position:relative; }
.kpi .top { display:flex; align-items:flex-start; justify-content:space-between; }
.kpi .l { color:var(--muted); font-size:.83rem; }
.kpi .ic { width:34px; height:34px; border-radius:9px; display:flex; align-items:center; justify-content:center; font-size:1.05rem; background:rgba(76,141,255,.15); color:var(--blue); flex:none; }
.kpi.g .ic { background:rgba(63,214,168,.15); color:var(--good); } .kpi.a .ic { background:rgba(240,185,78,.16); color:var(--warn); }
.kpi.r .ic { background:rgba(255,107,107,.15); color:var(--bad); } .kpi.p .ic { background:rgba(167,139,250,.16); color:var(--purple); } .kpi.c .ic { background:rgba(76,141,255,.15); color:var(--blue); }
.kpi .v { font-size:1.55rem; font-weight:800; line-height:1.15; margin-top:.35rem; }
.kpi .s { color:var(--muted); font-size:.8rem; margin-top:.15rem; }
.kpi .s.trend-up { color:var(--good); } .kpi .s.trend-down { color:var(--bad); }
.notice { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:.65rem .9rem; font-size:.93rem; margin:.3rem 0 .8rem; }
.notice b { font-weight:700; }
.notice.w { border-color:#5C4A1E; background:#1F1A0E; } .notice.r { border-color:#5C2A2A; background:#211313; } .notice.g { border-color:#1E5C46; background:#0F1F1A; }
.pill { display:inline-block; border-radius:999px; padding:.2rem .75rem; font-size:.85rem; font-weight:700; border:1px solid var(--line); background:var(--panel); }
.pill.g { color:var(--good); border-color:#1E5C46; background:rgba(63,214,168,.08); } .pill.a { color:var(--warn); border-color:#5C4A1E; background:rgba(240,185,78,.08); } .pill.r { color:var(--bad); border-color:#5C2A2A; background:rgba(255,107,107,.08); }
.panel-title { font-size:1.02rem; font-weight:700; margin:.2rem 0 .35rem; }
.panel-note { color:var(--muted); font-size:.85rem; margin:-.15rem 0 .5rem; }

/* limit bars: the one memorable element */
.bandrow { display:grid; grid-template-columns:150px 1fr 74px; gap:.7rem; align-items:center; margin:.42rem 0 .12rem; }
.bandlabel { font-size:.93rem; }
.bandtrack { position:relative; height:12px; background:#08101F; border-radius:6px; border:1px solid var(--line); }
.bandzone { position:absolute; top:-1px; bottom:-1px; background:rgba(76,141,255,.22); border:1px solid rgba(76,141,255,.55); border-radius:6px; }
.bandmark { position:absolute; top:-5px; width:6px; height:20px; border-radius:3px; background:var(--good); transform:translateX(-3px); box-shadow:0 0 0 2px var(--bg); }
.bandmark.a { background:var(--warn); } .bandmark.r { background:var(--bad); }
.bandval { text-align:right; font-weight:700; font-size:.98rem; }
.bandval.a { color:var(--warn); } .bandval.r { color:var(--bad); }
.bandrange { grid-column:2 / 4; color:var(--muted); font-size:.78rem; margin:-.05rem 0 .2rem; }

.note-line { font-size:.92rem; padding:.42rem .7rem; border-radius:6px; margin:.28rem 0; background:var(--panel); border-left:3px solid var(--line); }
.note-line.chk { border-left-color:var(--warn); } .note-line.wrn { border-left-color:var(--bad); } .note-line.nt { border-left-color:var(--blue); }

/* widgets */
div.stButton > button, div.stDownloadButton > button { border-radius:8px; font-weight:600; }
button[kind="primary"], [data-testid="stBaseButton-primary"] { background:var(--good); border-color:var(--good); color:#062018; font-weight:700; }
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { background:#57E6BB; border-color:#57E6BB; color:#062018; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap:.2rem; border-bottom:1px solid var(--line); }
[data-testid="stTabs"] [data-baseweb="tab"] { font-size:.95rem; font-weight:500; color:var(--muted); padding:.5rem .95rem; }
[data-testid="stTabs"] [aria-selected="true"] { color:var(--text); }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
[data-testid="stExpander"] { border:1px solid var(--line); border-radius:10px; background:var(--panel); }
[data-testid="stFileUploader"] section { background:var(--panel); border:1px dashed var(--line); border-radius:10px; }
.vsline { color:var(--muted); font-size:.88rem; margin-left:.8rem; }
.foot { color:#5A6B8F; font-size:.8rem; text-align:right; margin-top:1.6rem; }
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------- state
def init_state():
    if "df" in S:
        return
    S.df = opt.demo_df()
    S.source = "Built-in demo table"
    S.demo = True
    S.cfg = opt.Config()
    S.master_notes = []
    S.result = None            # (status, blend, cost, achieved, diagnostics)
    S.bundle = None            # frozen inputs + results of the last run
    S.runs = 0
    S.changed = False
    S.changed_source = ""
    S.history = []
    S.analysis = {}            # last sweep / sensitivities / tornado / break-even, kept for the Excel export
    S.export = None            # (bytes, file name, run stamp) once the workbook has been built
    S.nav = "Dashboard"
    S.cfg_ver = 0
    S.mat_ver = 0
    S.up_ver = 0
    S.selftest = None
    # V6
    S.cache = {}               # analysis results keyed by (kind, materials fingerprint, settings, parameters)
    S.scenarios = []           # saved runs (name, key numbers, recipe, settings, inputs, 0-100 % curve)
    S.last_ok = None           # (materials, settings) of the last optimal run, to label what changed
    S.qi_draft = None          # quick-inputs draft {"cfg", "df"} while the panel is open
    S.qi_ver = 0
    S.qi_show = False
    S.ox_whatif = None
    S.v6 = {}                  # latest Trends / Slag oxides tables, for the Excel export
    # V7
    S.audit_recs = []          # plant months read for the heat audit
    S.audit = None             # the last heat-audit result
    S.aud_ver = 0
    S.audit_msg = None


init_state()


def mark_changed(source):
    S.changed = True
    S.changed_source = source


def status_label():
    if S.result is None:
        return "Not run yet", "a"
    if S.changed:
        return f"Rerun needed: {S.changed_source} changed", "r"
    return f"Up to date, run {S.runs}", "g"


def blockers():
    """Reasons the optimiser cannot be run right now (settings that conflict, material-table errors)."""
    return list(S.cfg.problems()) + opt.validate_df(opt.ensure_columns(S.df))


def run_optimizer(progress=None):
    changes = an.describe_changes(S.last_ok[0], S.df, S.last_ok[1], S.cfg) if S.last_ok else None
    res = opt.solve(S.df, S.cfg)
    S.bundle = opt.make_bundle(S.df, res, S.cfg, progress=progress)     # also solves the 0-100 % sinter curve
    S.result = res
    S.runs += 1
    S.changed = False
    S.changed_source = ""
    S.export = None
    if res[0] == "Optimal":
        a = res[3]
        S.history.append({"Run": S.runs, "Furnace": S.cfg.furnace_name, "Cost Rs/tHM": res[2], "Sinter %": a["Sinter_share_pct"],
                          "Coke kg/tHM": a["Coke_kg"], "Fuel kg/tHM": a["Fuel_supplied"], "Time": dt.datetime.now().strftime("%H:%M"),
                          "Changed": "first run" if changes is None else an.change_label(changes)})
        S.history = S.history[-30:]
        S.last_ok = (opt.ensure_columns(S.df).copy(), copy.deepcopy(S.cfg))


def reset_results():
    S.result = None
    S.bundle = None
    S.changed = False
    S.changed_source = ""
    S.export = None
    S.analysis = {}


# ----------------------------------------------------------------------------- small UI helpers
def nice(v):
    """Number for display: thousands separators, no trailing zeros."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (int, np.integer)):
        return f"{v:,}"
    s = f"{v:,.4f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


_ALIGN = "alignment" in inspect.signature(st.column_config.TextColumn).parameters


def show(df, status_cols=(), height=None):
    # The table widget shows missing numbers as "None" whatever the formatting, so a number column with gaps
    # (a TOTAL row without a price, say) is shown as formatted text, right-aligned, with the gaps left blank.
    df = df.copy()
    cc = {}
    for c in [c for c in df.columns if pd.api.types.is_float_dtype(df[c]) and df[c].isna().any()]:
        df[c] = df[c].map(nice)
        if _ALIGN:
            cc[c] = st.column_config.TextColumn(alignment="right")
    sty = df.style
    floats = [c for c in df.columns if pd.api.types.is_float_dtype(df[c])]
    if floats:
        sty = sty.format({c: nice for c in floats}, na_rep="")

    def color(v):
        k = {"OK": "#1F5A44", "USED": "#1F5A44", "ON": "#1F5A44", "Optimal": "#1F5A44", "AT LIMIT": "#6B5620", "NOTE": "#6B5620",
             "OUT OF LIMIT": "#7A2F33", "OFF": "#7A2F33", "Infeasible": "#7A2F33", "INPUT_ERROR": "#7A2F33", "not used": "#3C4250"}.get(v)
        return f"background-color:{k};color:#EAF0FB" if k else ""
    cols = [c for c in status_cols if c in df.columns]
    if cols:
        sty = sty.map(color, subset=cols)
    kw = {"height": height} if height else {}
    if cc:
        kw["column_config"] = cc
    st.dataframe(sty, hide_index=True, **W, **kw)


def kpi(label, value, sub="", kind="", icon=None, trend=None, lower_is_better=False):
    """trend: a signed float (percent or points) if there is something real to compare against, else None.
    lower_is_better: True for cost-like metrics, so a fall shows green and a rise shows red."""
    ic = icon or KPI_ICON.get(kind, "insights")
    trend_html = ""
    if trend is not None and not (isinstance(trend, float) and math.isnan(trend)):
        arrow = "arrow_upward" if trend > 0 else "arrow_downward" if trend < 0 else "remove"
        good = (trend < 0) if lower_is_better else (trend > 0)
        cls = "trend-up" if good else "trend-down" if trend != 0 else ""
        trend_html = f' &nbsp;<span class="s {cls}"><span class="material-symbols-outlined" style="font-size:.9em;vertical-align:-2px">{arrow}</span> {abs(trend):.1f}%</span>'
    return (f'<div class="kpi {kind}"><div class="top"><div class="l">{label}</div>'
            f'<div class="ic"><span class="material-symbols-outlined">{ic}</span></div></div>'
            f'<div class="v">{value}</div><div class="s">{sub}{trend_html}</div></div>')


def notice(title, body, kind=""):
    return f'<div class="notice {kind}"><b>{title}</b> {body}</div>'


def page_header(title, subtitle):
    st.markdown(f"<h1>{title}</h1><div class='sub'>{subtitle}</div>", unsafe_allow_html=True)


def panel_title(text, note=""):
    st.markdown(f'<div class="panel-title">{text}</div>' + (f'<div class="panel-note">{note}</div>' if note else ""), unsafe_allow_html=True)


def notes_html(lines):
    out = []
    for ln in lines:
        s = str(ln)
        cls = "chk" if s.startswith("CHECK") else "wrn" if s.startswith("WARNING") else "nt" if s.startswith("NOTE") else ""
        out.append(f'<div class="note-line {cls}">{s}</div>')
    return "".join(out)


def need_result():
    """Stop a page that needs a finished run and say what to do."""
    if S.result is None:
        st.markdown(notice("No results yet.", "Run the optimiser from the Dashboard first."), unsafe_allow_html=True)
        if st.button("Go to Dashboard", key=f"goto_dash_{S.nav}"):
            S.nav = "Dashboard"
            st.rerun()
        st.stop()
    if S.result[0] != "Optimal":
        st.markdown(notice(f"The last run ended as {S.result[0]}.", "There is no burden to show. The reasons are on the Dashboard."), unsafe_allow_html=True)
        st.stop()


def comp_kind(bundle, prefix):
    for _, r in bundle["compliance"].iterrows():
        if str(r["Requirement"]).startswith(prefix):
            return STATUS_KIND.get(r["Status"], "")
    return ""


# ----------------------------------------------------------------------------- charts
def _layout(fig, height, margin=(10, 10, 10, 10), legend=False):
    fig.update_layout(height=height, margin=dict(l=margin[0], r=margin[1], t=margin[2], b=margin[3]),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Manrope, Segoe UI, sans-serif", color="#EAF0FB", size=13), showlegend=legend,
                      legend=dict(orientation="h", y=-0.15, font=dict(size=12)))
    return fig


def fig_burden(bundle):
    bf = bundle["burden_full"]
    used = bf[bf["Result"] == "USED"].sort_values("Dry kg/tHM")
    fig = go.Figure(go.Bar(x=used["Dry kg/tHM"], y=used["Material"], orientation="h",
                           marker_color=[GROUP_COLOR.get(g, "#888") for g in used["Group"]],
                           text=[f"{v:,.1f}" for v in used["Dry kg/tHM"]], textposition="outside", cliponaxis=False,
                           customdata=[GROUP_LABEL.get(g, g) for g in used["Group"]],
                           hovertemplate="%{y} (%{customdata}): %{x:,.1f} kg/tHM<extra></extra>"))
    fig.update_xaxes(showgrid=True, gridcolor="#22304C", zeroline=False, title="kg per tonne of hot metal (dry)")
    fig.update_yaxes(showgrid=False)
    return _layout(fig, max(240, 34 * len(used) + 70), margin=(10, 50, 10, 40))


def fig_cost_donut(bundle):
    bf = bundle["burden_full"]
    used = bf[(bf["Result"] == "USED") & (bf["Cost Rs/tHM"] > 0)]
    om_ = float(bundle["ach"].get("OM_Rs_tHM", 0.0))
    labels, values = list(used["Material"]), list(used["Cost Rs/tHM"])
    colors = [GROUP_COLOR.get(g, "#888") for g in used["Group"]]
    if om_ > 0:
        labels.append("O&M"); values.append(om_); colors.append("#7B8A96")
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.68, sort=True,
                           marker=dict(colors=colors, line=dict(color="#0B1220", width=2)),
                           textinfo="none", hovertemplate="%{label}: Rs %{value:,.0f}/tHM (%{percent})<extra></extra>"))
    fig.add_annotation(text=f"<b>₹ {bundle['cost']:,.0f}</b><br><span style='font-size:12px;color:#8B9BC0'>per tHM</span>",
                       x=0.5, y=0.5, showarrow=False, font=dict(size=22))
    return _layout(fig, 300, legend=True)


def fig_gauge(title, value, top, bar="#4C8DFF", band=None, suffix=""):
    gauge = dict(axis=dict(range=[0, top], tickfont=dict(size=10), tickcolor="#5B6B8C"), bar=dict(color=bar, thickness=0.3),
                 bgcolor="#08101F", borderwidth=0)
    if band:
        gauge["steps"] = [dict(range=list(band), color="rgba(34,184,230,.25)")]
    fig = go.Figure(go.Indicator(mode="gauge+number", value=value, number=dict(suffix=suffix, font=dict(size=26)),
                                 title=dict(text=title, font=dict(size=13, color="#8B9BC0")), gauge=gauge))
    return _layout(fig, 170, margin=(18, 18, 38, 8))


def fig_history(hist):
    h = pd.DataFrame(hist)
    fig = go.Figure(go.Scatter(x=h["Run"], y=h["Cost Rs/tHM"], mode="lines+markers", line=dict(color="#4C8DFF", width=3),
                               marker=dict(size=8, color="#A78BFA"), hovertemplate="Run %{x}: Rs %{y:,.0f}/tHM<extra></extra>"))
    fig.update_xaxes(dtick=1, title="Run", showgrid=False)
    fig.update_yaxes(title="Rs per tHM", gridcolor="#22304C", tickformat=",")
    return _layout(fig, 230, margin=(10, 10, 10, 35))


GROUP_PALETTE = {"Iron_ore": ["#4C8DFF", "#8DB6FF", "#2E5FB8", "#BFD6FF"], "Sinter": ["#3FD6B0", "#1F9C7F", "#8CEBD2"],
                 "Flux": ["#A78BFA", "#F472B6", "#FACC15", "#C4B5FD"], "Minor": ["#F5A85C", "#FFD29E"],
                 "Fuel_Coke": ["#5B6B8C", "#8391B0", "#3E4A63"], "Fuel_NutCoke": ["#7C8CAD"], "Fuel_PCI": ["#B8C3DB"]}


def mat_colors(names, groups):
    """Same colour for the same material in every chart: a colour family per group, a different shade per material.
    Shades follow the material's position in the material table, so a material keeps its colour from chart to chart."""
    order = list(S.df.index) if "df" in S else []
    out = {}
    for m in names:
        g = groups.get(m, "")
        pal = GROUP_PALETTE.get(g, ["#888888"])
        same = [x for x in order if x in S.df.index and str(S.df.loc[x, "Group"]) == g] if order else []
        k = same.index(m) if m in same else sum(1 for x in out if groups.get(x) == g)
        out[m] = pal[k % len(pal)]
    return out


def fig_sinter_curve(curve, choice, col, title, color, cfg):
    """Coke or cost against sinter share 0-100 %: this run starred, infeasible range shaded, guard rails shaded."""
    fig = go.Figure()
    fig.add_vrect(x0=cfg.sinter_min * 100, x1=cfg.sinter_max * 100, fillcolor="rgba(63,214,168,.08)", line_width=0,
                  annotation_text="guard rails", annotation_position="top left", annotation_font=dict(size=11, color="#8B9BC0"))
    lo, hi = curve.attrs.get("lo"), curve.attrs.get("hi")
    if lo is None:
        fig.add_vrect(x0=0, x1=100, fillcolor="rgba(255,107,107,.13)", line_width=0)
    else:
        if hi < 100 - 1e-6:
            fig.add_vrect(x0=hi, x1=100, fillcolor="rgba(255,107,107,.13)", line_width=0,
                          annotation_text=f"infeasible above {hi:.1f} %", annotation_position="top right",
                          annotation_font=dict(size=11, color="#FF6B6B"))
        if lo > 1e-6:
            fig.add_vrect(x0=0, x1=lo, fillcolor="rgba(255,107,107,.13)", line_width=0,
                          annotation_text=f"infeasible below {lo:.1f} %", annotation_position="top left",
                          annotation_font=dict(size=11, color="#FF6B6B"))
    ok = curve[curve["Status"] == "Optimal"] if "Status" in curve.columns else curve.iloc[0:0]
    if col in ok.columns and len(ok):
        fig.add_trace(go.Scatter(x=ok["Sinter %"], y=ok[col], mode="lines+markers", line=dict(color=color, width=3), marker=dict(size=5),
                                 name=title, hovertemplate="Sinter %{x:.1f} %: %{y:,.1f}<extra></extra>"))
    if col == "Coke kg" and "Heat-min coke kg" in ok.columns and len(ok):
        cal = bool(getattr(cfg, "heat", {}).get("calibrated"))
        fig.add_trace(go.Scatter(x=ok["Sinter %"], y=ok["Heat-min coke kg"], mode="lines", line=dict(color="#FF6B6B", width=2, dash="dash"),
                                 name="Heat-balance minimum coke" + ("" if cal else " (uncalibrated)"),
                                 hovertemplate="Heat-balance minimum at %{x:.1f} %: %{y:,.1f} kg<extra></extra>"))
    if choice is not None:
        val = choice[2] if col == "Coke kg" else choice[1]
        fig.add_trace(go.Scatter(x=[choice[0]], y=[val], mode="markers+text", marker=dict(symbol="star", size=18, color="#3FD6A8",
                                 line=dict(color="#0B1220", width=1)), text=[f"{val:,.1f} kg" if col == "Coke kg" else f"₹ {val:,.0f}"],
                                 textposition="top center", textfont=dict(color="#3FD6A8", size=13), name=choice[3],
                                 hovertemplate=f"{choice[3]}: %{{x:.2f}} % sinter, %{{y:,.1f}}<extra></extra>"))
    fig.update_xaxes(range=[0, 100], dtick=10, title="Sinter share of (sinter + ore), %", showgrid=False)
    fig.update_yaxes(title="kg per tHM" if col == "Coke kg" else "Rs per tHM", gridcolor="#22304C", tickformat=",")
    return _layout(fig, 300, margin=(10, 10, 30, 40))


def fig_oxide_sources(compact):
    """Stacked bars: share of the CaO, SiO2 and Al2O3 charged that each material brings."""
    body = compact[compact["Material"] != "TOTAL CHARGED"]
    tot = compact[compact["Material"] == "TOTAL CHARGED"].iloc[0]
    oxides = ["CaO", "SiO2", "Al2O3"]
    ylab = [f"{o}  ({float(tot[f'{o} kg']):.1f} kg)" for o in oxides]
    cols = mat_colors(list(body["Material"]), dict(zip(body["Material"], body["Group"])))
    fig = go.Figure()
    for _, r in body.iterrows():
        vals = [float(r[f"{o} % of total"]) for o in oxides]
        kgs = [float(r[f"{o} kg"]) for o in oxides]
        fig.add_trace(go.Bar(y=ylab, x=vals, orientation="h", name=r["Material"], marker_color=cols[r["Material"]],
                             text=[f"{v:.0f}%" if v >= 5 else "" for v in vals], textposition="inside", insidetextanchor="middle",
                             customdata=kgs, hovertemplate=f"{r['Material']}: %{{x:.1f}} %% (%{{customdata:.2f}} kg/tHM)<extra></extra>"))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(range=[0, 100], title="% of the total charged to the furnace", gridcolor="#22304C")
    fig.update_yaxes(autorange="reversed")
    fig = _layout(fig, 290, margin=(10, 10, 46, 30), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)))
    return fig


def fig_oxides_by_sinter(oxl, oxide):
    pts = oxl[oxl["Point"] != "optimizer's choice"]
    pv = pts.pivot_table(index="Sinter %", columns="Material", values=f"{oxide} kg", aggfunc="sum", fill_value=0.0)
    groups = dict(zip(pts["Material"], pts["Group"]))
    order = {g: i for i, g in enumerate(opt.GROUPS)}
    mats_ = sorted([m for m in pv.columns if pv[m].abs().max() > 1e-6], key=lambda m: (order.get(groups.get(m), 99), m))
    cols = mat_colors(mats_, groups)
    fig = go.Figure()
    for m in mats_:
        fig.add_trace(go.Bar(x=[f"{v:g}" for v in pv.index], y=pv[m], name=m, marker_color=cols[m],
                             hovertemplate=f"{m}: %{{y:.2f}} kg at %{{x}} %% sinter<extra></extra>"))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="Sinter share, %", type="category")
    fig.update_yaxes(gridcolor="#22304C", title=f"{oxide} charged, kg per tHM")
    fig = _layout(fig, 330, margin=(10, 10, 46, 40), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)))
    return fig


def fig_tornado(tor, n=15):
    t = tor.dropna(subset=["Swing Rs"]).head(n).iloc[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=t["Driver"], x=t["Low vs base Rs"], orientation="h", name="Driver moved down", marker_color="#4C8DFF",
                         hovertemplate="%{y}: %{x:+,.0f} Rs/tHM<extra>down</extra>"))
    fig.add_trace(go.Bar(y=t["Driver"], x=t["High vs base Rs"], orientation="h", name="Driver moved up", marker_color="#F5A85C",
                         hovertemplate="%{y}: %{x:+,.0f} Rs/tHM<extra>up</extra>"))
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(title="Change in burden cost, Rs per tHM", gridcolor="#22304C", zeroline=True, zerolinecolor="#5B6B8C", tickformat=",")
    return _layout(fig, max(280, 28 * len(t) + 90), margin=(10, 10, 10, 40), legend=True)


def band_bar(label, value, lo, hi, kind, unit="", dec=2):
    pinned = (hi - lo) < 1e-9
    span = max(abs(lo) * 0.1, 1.0) if pinned else (hi - lo)
    vmin, vmax = lo - 0.55 * span, hi + 0.55 * span
    pos = lambda x: max(0.0, min(100.0, 100.0 * (x - vmin) / (vmax - vmin)))
    zone = (f'<div class="bandzone" style="left:{pos(lo) - 0.6:.1f}%;width:1.2%"></div>' if pinned
            else f'<div class="bandzone" style="left:{pos(lo):.1f}%;width:{pos(hi) - pos(lo):.1f}%"></div>')
    rng = f"pinned at {lo:.{dec}f}{unit}" if pinned else f"allowed {lo:.{dec}f} to {hi:.{dec}f}{unit}"
    return (f'<div class="bandrow"><div class="bandlabel">{label}</div>'
            f'<div class="bandtrack">{zone}<div class="bandmark {kind}" style="left:{pos(value):.1f}%"></div></div>'
            f'<div class="bandval {kind}">{value:.{dec}f}{unit}</div>'
            f'<div class="bandrange">{rng}</div></div>')


# ----------------------------------------------------------------------------- material master helpers
def frame_to_master(edited):
    d = edited.copy()
    d = d[d["Material"].notna() & (d["Material"].astype(str).str.strip() != "")]
    d["Material"] = d["Material"].map(opt.sanitize_name)
    for c in opt.NUM_COLS:
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    for c in opt.OPT_COLS:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["Available"] = d["Available"].fillna(False).astype(bool)
    d["Group"] = d["Group"].astype(str).str.strip()
    return d.set_index("Material")[opt.COLUMNS[1:]]


def activate_master(newdf, name, notes):
    S.df = newdf
    S.source = name
    S.demo = False
    S.master_notes = notes
    reset_results()
    S.up_ver += 1
    S.mat_ver += 1


def master_uploader(prefix):
    f = st.file_uploader("Master materials workbook (.xlsx)", type=["xlsx"], key=f"{prefix}_up_{S.up_ver}",
                         help="One sheet, one row per material. Download the template under Upload & settings.")
    if f is None:
        return
    try:
        newdf, notes = opt.load_master(f.getvalue())
    except Exception as e:
        st.markdown(notice("That file could not be read.", str(e).replace("\n", "<br>"), "r"), unsafe_allow_html=True)
        return
    st.markdown(notice(f"{len(newdf)} materials read from {f.name}.", "Nothing changes until you activate it.", "g"), unsafe_allow_html=True)
    if notes:
        st.markdown(notes_html(["NOTE: " + n for n in notes]), unsafe_allow_html=True)
    if st.button("Activate this master", type="primary", key=f"{prefix}_activate"):
        activate_master(newdf, f.name, notes)
        st.rerun()


# ----------------------------------------------------------------------------- sidebar
NAV = [("Workspace", [("Dashboard", "dashboard"), ("Inputs", "tune")]),
       ("Operations", [("Materials & stock", "inventory_2"), ("Burden & cost", "layers")]),
       ("Analysis", [("Slag oxides", "science"), ("Trends", "monitoring"), ("Scenario analysis", "query_stats"),
                     ("Heat audit", "thermostat")]),
       ("Reporting", [("Reports & export", "download")]),
       ("System", [("Upload & settings", "settings")])]


def sidebar_nav():
    with st.sidebar:
        st.markdown('<div class="brand"><span class="chip">⚙️</span> MBF Cost Optimizer</div>'
                    '<div class="brand-sub">Hospet Steels</div>', unsafe_allow_html=True)
        for group, items in NAV:
            st.markdown(f'<div class="nav-group">{group}</div>', unsafe_allow_html=True)
            for label, icon in items:
                if st.button(label, key=f"nav_{label}", icon=f":material/{icon}:", type="primary" if S.nav == label else "secondary", **W):
                    S.nav = label
                    st.rerun()


def sidebar_footer():
    """Drawn after the page has run, so the status shown is the current one."""
    with st.sidebar:
        label, kind = status_label()
        on = int(S.df["Available"].sum())
        st.markdown(f'<div class="side-foot"><b>Materials</b><br>{S.source}<br>{len(S.df)} rows, {on} switched on<br><br>'
                    f'<b>Optimiser</b><br>{label}<br><br><b>Engine</b><br>{opt.ENGINE_VERSION}</div>', unsafe_allow_html=True)


# ============================================================================= DASHBOARD
def dashboard():
    page_header("Dashboard", "Least-cost burden for one tonne of hot metal, checked against every plant limit.")
    cfg = S.cfg
    if S.demo:
        st.markdown(notice("Demo table in use.", "Prices and assays are placeholders. Upload your master workbook under Upload & settings before relying on a result.", "w"),
                    unsafe_allow_html=True)
    blk = blockers()

    c1, c2, c3, c5, c4 = st.columns([1, 1.25, 1, 0.95, 1], vertical_alignment="bottom")
    with c1:
        names = list(cfg.furnace_profiles)
        new = st.selectbox("Furnace", names, index=names.index(cfg.furnace_name) if cfg.furnace_name in names else 0, key=f"dash_furnace_{S.cfg_ver}")
        if new != cfg.furnace_name:
            cfg.furnace_name = new
            mark_changed("Furnace")
    with c2:
        mode = (f"Pinned at {cfg.sinter_manual_pct:g} %" if cfg.sinter_manual_on
                else f"Free choice, guard rails {cfg.sinter_min * 100:g} to {cfg.sinter_max * 100:g} %")
        st.markdown(f"<div class='panel-note' style='margin:0'>Sinter share</div><div style='font-weight:600;padding-bottom:.45rem'>{mode}</div>", unsafe_allow_html=True)
    with c3:
        st.markdown(f"<div class='panel-note' style='margin:0'>Materials switched on</div>"
                    f"<div style='font-weight:600;padding-bottom:.45rem'>{int(S.df['Available'].sum())} of {len(S.df)}</div>", unsafe_allow_html=True)
    with c5:
        opened = st.button("Adjust inputs", key="qi_open", icon=":material/tune:", help="Change the main inputs here and rerun, without leaving the Dashboard.", **W)
        if opened:
            open_quick_inputs()
    with c4:
        run = st.button("Run optimiser", type="primary", disabled=bool(blk), key="run_opt", **W)
    if S.qi_draft is not None and S.qi_show and (opened or _DIALOG_ON_DISMISS):
        quick_inputs()

    label, kind = status_label()
    vs = vs_last_run_text()
    st.markdown(f'<span class="pill {kind}">{label}</span>' + (f'<span class="vsline">{vs}</span>' if vs else ""), unsafe_allow_html=True)
    if blk:
        st.markdown(notice("Fix these before running:", "<br>".join(f"- {b}" for b in blk[:8]), "r"), unsafe_allow_html=True)

    off = cfg.rules_off()
    if off:
        st.markdown(notice("Thumb rules switched off:", ", ".join(off) + ". Results exclude them (not the plant rule set). "
                           "Switch them back on under Inputs > Fuel-rate rules.", "w"), unsafe_allow_html=True)

    if run:
        bar = st.progress(0.0, text="Solving the burden...")
        run_optimizer(progress=lambda i, n: bar.progress(i / n, text=f"Drawing the 0-100 % sinter curve: {i} of {n} sinter shares"))
        bar.empty()
        st.rerun()

    with st.expander("Change the materials workbook"):
        st.caption(f"Active: {S.source}")
        master_uploader("dash")

    if S.result is None:
        st.markdown(notice("No results yet.", "Check the settings under Inputs and the materials under Materials & stock, then press Run optimiser."), unsafe_allow_html=True)
        return
    status, blend, cost, ach, diag = S.result
    if status != "Optimal":
        st.markdown(notice(f"No burden was produced: {status}.", "The reasons are listed below.", "r"), unsafe_allow_html=True)
        st.markdown(notes_html(diag), unsafe_allow_html=True)
        cv = S.bundle.get("curve") if S.bundle else None
        if cv is not None:
            panel_title("Where a feasible burden exists", cv.attrs.get("note", ""))
            c1, c2 = st.columns(2)
            c1.plotly_chart(fig_sinter_curve(cv, None, "Coke kg", "Regular coke", "#4C8DFF", S.bundle["cfg"]), config={"displayModeBar": False}, **W)
            c2.plotly_chart(fig_sinter_curve(cv, None, "Cost Rs/tHM", "Burden cost", "#F5A85C", S.bundle["cfg"]), config={"displayModeBar": False}, **W)
        return
    b = S.bundle
    if S.changed:
        st.markdown(notice("These results are from an earlier run.", f"{S.changed_source} changed since. Press Run optimiser to refresh.", "w"), unsafe_allow_html=True)

    # ---- key numbers (trend, where shown, is this run vs the previous one in the run history - never invented)
    prev = S.history[-2] if len(S.history) >= 2 else None
    cost_trend = 100.0 * (cost - prev["Cost Rs/tHM"]) / prev["Cost Rs/tHM"] if prev else None
    sinter_trend = ach["Sinter_share_pct"] - prev["Sinter %"] if prev else None
    om_ = float(ach.get("OM_Rs_tHM", 0.0))
    k1 = [(("Raw-material cost" if om_ <= 0 else "Cost incl. O&M"), f"₹ {cost:,.0f}",
           ("per tHM vs last run" if om_ <= 0 else f"per tHM: raw ₹{cost - om_:,.0f} + O&M ₹{om_:,.0f}"), "c", "payments", cost_trend),
          ("Sinter share", f"{ach['Sinter_share_pct']:.1f} %", "of sinter + ore", comp_kind(b, "Sinter share"), "grain", sinter_trend),
          ("Total fuel", f"{ach['Fuel_supplied']:.1f} kg", "coke + nut coke + PCI, per tHM", comp_kind(b, "Fuel supplied"), "local_fire_department", None),
          ("Regular coke", f"{ach['Coke_kg']:.1f} kg", "the residual fuel, per tHM", "p", "propane", None),
          ("Slag", f"{ach['slag_kg']:.0f} kg", "per tHM", "p", "layers", None)]
    k2 = [("Basicity B2", f"{ach['B2']:.3f}", "CaO / SiO2", comp_kind(b, "Slag basicity"), "science", None),
          ("MgO in slag", f"{ach['MgO_pct']:.2f} %", "", comp_kind(b, "MgO"), "science", None),
          ("Al2O3 in slag", f"{ach['Al2O3_pct']:.2f} %", "", comp_kind(b, "Al2O3"), "science", None),
          ("MgO / Al2O3", f"{ach['MgO_Al2O3']:.2f}", "guide 0.40-0.55, diagnostic", comp_kind(b, "MgO / Al2O3"), "water_drop", None),
          ("Predicted S in hot metal", f"{ach['S_HM_pct']:.3f} %", f"Ks {b['cfg'].ks_fixed:g}, all charged S", "", "insights", None)]
    for row_i, row in enumerate((k1, k2)):
        cols = st.columns(5)
        for col_i, (col, (lab, val, sub, kd, ic, tr)) in enumerate(zip(cols, row)):
            lib = row_i == 0 and col_i == 0        # only the cost card treats a fall as good
            col.markdown(kpi(lab, val, sub, kd, icon=ic, trend=tr, lower_is_better=lib), unsafe_allow_html=True)
        st.write("")

    # ---- recipe: straight under the key numbers
    panel_title("Recipe", "The optimised burden for one tonne of hot metal. Dry kg is the model basis; wet kg = dry / (1 - moisture).")
    rec = recipe_table(b)
    show(rec, height=35 * (len(rec) + 1) + 4)
    save_scenario_row("dash")

    # ---- what decided the sinter share
    if b.get("decision"):
        dec = b["decision"].replace("SINTER DECISION: ", "")
        head, _, rest = dec.partition(". ")
        st.markdown(notice("What set the sinter share:", f"{head}.<br><span style='color:#8B9BC0'>{rest}</span>", "g"), unsafe_allow_html=True)

    # ---- coke and cost at every sinter share
    cv = b.get("curve")
    if cv is not None:
        panel_title("Coke and cost at every sinter share, 0 to 100 %",
                    "The full model re-solved with the sinter share pinned at each value. The star is this run. " + cv.attrs.get("note", ""))
        c1, c2 = st.columns(2)
        c1.plotly_chart(fig_sinter_curve(cv, b.get("choice"), "Coke kg", "Regular coke", "#4C8DFF", b["cfg"]), config={"displayModeBar": False}, **W)
        c2.plotly_chart(fig_sinter_curve(cv, b.get("choice"), "Cost Rs/tHM", "Burden cost", "#F5A85C", b["cfg"]), config={"displayModeBar": False}, **W)

    # ---- where the slag oxides come from
    if b.get("compact_oxides") is not None:
        panel_title("Where the CaO, SiO2 and Al2O3 come from", "Share of each oxide charged to the furnace, by material.")
        st.plotly_chart(fig_oxide_sources(b["compact_oxides"]), config={"displayModeBar": False}, **W)
        with st.expander("Oxide sources table (kg and % by material)"):
            co = b["compact_oxides"].copy()
            co["Group"] = co["Group"].map(lambda g: GROUP_LABEL.get(g, g))
            show(co)

    # ---- burden and cost
    left, right = st.columns([1.55, 1])
    with left:
        panel_title("What goes into one tonne of hot metal", "Dry kg per tHM, coloured by material group.")
        st.plotly_chart(fig_burden(b), config={"displayModeBar": False}, **W)
    with right:
        panel_title("Where the cost goes", "Share of the cost by material" + (", with O&M." if float(ach.get("OM_Rs_tHM", 0.0)) > 0 else "."))
        st.plotly_chart(fig_cost_donut(b), config={"displayModeBar": False}, **W)

    # ---- limits
    left, right = st.columns([1.6, 1])
    with left:
        panel_title("Limits", "The marker shows where each value landed inside its allowed band.")
        bc = b["cfg"]
        if bc.sinter_manual_on:
            slo = shi = bc.sinter_manual_pct
        else:
            slo, shi = bc.sinter_min * 100, bc.sinter_max * 100
        g_lo, g_hi = bc.mgo_al2o3_guide_lo, bc.mgo_al2o3_guide_hi
        html = (band_bar("Sinter share", ach["Sinter_share_pct"], slo, shi, comp_kind(b, "Sinter share"), " %", 1)
                + band_bar("Basicity B2", ach["B2"], bc.basicity_min, bc.basicity_max, comp_kind(b, "Slag basicity"), "", 3)
                + band_bar("MgO in slag", ach["MgO_pct"], bc.mgo_min_pct, bc.mgo_max_pct, comp_kind(b, "MgO in slag"), " %", 2)
                + band_bar("Al2O3 in slag", ach["Al2O3_pct"], bc.al2o3_min_pct, bc.al2o3_max_pct, comp_kind(b, "Al2O3"), " %", 2)
                + band_bar("MgO / Al2O3 (guide)", ach["MgO_Al2O3"], g_lo, g_hi, comp_kind(b, "MgO / Al2O3"), "", 2))
        st.markdown(html, unsafe_allow_html=True)
    with right:
        st.write("")
        st.caption("Sinter share is shown against the guard rails. Amber means the value sits on a band edge: the optimiser is using that limit "
                   "in full. MgO/Al2O3 is a drainage guide only; it never constrains the optimiser. The Slag oxides page shows which "
                   "materials bring each oxide and what would move it.")

    # ---- fuel gauges
    panel_title("Fuel", "Per tonne of hot metal. PCI and nut coke are fixed; regular coke is what the fuel rule leaves.")
    g1, g2, g3, g4 = st.columns(4)
    cfgb = b["cfg"]
    band = (cfgb.sinter_min * 100, cfgb.sinter_max * 100) if not cfgb.sinter_manual_on else None
    g1.plotly_chart(fig_gauge("Regular coke, kg", ach["Coke_kg"], 600, "#5B6B8C"), config={"displayModeBar": False}, **W)
    g2.plotly_chart(fig_gauge("Nut coke, kg", ach["NutCoke_kg"], 100, "#7C8CAD"), config={"displayModeBar": False}, **W)
    g3.plotly_chart(fig_gauge("PCI, kg", ach["PCI_kg"], 200, "#94A2C2"), config={"displayModeBar": False}, **W)
    g4.plotly_chart(fig_gauge("Sinter share (guard rails)", ach["Sinter_share_pct"], 100, "#4C8DFF", band, " %"), config={"displayModeBar": False}, **W)

    # ---- heat balance (hot zone below the thermal reserve zone)
    h = ach.get("Heat")
    if h:
        hc = b["cfg"].heat
        panel_title("Heat balance, hot zone", ("Check mode: reported only, the optimum is unchanged. " if hc.get("mode") == "check"
                                               else "Floor mode: the optimiser must also meet the heat-balance carbon need. ")
                    + ("Calibrated: " + hc.get("calibration_note", "") if hc.get("calibrated")
                       else "Placeholder values, not calibrated: read the differences between runs, not the absolute surplus."))
        hcols = st.columns(4)
        sur = h["C_surplus_kg"]
        items = [("Carbon charged", f"{h['C_supplied_kg']:.1f} kg", "fixed carbon, per tHM", "p", "local_fire_department"),
                 ("Carbon the hot zone needs", f"{h['C_need_kg']:.1f} kg", "tuyere + reaction carbon, per tHM", "p", "thermostat"),
                 ("Surplus / deficit", f"{sur:+.1f} kg C", f"{h['surplus_MJ']:+,.0f} MJ per tHM", "g" if sur >= 0 else "a", "balance"),
                 ("Heat-balance minimum fuel", f"{h['fuel_min_kg']:.1f} kg", f"plant rule {ach['Fuel_rule']:.1f} kg", "", "insights")]
        for col, (lab, val, sub, kd, ic) in zip(hcols, items):
            col.markdown(kpi(lab, val, sub, kd, icon=ic), unsafe_allow_html=True)
        st.caption("Details, every heat term and the comparison with the plant thumb rules are on Burden & cost > Heat balance. "
                   "Calibrate with a plant run (Inputs > Heat balance) or with real plant months (Heat audit).")

    # ---- run history and notes
    left, right = st.columns([1, 1.25])
    with left:
        panel_title("Cost across runs")
        if len(S.history) >= 2:
            st.plotly_chart(fig_history(S.history), config={"displayModeBar": False}, **W)
        else:
            st.caption("Run again after a change to compare the cost here.")
    with right:
        panel_title("Notes from this run")
        st.markdown(notes_html([x for x in diag[1:] if not str(x).startswith("SINTER DECISION")] if len(diag) > 1 else diag), unsafe_allow_html=True)


# ============================================================================= INPUTS
def _k(name):
    return f"cfg_{name}_{S.cfg_ver}"


def f_num(label, attr, lo, hi, step, fmt="%.2f", scale=1.0, help=None):
    cur = float(getattr(S.cfg, attr)) * scale
    val = min(max(cur, float(lo)), float(hi))
    new = st.number_input(label, min_value=float(lo), max_value=float(hi), value=float(val), step=float(step), format=fmt, key=_k(attr), help=help)
    if not math.isclose(new, cur, rel_tol=0.0, abs_tol=1e-12):
        setattr(S.cfg, attr, new / scale)
        mark_changed("Inputs")


def f_chk(label, attr, help=None):
    cur = bool(getattr(S.cfg, attr))
    new = st.checkbox(label, value=cur, key=_k(attr), help=help)
    if new != cur:
        setattr(S.cfg, attr, new)
        mark_changed("Inputs")


def f_sel(label, attr, options, fmt=None, help=None):
    cur = getattr(S.cfg, attr)
    new = st.selectbox(label, options, index=options.index(cur) if cur in options else 0, format_func=fmt or str, key=_k(attr), help=help)
    if new != cur:
        setattr(S.cfg, attr, new)
        mark_changed("Inputs")


def inputs_page():
    page_header("Inputs", "Plant policy, slag limits and the fuel-rate rules. Every change asks for a rerun.")
    top = st.columns([4, 1.2])
    with top[1]:
        if st.button("Reset to plant defaults", key="reset_cfg", **W):
            S.cfg = opt.Config()
            S.cfg_ver += 1
            mark_changed("Inputs")
            st.rerun()
    conflict_slot = st.container()          # filled at the end of the page, after the widgets below have updated the settings

    t1, t2, t3, t4, t5 = st.tabs(["Policy", "Slag limits", "Fuel-rate rules", "Stock and pricing", "Heat balance"])

    with t1:
        a, b, c = st.columns(3)
        with a:
            panel_title("Sinter and ore")
            f_num("Guard rail: lowest sinter share, % of sinter + ore", "sinter_min", 1, 99, 1, "%.1f", 100.0,
                  help="The optimiser chooses the sinter share freely between the two guard rails.")
            f_num("Guard rail: highest sinter share, % of sinter + ore", "sinter_max", 1, 99, 1, "%.1f", 100.0)
            f_chk("Pin the sinter share instead of letting the optimiser choose", "sinter_manual_on",
                  "Solved exactly at the value below, 0 % (all ore) to 100 % (all sinter). For what-if work; a value outside the guard rails is flagged.")
            if S.cfg.sinter_manual_on:
                f_num("Pinned sinter share, %", "sinter_manual_pct", 0, 100, 1, "%.1f")
        with b:
            panel_title("Fuel policy")
            f_num("PCI, kg per tHM (fixed)", "pci_fixed_kgthm", 0, 300, 5, "%.1f")
            f_num("Nut coke, kg per tHM", "nut_coke_kgthm", 0, 150, 5, "%.1f")
            f_sel("Nut coke rule", "nut_coke_mode", ["fixed", "cap"], lambda v: {"fixed": "Exactly this rate when a nut coke is on", "cap": "At most this rate"}[v])
            f_num("Minor materials cap, kg per tHM", "minor_max_kgthm", 0, 500, 10, "%.0f", help="BHQ, Mn ore and sponge iron together.")
            f_chk("Use minor materials only when the limits cannot be met without them", "minor_demand_only")
        with c:
            panel_title("Hot metal and Fe closure")
            f_num("Fe charged per 100 kg hot metal", "fe_required_per_100kg", 50, 99.9, 0.1, "%.2f", help="Matched exactly, not a range.")
            f_sel("Fe closure counts", "fe_closure_basis", ["burden", "all"], lambda v: {"burden": "Ore + sinter + minor (plant convention)", "all": "Every charged material"}[v])
            f_num("Si in hot metal, %", "hm_si_pct", 0.05, 3.0, 0.05, "%.2f")
            f_num("C in hot metal, %", "hm_c_pct", 3.0, 5.5, 0.1, "%.2f", help="Placeholder pending plant data. It only sets the carbon sanity floor.")
            f_num("Fe in hot metal, %", "hm_fe_pct", 85.0, 98.0, 0.5, "%.1f")
            f_num("Fe/C guide (diagnostic only)", "fe_c_target", 0.5, 5.0, 0.05, "%.2f")
            f_num("Fe/C guide tolerance, +/-", "fe_c_tol", 0.0, 2.0, 0.01, "%.2f")

    with t2:
        a, b, c = st.columns(3)
        with a:
            panel_title("Basicity B2 = CaO / SiO2")
            f_num("Minimum", "basicity_min", 0.5, 2.0, 0.01, "%.3f")
            f_num("Maximum", "basicity_max", 0.5, 2.0, 0.01, "%.3f")
        with b:
            panel_title("MgO in slag, %")
            f_num("Minimum ", "mgo_min_pct", 0.0, 30.0, 0.1, "%.2f")
            f_num("Maximum ", "mgo_max_pct", 0.0, 30.0, 0.1, "%.2f")
        with c:
            panel_title("Al2O3 in slag, %")
            f_num("Minimum  ", "al2o3_min_pct", 0.0, 40.0, 0.1, "%.2f")
            f_num("Maximum  ", "al2o3_max_pct", 0.0, 40.0, 0.1, "%.2f")
        st.caption("Slag SiO2 is the charged SiO2 less the SiO2 that is reduced into the metal as Si. "
                   f"MgO/Al2O3 is reported against a {S.cfg.mgo_al2o3_guide_lo:g}-{S.cfg.mgo_al2o3_guide_hi:g} drainage guide (diagnostic only).")
        g1, g2 = st.columns(2)
        with g1:
            f_num("MgO/Al2O3 guide, minimum", "mgo_al2o3_guide_lo", 0.0, 2.0, 0.01, "%.2f")
        with g2:
            f_num("MgO/Al2O3 guide, maximum", "mgo_al2o3_guide_hi", 0.0, 2.0, 0.01, "%.2f")
        f_num("Sulphur partition Ks (slag/metal), used for the predicted S only", "ks_fixed", 1.0, 200.0, 1.0, "%.1f",
              help="Plant value 25. Predicted S in hot metal = all charged S / (1 + Ks x slag / hot metal).")

    with t3:
        a, b = st.columns([1, 2])
        with a:
            panel_title("Furnace")
            names = list(S.cfg.furnace_profiles)
            f_sel("Furnace profile", "furnace_name", names)
            st.caption(f"Base fuel {S.cfg.furnace_profiles.get(S.cfg.furnace_name, {}).get('base', 0):g} kg/tHM at the plant reference point "
                       "(ideal conditions: reference hot blast, reference coke quality, steady state).")
            panel_title("Fe content and fuel")
            rules = S.cfg.material_rules
            for rk, nm in (("ore_fe", "Iron ore"), ("sinter_fe", "Sinter")):
                ca, cb = st.columns(2)
                nc = ca.number_input(f"{nm}: kg fuel per +1 pt Fe", 0.0, 50.0, float(rules[rk]["coef"]), 0.5, key=_k(rk + "_coef"))
                nr = cb.number_input(f"{nm}: reference Fe %", 0.0, 100.0, float(rules[rk]["ref"]), 0.1, key=_k(rk + "_ref"))
                if not math.isclose(nc, rules[rk]["coef"], abs_tol=1e-12) or not math.isclose(nr, rules[rk]["ref"], abs_tol=1e-12):
                    rules[rk]["coef"], rules[rk]["ref"] = float(nc), float(nr)
                    mark_changed("Fuel-rate rules")
        with b:
            panel_title("Thumb rules in use", "Switch a rule off for a what-if; every result then names it. Base fuel is always on.")
            cols_ = st.columns(2)
            for i, (key, (lab, desc)) in enumerate(opt.RULE_SWITCHES.items()):
                cur = bool(S.cfg.fuel_terms.get(key, True))
                new = cols_[i % 2].toggle(lab, value=cur, key=_k("rule_" + key), help=desc)
                if new != cur:
                    S.cfg.fuel_terms[key] = bool(new)
                    mark_changed("Fuel-rate rules")
            if S.cfg.rules_off():
                if st.button("All rules on (plant rule set)", key="rules_all_on"):
                    S.cfg.fuel_terms = {k: True for k in opt.RULE_SWITCHES}
                    S.cfg_ver += 1
                    mark_changed("Fuel-rate rules")
                    st.rerun()
            st.caption("Sinter/flux moisture is an extension, not a plant thumb rule. Removed from the model in the plant review: "
                       "PCI rate, PCI FC, PCI moisture, coke ash, coke CSR, DRI credit, hot blast, furnace offset, burden-Fe form.")

        with st.expander("All rule coefficients"):
            keys = list(opt.MATERIAL_RULES)
            tbl = pd.DataFrame([{"Rule": opt.TERM_LABELS[k],
                                 "Applies to": f"{opt.MATERIAL_RULES[k]['attr']} of " + ", ".join(GROUP_LABEL.get(p, p) for p in opt.MATERIAL_RULES[k]["pools"]),
                                 "Fuel effect": "rises above reference" if opt.MATERIAL_RULES[k]["sign"] > 0 else "falls above reference",
                                 "kg per point": S.cfg.material_rules[k]["coef"], "Reference": S.cfg.material_rules[k]["ref"]} for k in keys])
            ed = st.data_editor(tbl, hide_index=True, disabled=["Rule", "Applies to", "Fuel effect"], key=_k("rules_tbl"), **W,
                                column_config={"kg per point": st.column_config.NumberColumn(min_value=0.0, format="%.3f"),
                                               "Reference": st.column_config.NumberColumn(format="%.3f")})
            for i, k in enumerate(keys):
                nc, nr = ed.iloc[i]["kg per point"], ed.iloc[i]["Reference"]
                if pd.notna(nc) and pd.notna(nr) and (not math.isclose(nc, S.cfg.material_rules[k]["coef"], abs_tol=1e-12)
                                                      or not math.isclose(nr, S.cfg.material_rules[k]["ref"], abs_tol=1e-12)):
                    S.cfg.material_rules[k]["coef"], S.cfg.material_rules[k]["ref"] = float(nc), float(nr)
                    mark_changed("Fuel-rate rules")
            st.caption("Coefficients are defined at the plant's reference charge and converted to a per-kg form so the model stays linear.")

        with st.expander("Other thumb-rule constants and furnace profiles"):
            a, b = st.columns(2)
            with a:
                f_num("Reference slag, kg per tHM", "slag_ref_kgthm", 100, 600, 5, "%.0f")
                f_num("Fuel per kg slag over reference", "fuel_per_kg_slag", 0, 1, 0.01, "%.3f")
                f_num("Reference sinter share, %", "sinter_ref_pct", 0, 100, 1, "%.1f")
                f_num("Fuel per point of sinter share (either side of the reference)", "fuel_per_pct_sinter", 0, 3, 0.05, "%.3f",
                      help="Plant thumb rule: +10 points of sinter in (sinter + ore) = -10 kg coke, and vice versa, i.e. 1.0.")
            with b:
                f_num("Reference raw flux, kg per tHM", "raw_flux_ref_kgthm", 0, 100, 0.5, "%.1f")
                f_num("Fuel per kg raw flux over reference", "fuel_per_kg_raw_flux", 0, 1, 0.01, "%.3f",
                      help="Limestone / dolomite charged straight to the furnace. Literature: 20-35 kg coke per 100 kg limestone.")
                f_num("Raw-flux threshold, CaO + MgO %", "raw_flux_min_cao_mgo", 0, 100, 1, "%.1f",
                      help="A Flux row at or above this counts as limestone / dolomite for the raw-flux rule.")
            st.markdown("<div class='panel-note'><b>Reference charge rates behind the per-point rules, kg per tHM</b></div>", unsafe_allow_html=True)
            rc = st.columns(3)
            for i_, rk in enumerate(("ore", "sinter", "coke")):
                cur = float(S.cfg.ref_rates.get(rk, opt.REF_RATES[rk]))
                nv = rc[i_].number_input({"ore": "Ore", "sinter": "Sinter", "coke": "Coke + nut coke"}[rk], 1.0, max(3000.0, cur), cur, 5.0,
                                         format="%.0f", key=_k("ref_" + rk))
                if not math.isclose(nv, cur, abs_tol=1e-12):
                    S.cfg.ref_rates[rk] = float(nv)
                    mark_changed("Fuel-rate rules")
            prof = pd.DataFrame(S.cfg.furnace_profiles).T.rename(columns={"base": "Base fuel, kg/tHM"})
            edp = st.data_editor(prof, key=_k("profiles"), **W, column_config={
                "Base fuel, kg/tHM": st.column_config.NumberColumn(min_value=0.0, format="%.1f")})
            newp = {n: {"base": float(edp.loc[n, "Base fuel, kg/tHM"])} for n in edp.index if pd.notna(edp.loc[n, "Base fuel, kg/tHM"])}
            if newp and newp != S.cfg.furnace_profiles:
                S.cfg.furnace_profiles = newp
                mark_changed("Furnace profiles")

    with t4:
        a, b = st.columns(2)
        with a:
            panel_title("Raw-material stock")
            f_num("Planned hot metal for the stock period, t (0 = no stock caps)", "stock_plan_hm_tonnes", 0, 10_000_000, 1000, "%.0f",
                  help="With a plan, each material's stock also caps how much of it a tonne of hot metal can use.")
            f_num("Stock balance (1 = split by stock, 0 = cost decides)", "stock_balance", 0.0, 1.0, 0.05, "%.2f",
                  help="Applies within groups that have two or more materials on and stock entered. If it cannot be met it is relaxed as little as possible.")
        with b:
            panel_title("Prices")
            f_sel("Prices are per", "price_basis", ["dry", "wet"], lambda v: {"dry": "Dry tonne (plant convention)", "wet": "Wet tonne (converted by moisture)"}[v])
            st.caption("Quantities in the model are always dry and net. Wet quantities are shown on the Burden & cost page.")
            f_num("O&M cost, Rs per tonne of hot metal", "om_rs_thm", 0.0, 100_000.0, 50.0, "%.0f",
                  help="Operations and maintenance cost of the furnace. It is added to the raw-material cost in the reported cost per tHM. It does not change the burden.")
            panel_title("Model constants", "Plant defaults; change only with a reason.")
            f_num("Direct-reduction degree for the carbon floor", "dr_degree_floor", 0.0, 1.0, 0.01, "%.2f",
                  help="Only sets the stoichiometric carbon sanity floor. The heat balance has its own DRR.")
            f_num("Mn reduction efficiency", "mn_reduction_eff", 0.0, 1.0, 0.01, "%.2f")

    with t5:
        st.caption("Hot zone below the thermal reserve zone. Check mode reports the heat-balance minimum fuel next to the plant rule and "
                   "leaves the optimum unchanged; floor mode makes the optimiser also meet it. The values are literature placeholders "
                   "until calibrated with a plant run (below) or with real plant months (Heat audit page).")
        heat_controls(S.cfg.heat, _k, lambda: mark_changed("Heat balance"))

        def _new_heat(hd):
            S.cfg.heat = hd
            S.cfg_ver += 1
            mark_changed("Heat balance")
            st.rerun()
        calibration_block(S.cfg.heat, _k, _new_heat)

    probs = S.cfg.problems()
    if probs:
        with conflict_slot:
            st.markdown(notice("These settings conflict:", "<br>".join(f"- {p}" for p in probs), "r"), unsafe_allow_html=True)


# ============================================================================= MATERIALS & STOCK
def materials_page():
    page_header("Materials & stock", "One row per material. Switch it on or off, then set price, moisture, chemistry and stock.")
    df = S.df
    cols = st.columns(len(opt.GROUPS))
    for col, g in zip(cols, opt.GROUPS):
        sub = df[df["Group"] == g]
        col.markdown(kpi(GROUP_LABEL[g], f"{int(sub['Available'].sum())} of {len(sub)}", "switched on", "" if len(sub) else "a"), unsafe_allow_html=True)
    st.write("")
    errs = opt.validate_df(opt.ensure_columns(df))
    if errs:
        st.markdown(notice("The applied table has problems:", "<br>".join(f"- {e}" for e in errs[:10]), "r"), unsafe_allow_html=True)

    nc = st.column_config.NumberColumn
    cc = {"Material": st.column_config.TextColumn("Material", required=True),
          "Group": st.column_config.SelectboxColumn("Group", options=opt.GROUPS, required=True),
          "Available": st.column_config.CheckboxColumn("On", help="Untick to take the material out of the optimisation."),
          "Price_Rs_t": nc("Price Rs/t", min_value=0.0, format="%.0f", help="Per dry tonne unless Inputs > Stock and pricing says wet."),
          "Moisture_Pct": nc("Moisture %", min_value=0.0, max_value=59.9, format="%.2f"),
          "Fe": nc("Fe %", min_value=0.0, max_value=100.0, format="%.3f"), "CaO": nc("CaO %", min_value=0.0, max_value=100.0, format="%.3f"),
          "MgO": nc("MgO %", min_value=0.0, max_value=100.0, format="%.3f"), "SiO2": nc("SiO2 %", min_value=0.0, max_value=100.0, format="%.3f"),
          "Al2O3": nc("Al2O3 %", min_value=0.0, max_value=100.0, format="%.3f"), "Mn": nc("Mn %", min_value=0.0, format="%.3f"),
          "S": nc("S %", min_value=0.0, format="%.3f"), "FC": nc("FC %", min_value=0.0, max_value=100.0, format="%.2f"),
          "RM_Stock": nc("RM stock, t", min_value=0.0, format="%.0f", help="Blank = unlimited. 0 = treated as unavailable."),
          "Fines_Pct": nc("Fines %", min_value=0.0, max_value=99.9, format="%.1f", help="Ore only. Blank = no fines credit."),
          "Fines_Credit_Rs_t": nc("Fines credit Rs/t", min_value=0.0, format="%.0f")}
    order = ["Material", "Group", "Available", "Price_Rs_t", "Moisture_Pct", "RM_Stock", "Fe", "CaO", "MgO", "SiO2", "Al2O3", "Mn", "S", "FC",
             "Fines_Pct", "Fines_Credit_Rs_t"]
    edited = st.data_editor(opt.ensure_columns(df).reset_index(), key=f"mat_editor_{S.mat_ver}", num_rows="dynamic", hide_index=True,
                            column_config=cc, column_order=order, **W)
    try:
        newdf = frame_to_master(edited)
        pending = opt.fingerprint(newdf) != opt.fingerprint(df)
    except Exception:
        newdf, pending = None, False
    a, b, c = st.columns([1, 1, 4])
    if a.button("Apply changes", type="primary", disabled=not pending, key="mat_apply"):
        e2 = opt.validate_df(newdf)
        if e2:
            st.markdown(notice("Not applied.", "<br>".join(f"- {e}" for e in e2[:10]), "r"), unsafe_allow_html=True)
        else:
            S.df = newdf
            if not S.source.endswith("(edited)"):
                S.source = S.source + " (edited)"
            S.mat_ver += 1
            mark_changed("Materials")
            st.rerun()
    if b.button("Discard edits", disabled=not pending, key="mat_discard"):
        S.mat_ver += 1
        st.rerun()
    if pending:
        c.markdown("<div style='padding-top:.45rem;color:#F0B94E'>You have edits that are not applied yet.</div>", unsafe_allow_html=True)
    st.caption("Add a row at the bottom of the table for a new material. Prices are per dry tonne. Blank stock means unlimited; a stock of 0 removes the material. "
               "Minor materials (BHQ, Mn ore, sponge iron) are used only when the limits need them.")


# ============================================================================= BURDEN & COST
def burden_page():
    page_header("Burden & cost", "The optimised burden in detail: recipe, slag, fuel rate, moisture and stock.")
    need_result()
    b = S.bundle
    T = {k: (title, t) for k, title, t in b["tables"]}
    tabs = st.tabs(["Recipe", "Slag and limits", "Oxide sources", "Fuel rate", "Fe impact", "Moisture", "Stock use", "Sinter curve 0-100 %", "Heat balance", "Diagnostics"])

    with tabs[0]:
        both = st.checkbox("Also show materials that were not used", value=False, key="recipe_all")
        bf = b["burden_full"].copy()
        if not both:
            bf = bf[(bf["Result"] == "USED") | (bf["Material"] == "TOTAL")]
        bf["Group"] = bf["Group"].map(lambda g: GROUP_LABEL.get(g, g))
        show(bf, status_cols=("Availability", "Result"))
        st.caption("Dry kg is the model basis. Wet kg = dry / (1 - moisture). Cost uses the price basis chosen under Inputs.")

    with tabs[1]:
        panel_title("Slag chemistry", "SiO2 is net of the Si reduced into the metal.")
        show(T["slag"][1], status_cols=("Status",))
        panel_title("Every requirement")
        show(b["compliance"], status_cols=("Status",))

    with tabs[2]:
        panel_title("Where the slag oxides come from", "kg per tHM and % of the total charged, then the SiO2 reduced into the metal, ending on the slag.")
        if b.get("compact_oxides") is not None:
            st.plotly_chart(fig_oxide_sources(b["compact_oxides"]), config={"displayModeBar": False}, **W)
        ox = T["oxides"][1].copy()
        ox["Group"] = ox["Group"].map(lambda g: GROUP_LABEL.get(g, g))
        show(ox)
        if T["oxides"][1].attrs.get("note"):
            st.caption(T["oxides"][1].attrs["note"])

    with tabs[3]:
        panel_title("Fuel balance")
        show(T["fuel"][1])
        panel_title("Why the fuel rate is what it is", "Red adds fuel, green saves fuel, relative to the plant reference point.")
        bd = T["breakdown"][1]
        terms = bd[~bd["Term"].str.startswith(("Base", "TOTAL"))]
        terms = terms[terms["kg/tHM"].abs() > 0.005]
        if len(terms):
            terms = terms.iloc[::-1]
            fig = go.Figure(go.Bar(x=terms["kg/tHM"], y=terms["Term"], orientation="h",
                                   marker_color=["#FF6B6B" if v > 0 else "#3FD6A8" for v in terms["kg/tHM"]],
                                   text=[f"{v:+.2f}" for v in terms["kg/tHM"]], textposition="outside", cliponaxis=False))
            fig.update_xaxes(gridcolor="#22304C", zeroline=True, zerolinecolor="#5B6B8C", title="kg fuel per tHM")
            st.plotly_chart(_layout(fig, max(200, 34 * len(terms) + 60), margin=(10, 50, 10, 40)), config={"displayModeBar": False}, **W)
        show(bd)

    with tabs[4]:
        panel_title("Effect of Fe content on fuel", "Negative means the Fe content of that material saves fuel.")
        show(T["fe_impact"][1])

    with tabs[5]:
        if "moisture" in T:
            show(T["moisture"][1])
        else:
            st.caption("No moisture data for this run.")

    with tabs[6]:
        if "stock" in T:
            show(T["stock"][1])
        else:
            st.caption("No stock was entered for the materials in use. Add stock under Materials & stock.")

    with tabs[7]:
        cv = b.get("curve")
        if cv is not None:
            st.markdown(notes_html([cv.attrs.get("note", "")]), unsafe_allow_html=True)
            show(cv, status_cols=("Status",))
        else:
            st.caption("No sinter curve for this run.")

    with tabs[8]:
        if "heat" in T:
            h, hc = b["ach"]["Heat"], b["cfg"].heat
            st.markdown(notice(f"Heat balance, {hc.get('mode', 'check')} mode, "
                               + ("calibrated." if hc.get("calibrated") else "placeholder values (not calibrated)."),
                               opt_heat_note(h), "g" if hc.get("calibrated") else "w"), unsafe_allow_html=True)
            dd = pd.DataFrame({"Term": [opt.HEAT_TERM_LABELS[k_] for k_ in h["D"]], "MJ": [v / 1000.0 for v in h["D"].values()]})
            dd = dd.iloc[::-1]
            fig = go.Figure(go.Bar(x=dd["MJ"], y=dd["Term"], orientation="h", marker_color="#FF8B6B",
                                   text=[f"{v:,.0f}" for v in dd["MJ"]], textposition="outside", cliponaxis=False))
            fig.update_xaxes(gridcolor="#22304C", title="MJ per tHM")
            panel_title("Hot-zone heat demand", "Every term that the heat from carbon burnt at the tuyeres, with the hot blast, must cover.")
            st.plotly_chart(_layout(fig, 34 * len(dd) + 70, margin=(10, 60, 10, 40)), config={"displayModeBar": False}, **W)
            show(T["heat"][1])
            if T["heat"][1].attrs.get("note"):
                st.caption(T["heat"][1].attrs["note"])
            panel_title("Heat balance against the plant thumb rules", "Marginal fuel effects: what the rule says and what the hot-zone balance says.")
            show(T["heat_rules"][1])
            cv = b.get("curve")
            if cv is not None and cv.attrs.get("heat_note"):
                st.caption("0-100 % sinter curve: " + cv.attrs["heat_note"])
        else:
            st.caption("No heat balance for this run.")

    with tabs[9]:
        show(T["diagnostics"][1])
        with st.expander("Settings used for this run"):
            show(pd.DataFrame(b["settings"], columns=["Section", "Setting", "Value", "Note"]).astype(str))


# ============================================================================= SCENARIO ANALYSIS
def _store(key, df_out, title):
    S.analysis[key] = {"df": df_out, "time": dt.datetime.now(), "fingerprint": opt.fingerprint(S.df), "title": title}


def _stale(item):
    if item["fingerprint"] != opt.fingerprint(S.df):
        st.markdown(notice("Made with earlier materials.", "The table has changed since. Run it again to refresh.", "w"), unsafe_allow_html=True)


def scenario_page():
    page_header("Scenario analysis", "Re-solve the full model across sinter shares, assays, prices and rule settings to see what really drives cost and coke.")
    blk = blockers()
    if blk:
        st.markdown(notice("Fix these before running a scenario:", "<br>".join(f"- {x}" for x in blk[:8]), "r"), unsafe_allow_html=True)
        return
    st.caption("Every tool uses the applied materials and the current Inputs (including any thumb rules switched off). "
               "Each result is kept and added to the Excel workbook under Reports & export.")
    t1, t2, t3, t4, t5 = st.tabs(["Sinter sweep", "Assay sensitivity", "Price sensitivity", "Tornado", "Sinter break-even"])

    with t1:
        st.caption("Each row is the full model re-solved with the sinter share pinned at that value, plus the optimiser's own choice. "
                   "Also shows which material brings the CaO, SiO2 and Al2O3 at each share.")
        a, b, c, d = st.columns([1, 1, 1, 1.2], vertical_alignment="bottom")
        s0 = a.number_input("From, %", 0.0, 100.0, float(round(S.cfg.sinter_min * 100)), 1.0, key="sw_from")
        s1 = b.number_input("To, %", 0.0, 100.0, float(round(S.cfg.sinter_max * 100)), 1.0, key="sw_to")
        stp = c.number_input("Step, points", 0.1, 20.0, 2.5, 0.5, key="sw_step")
        if d.button("Run sinter sweep", type="primary", key="run_sweep", **W):
            try:
                bar = st.progress(0.0, text="Solving")
                out, oxl = opt.sweep(S.df, S.cfg, s0, s1, stp, progress=lambda i, n: bar.progress(i / n, text=f"Solved {i} of {n}"))
                bar.empty()
                _store("sweep", out, "Sinter sweep - model re-solved with the sinter share pinned at each value")
                _store("sweep_oxides", oxl, "Oxides by sinter share - kg charged per tHM by material")
            except ValueError as e:
                st.markdown(notice("The sweep could not run.", str(e), "r"), unsafe_allow_html=True)
        item = S.analysis.get("sweep")
        if item:
            out = item["df"]
            _stale(item)
            note = out.attrs.get("note")
            if note:
                st.markdown(notes_html([note]), unsafe_allow_html=True)
            ok = out[(out["Status"] == "Optimal") & (out["Sinter share %"] != "optimizer's choice")]
            free = out[out["Sinter share %"] == "optimizer's choice"]
            if len(ok) >= 2:
                c1, c2 = st.columns(2)
                for col, ycol, ttl, colr in ((c1, "Coke kg", "Regular coke, kg per tHM", "#4C8DFF"), (c2, "Cost Rs/tHM", "Burden cost, Rs per tHM", "#F5A85C")):
                    fig = go.Figure(go.Scatter(x=ok["Sinter %"], y=ok[ycol], mode="lines+markers", line=dict(color=colr, width=3), marker=dict(size=7),
                                               name=ttl))
                    if len(free) and free.iloc[0]["Status"] == "Optimal":
                        fig.add_trace(go.Scatter(x=[free.iloc[0]["Sinter %"]], y=[free.iloc[0][ycol]], mode="markers",
                                                 marker=dict(symbol="star", size=17, color="#3FD6A8"), name="Optimiser's choice"))
                    fig.update_xaxes(title="Sinter share, %", showgrid=False)
                    fig.update_yaxes(title=ttl, gridcolor="#22304C", tickformat=",")
                    col.plotly_chart(_layout(fig, 270, margin=(10, 10, 10, 40)), config={"displayModeBar": False}, **W)
            disp = out.copy().dropna(axis=1, how="all")
            disp["Sinter share %"] = disp["Sinter share %"].map(lambda v: "Optimiser's choice" if v == "optimizer's choice" else nice(float(v)))
            show(disp, status_cols=("Status",))
            ox_item = S.analysis.get("sweep_oxides")
            if ox_item is not None and len(ox_item["df"]):
                panel_title("CaO, SiO2 and Al2O3 by material at each sinter share", "kg charged per tHM (before the SiO2 reduced into the metal).")
                for ox in ("CaO", "SiO2", "Al2O3"):
                    st.plotly_chart(fig_oxides_by_sinter(ox_item["df"], ox), config={"displayModeBar": False}, **W)
                    with st.expander(f"{ox} table"):
                        pv = opt.oxides_by_sinter_pivot(ox_item["df"], ox)
                        pv["Point"] = pv["Point"].map(lambda v: "Optimiser's choice" if v == "optimizer's choice" else nice(float(v)))
                        show(pv)

    with t2:
        st.caption("Shift one assay of every material that is switched on in a group, and re-solve. Pin the sinter share under Inputs to keep the mix fixed while you test.")
        a, b, c, d = st.columns([1.2, 1, 1, 1.2], vertical_alignment="bottom")
        grp = a.selectbox("Group", opt.GROUPS, index=0, format_func=lambda g: GROUP_LABEL[g], key="sens_group")
        lab = {"Fe": "Fe %", "SiO2": "SiO2 %", "Al2O3": "Al2O3 %", "CaO": "CaO %", "MgO": "MgO %", "Moisture_Pct": "Moisture %"}
        meas = b.selectbox("Assay", list(opt.ASSAY_COLUMNS), format_func=lambda m: lab[m], key="sens_col")
        stp2 = c.number_input("Step, points", 0.1, 10.0, 1.0, 0.5, key="sens_step")
        if d.button("Run sensitivity", type="primary", key="run_sens", **W):
            try:
                with st.spinner("Solving five cases..."):
                    out = opt.sensitivity(S.df, S.cfg, grp, meas, stp2)
                _store("sensitivity", out, f"Assay sensitivity - {lab[meas]} of every ON {GROUP_LABEL[grp]} material shifted")
            except ValueError as e:
                st.markdown(notice("The sensitivity could not run.", str(e), "r"), unsafe_allow_html=True)
        item = S.analysis.get("sensitivity")
        if item:
            out = item["df"]
            _stale(item)
            ok = out[out["Status"] == "Optimal"]
            if len(ok) >= 2 and "Coke kg" in ok.columns:
                c1, c2 = st.columns(2)
                for col, ycol, ttl, colr in ((c1, "Coke kg", "Regular coke, kg per tHM", "#4C8DFF"), (c2, "Cost Rs/tHM", "Burden cost, Rs per tHM", "#F5A85C")):
                    fig = go.Figure(go.Scatter(x=ok["Change (pts)"], y=ok[ycol], mode="lines+markers", line=dict(color=colr, width=3), marker=dict(size=8)))
                    fig.update_xaxes(title="Change in assay, points", showgrid=False)
                    fig.update_yaxes(title=ttl, gridcolor="#22304C", tickformat=",")
                    col.plotly_chart(_layout(fig, 260, margin=(10, 10, 10, 40)), config={"displayModeBar": False}, **W)
            show(out, status_cols=("Status",))
            st.caption(item["title"])

    with t3:
        st.caption("Change the price of one material, or of every switched-on material in a group, and re-solve.")
        a, b, c = st.columns([1.6, 1, 1.2], vertical_alignment="bottom")
        targets = opt.price_targets(opt.ensure_columns(S.df))
        tgt = a.selectbox("Price of", targets, key="price_tgt") if targets else None
        stp3 = b.number_input("Step, %", 1.0, 50.0, 10.0, 1.0, key="price_step")
        if c.button("Run price sensitivity", type="primary", key="run_price", disabled=tgt is None, **W):
            try:
                with st.spinner("Solving five cases..."):
                    out = opt.price_sens(S.df, S.cfg, tgt, stp3)
                _store("price", out, f"Price sensitivity - {tgt} price changed by +/- {stp3:g} % steps")
            except ValueError as e:
                st.markdown(notice("The price sensitivity could not run.", str(e), "r"), unsafe_allow_html=True)
        item = S.analysis.get("price")
        if item:
            out = item["df"]
            _stale(item)
            ok = out[out["Status"] == "Optimal"]
            if len(ok) >= 2:
                c1, c2 = st.columns(2)
                for col, ycol, ttl, colr in ((c1, "Cost Rs/tHM", "Burden cost, Rs per tHM", "#F5A85C"), (c2, "Sinter %", "Sinter share chosen, %", "#3FD6B0")):
                    fig = go.Figure(go.Scatter(x=ok["Price change %"], y=ok[ycol], mode="lines+markers", line=dict(color=colr, width=3), marker=dict(size=8)))
                    fig.update_xaxes(title="Price change, %", showgrid=False)
                    fig.update_yaxes(title=ttl, gridcolor="#22304C", tickformat=",")
                    col.plotly_chart(_layout(fig, 260, margin=(10, 10, 10, 40)), config={"displayModeBar": False}, **W)
            show(out, status_cols=("Status",))
            st.caption(out.attrs.get("note", ""))

    with t4:
        st.caption("Every price, fuel-rule coefficient and key limit moved down and up one at a time, ranked by how much it moves the burden cost. "
                   "Prices and rules move by the swing %; limits by fixed steps (Al2O3 / MgO caps 0.5 pt, B2 floor and cap 0.01, Fe requirement 0.5).")
        a, b = st.columns([1, 1.2], vertical_alignment="bottom")
        sw = a.number_input("Swing, %", 1.0, 50.0, 10.0, 1.0, key="tor_swing")
        if b.button("Run tornado", type="primary", key="run_tornado", **W):
            try:
                with st.spinner("Moving every driver down and up (about 20-40 seconds)..."):
                    out = opt.tornado_run(S.df, S.cfg, sw)
                _store("tornado", out, f"Tornado - every driver moved one at a time (prices and rules +/- {sw:g} %)")
            except ValueError as e:
                st.markdown(notice("The tornado could not run.", str(e), "r"), unsafe_allow_html=True)
        item = S.analysis.get("tornado")
        if item:
            out = item["df"]
            _stale(item)
            st.plotly_chart(fig_tornado(out), config={"displayModeBar": False}, **W)
            st.caption(out.attrs.get("note", ""))
            show(out, status_cols=("Status",))

    with t5:
        st.caption("Sinter prices (all switched-on sinters moved together) at which the optimiser's free choice drops to the lower guard rail "
                   "or reaches the upper one. Compare them with the MARGINAL cost of sinter, not the full absorbed cost.")
        if S.cfg.sinter_manual_on:
            st.markdown(notice("The sinter share is pinned.", "Untick the pin under Inputs > Policy: the break-even needs the optimiser's free choice.", "w"),
                        unsafe_allow_html=True)
        elif st.button("Find break-even sinter prices", type="primary", key="run_be"):
            try:
                with st.spinner("Searching (about 15-30 seconds)..."):
                    out = opt.breakeven(S.df, S.cfg)
                _store("breakeven", out, "Sinter break-even - sinter prices at which the free choice hits a guard rail")
            except ValueError as e:
                st.markdown(notice("The break-even could not run.", str(e), "r"), unsafe_allow_html=True)
        item = S.analysis.get("breakeven")
        if item:
            _stale(item)
            show(item["df"])
            st.caption(item["df"].attrs.get("note", ""))


# ============================================================================= REPORTS & EXPORT
def reports_page():
    page_header("Reports & export", "Build a formatted Excel workbook of the last run: inputs, results, charts and every setting used.")
    if S.bundle is None:
        st.markdown(notice("No run to export yet.", "Run the optimiser from the Dashboard first."), unsafe_allow_html=True)
        return
    b = S.bundle
    ok = b["status"] == "Optimal"
    st.markdown(notice(f"Last run: {b['furnace']}, {b['created']:%d %b %Y %H:%M}.",
                       f"Status {b['status']}" + (f", cost ₹ {b['cost']:,.0f} per tHM." if ok else ". The workbook will hold the inputs and the reason."),
                       "g" if ok else "r"), unsafe_allow_html=True)
    sheets = ["Summary"] + (["Sinter Curve 0-100%"] if b.get("curve") is not None else []) + ["Inputs", "Run Settings"] \
        + (["Optimised Burden", "Slag & Chemistry", "Fuel Rate", "Fe Impact", "Heat Balance", "Moisture"] if ok else [])
    if ok and any(t[0] == "stock" for t in b["tables"]):
        sheets.append("RM Stock")
    for key, nm, _c in opt.ANALYSIS_SHEETS:
        it = S.analysis.get(key)
        if it:
            same = it["fingerprint"] == b["fingerprint"]
            sheets.append(nm + ("" if same else " (materials changed since it was made)"))
    extra = extra_export_items() if ok else []
    sheets += [it[0] for it in extra]
    st.markdown("**The workbook will contain:** " + ", ".join(sheets) + ".")
    st.caption("Every tool you ran under Scenario analysis (sweep, assay, price, tornado, break-even) is added as its own sheet, and so are "
               "the Trends and Slag oxides tables you opened for this run, the run history and your saved scenarios.")

    if st.button("Export optimised results", type="primary", key="export_build"):
        with st.spinner("Building the workbook..."):
            fname = f"MBF_Optimised_Results_{b['furnace']}_{b['created']:%Y%m%d_%H%M%S}.xlsx"
            S.export = (opt.export_bytes(b, S.analysis, extra=an.extra_sheets(extra) if extra else None), fname, b["created"])
    if S.export and S.export[2] == b["created"]:
        st.download_button("Download workbook (.xlsx)", S.export[0], file_name=S.export[1], key="export_dl",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("The file is ready. Nothing downloads until you click the button above.")
    if S.changed:
        st.caption("Settings or materials have changed since this run. The workbook holds the last run with its own inputs and settings.")


# ============================================================================= HEAT AUDIT
def opt_heat_note(h):
    """The engine's one-line heat note, without the leading tag."""
    return opt.heat_note(h).split(": ", 1)[-1] if ": " in opt.heat_note(h) else opt.heat_note(h)


@st.cache_data(show_spinner=False)
def _audit_template(example):
    return opt.audit_template_bytes(example=example)


def audit_page():
    page_header("Heat audit", "Measure the heat-balance parameters from real plant months, then apply them to the model.")
    hc = S.cfg.heat
    st.markdown(notice("Heat model now: " + ("calibrated." if hc.get("calibrated") else "placeholder values, not calibrated."),
                       hc.get("calibration_note", ""), "g" if hc.get("calibrated") else "w"), unsafe_allow_html=True)
    if S.audit_msg:
        st.markdown(notice(*S.audit_msg), unsafe_allow_html=True)
        S.audit_msg = None

    a, b = st.columns(2)
    with a:
        panel_title("1  Get the template", "One workbook per plant month: Month, Materials and Cooling sheets, and the questions for the plant.")
        st.download_button("Template with a worked example (.xlsx)", _audit_template(True), file_name="MBF_Heat_Audit_Template_Example.xlsx",
                           key="aud_tpl_ex", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", **W)
        st.download_button("Blank template (.xlsx)", _audit_template(False), file_name="MBF_Heat_Audit_Template.xlsx", key="aud_tpl_blank",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", **W)
        with st.expander("Questions the plant must answer"):
            st.markdown("<br>".join(f"{i}. {q}" for i, q in enumerate(opt.AUDIT_Q_QUESTIONS, 1)), unsafe_allow_html=True)
        st.caption("Choose steady months (no long stoppage, blow-down or hanging). Three or more months at different sinter shares "
                   "let the audit estimate how the degree of direct reduction changes with sinter share.")
    with b:
        panel_title("2  Load plant months", f"{len(S.audit_recs)} month(s) loaded"
                    + (": " + ", ".join(str(r["name"]) for r in S.audit_recs) if S.audit_recs else "."))
        files = st.file_uploader("Plant-month workbooks (.xlsx)", type=["xlsx"], accept_multiple_files=True, key=f"aud_up_{S.aud_ver}")
        if files:
            recs, errs = [], []
            for f in files:
                try:
                    recs.append(opt.audit_read(f.getvalue(), f.name))
                except Exception as e:
                    errs.append(f"{f.name}: {e}")
            if errs:
                st.markdown(notice("Some files could not be read:", "<br>".join(errs), "r"), unsafe_allow_html=True)
            if recs and st.button(f"Use these {len(recs)} month(s)", type="primary", key="aud_use"):
                S.audit_recs, S.audit = recs, None
                S.aud_ver += 1
                st.rerun()
        c1, c2 = st.columns(2)
        if c1.button("Try it with 4 synthetic months", key="aud_demo", **W,
                     help="Four model-generated months at 55, 62, 70 and 77 % sinter with a known DRR slope. For trying the audit only."):
            S.audit_recs, S.audit = opt.audit_examples(), None
            st.rerun()
        if c2.button("Clear the months", key="aud_clear", disabled=not S.audit_recs, **W):
            S.audit_recs, S.audit = [], None
            S.aud_ver += 1
            st.rerun()

    panel_title("3  Run the audit")
    if st.button("Run heat audit", type="primary", key="aud_run", disabled=not S.audit_recs):
        with st.spinner("Auditing the plant months..."):
            S.audit = opt.audit_run_cfg(S.audit_recs, S.cfg)
    A = S.audit
    if A is None:
        st.caption("Load at least one plant month, then run the audit. Nothing changes in the model until you apply the result.")
        return
    usable = sum(1 for r in A["results"] if r["usable"])
    st.markdown(notice(f"{len(A['results'])} month(s) audited, {usable} usable.", "Months that fail the balances are named below and "
                       "left out of the result.", "g" if usable else "r"), unsafe_allow_html=True)
    mt = A["months"].copy()
    show(mt.set_index("Month").T.reset_index().rename(columns={"index": "Quantity"}).astype(str))
    if A.get("fit_text"):
        st.markdown(notes_html(A["fit_text"]), unsafe_allow_html=True)
    if A["flags"]:
        st.markdown(notes_html(["NOTE: " + f for f in A["flags"]]), unsafe_allow_html=True)
    with st.expander("Heat terms by month, MJ per tHM"):
        show(pd.DataFrame(opt.HEAT_TERM_ROWS(A)))
    for per, tbl in A.get("sensitivity", {}).items():
        with st.expander(f"How far the result moves for plausible measurement errors: {per}"):
            show(tbl)

    panel_title("4  Apply to the model", "Writes the measured blast, enthalpies, degree of direct reduction and hot-zone losses into the "
                                          "heat settings of this session.")
    modes = {"measured": "Measured only (recommended): losses = the hot-zone cooling water",
             "anchored": "Measured + absorb the average residual into the losses (an anchor, not a measurement)"}
    mode = st.radio("How to apply", list(modes), format_func=modes.get, key="aud_mode")
    x, y = st.columns(2)
    if x.button("Apply to model", type="primary", key="aud_apply", disabled=not usable, **W):
        try:
            new_heat, note = opt.audit_apply_cfg(A, S.cfg, mode)
        except ValueError as e:
            st.markdown(notice("Not applied.", str(e), "r"), unsafe_allow_html=True)
        else:
            S.cfg.heat = new_heat
            S.cfg_ver += 1
            mark_changed("Heat audit")
            S.audit_msg = ("Applied to the model.", note + ". Run the optimiser to see the effect.", "g")
            st.rerun()
    x.caption("The heat balance stays in check mode unless you switch it to floor under Inputs > Heat balance.")
    y.download_button("Download the audit results (.xlsx)", opt.audit_export_bytes(A, S.cfg), file_name="MBF_Heat_Audit_Results.xlsx",
                      key="aud_dl", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", **W)


# ============================================================================= UPLOAD & SETTINGS
@st.cache_data(show_spinner=False)
def _template():
    return opt.template_bytes()


def settings_page():
    page_header("Upload & settings", "The materials workbook, the input template, and this session's settings.")
    a, b = st.columns(2)
    with a:
        panel_title("Materials workbook")
        st.markdown(notice(S.source, f"{len(S.df)} materials, {int(S.df['Available'].sum())} switched on."
                           + (" Placeholder demo values." if S.demo else ""), "w" if S.demo else "g"), unsafe_allow_html=True)
        if S.master_notes:
            st.markdown(notes_html(["NOTE: " + n for n in S.master_notes]), unsafe_allow_html=True)
        master_uploader("set")
        st.download_button("Download the input template (.xlsx)", _template(), file_name="MBF_Input_Template.xlsx", key="tmpl_dl",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("One sheet, one row per material. The template holds demo values; replace every price and assay before relying on a result.")
        if not S.demo and st.button("Go back to the demo table", key="to_demo"):
            S.df = opt.demo_df()
            S.source, S.demo, S.master_notes = "Built-in demo table", True, []
            S.mat_ver += 1
            reset_results()
            st.rerun()
        if S.demo and st.button("I have entered plant values, hide the demo notice", key="hide_demo"):
            S.demo = False
            st.rerun()
    with b:
        panel_title("This session")
        if st.button("Reset all settings to plant defaults", key="reset_all", **W):
            S.cfg = opt.Config()
            S.cfg_ver += 1
            mark_changed("Inputs")
            st.rerun()
        if st.button("Clear results and run history", key="clear_res", **W):
            reset_results()
            S.history = []
            S.last_ok = None
            st.rerun()
        if st.button("Run the engine self-test", key="selftest_btn", **W):
            S.selftest = opt.quick_self_test()
        if S.selftest:
            passed = sum(ok for _, ok, _ in S.selftest)
            st.markdown(notice(f"Self-test: {passed} of {len(S.selftest)} passed.", "", "g" if passed == len(S.selftest) else "r"), unsafe_allow_html=True)
            show(pd.DataFrame([{"Check": n, "Result": "OK" if ok else "OUT OF LIMIT", "Detail": d} for n, ok, d in S.selftest]), status_cols=("Result",))
        st.caption(f"Engine {opt.ENGINE_VERSION}. Settings and results belong to this browser session; they are lost when the page is closed, "
                   "so export a workbook to keep a run.")


# ============================================================================= V6: shared helpers
SCEN_COLORS = ["#A78BFA", "#F472B6", "#FACC15"]
PLOT_CFG = {"displayModeBar": False}


def cached(kind, df, cfg, fn, *extra):
    """Session cache for analyses: recalculated only when the materials, the settings or the parameters change."""
    key = (kind,) + an.run_key(df, cfg, *extra)
    if key not in S.cache:
        if len(S.cache) > 60:
            S.cache.clear()
        S.cache[key] = fn()
    return S.cache[key]


def run_base():
    """Materials, settings, result and bundle of the last run. The analysis pages work on these so they match the Dashboard."""
    b = S.bundle
    return b["inputs"], b["cfg"], S.result, b


def keep(name, df_out, title, note=""):
    """Remember the latest table of a V6 analysis for the Excel export (tied to the run it was made from)."""
    S.v6[name] = {"df": df_out, "title": title, "note": note, "run": S.bundle["created"] if S.bundle else None}


def stale_run_notice():
    if S.changed:
        st.markdown(notice("These analyses use the last run.", f"{S.changed_source} changed since. Press Run optimiser on the Dashboard to refresh them.", "w"),
                    unsafe_allow_html=True)


def vs_last_run_text():
    """'vs run n' line: the change in cost, coke and sinter share against the previous optimal run, and what changed."""
    if len(S.history) < 2 or S.history[-1]["Run"] != S.runs or S.result is None or S.result[0] != "Optimal":
        return None
    a, b = S.history[-2], S.history[-1]
    return (f"vs run {a['Run']}: cost {b['Cost Rs/tHM'] - a['Cost Rs/tHM']:+,.0f} Rs/tHM, coke {b['Coke kg/tHM'] - a['Coke kg/tHM']:+.1f} kg, "
            f"sinter {a['Sinter %']:.1f} % → {b['Sinter %']:.1f} %. Changed: {b.get('Changed', '')}")


def recipe_table(b):
    bf = b["burden_full"]
    rec = bf[(bf["Result"] == "USED") | (bf["Material"] == "TOTAL")][
        ["Material", "Group", "Dry kg/tHM", "Wet kg/tHM", "% of burden", "Share of group %", "Price Rs/t (dry)", "Cost Rs/tHM"]].copy()
    rec["Group"] = rec["Group"].map(lambda g: GROUP_LABEL.get(g, g))
    for col, dec in (("Dry kg/tHM", 1), ("Wet kg/tHM", 1), ("% of burden", 2), ("Share of group %", 1), ("Price Rs/t (dry)", 0), ("Cost Rs/tHM", 0)):
        rec[col] = pd.to_numeric(rec[col], errors="coerce").astype(float).round(dec)
    return rec.reset_index(drop=True)


def save_scenario_row(prefix):
    if S.result is None or S.result[0] != "Optimal":
        return
    a, b, c = st.columns([2.2, 1, 3.2], vertical_alignment="bottom")
    name = a.text_input("Save this run as a scenario", value=f"Run {S.runs}", key=f"{prefix}_scen_name_{S.runs}",
                        help="Saved scenarios can be compared side by side and overlaid on the Trends page.")
    if b.button("Save scenario", key=f"{prefix}_scen_save", icon=":material/bookmark_add:", **W):
        sc = an.scenario_from_bundle(name, S.bundle)
        S.scenarios = [x for x in S.scenarios if x["name"] != sc["name"]] + [sc]
        S.scenarios = S.scenarios[-8:]
        c.markdown(f"<div style='padding-bottom:.5rem;color:#3FD6A8'>Saved as “{sc['name']}”. {len(S.scenarios)} scenario(s) saved; compare them under Trends.</div>",
                   unsafe_allow_html=True)


# ============================================================================= V7: Adjust inputs panel (every model input)
_DIALOG_ON_DISMISS = "on_dismiss" in inspect.signature(st.dialog).parameters


def open_quick_inputs():
    S.qi_draft = {"cfg": copy.deepcopy(S.cfg), "df": opt._fdf(opt.ensure_columns(S.df))}
    S.qi_ver += 1
    S.qi_show = True


def close_quick_inputs():
    S.qi_draft = None
    S.qi_show = False


def _quick_inputs():
    quick_inputs_body()


# The panel stays open across reruns until it is applied, cancelled or dismissed (Streamlit >= 1.50 reports a dismiss;
# on older versions it is shown only on the run in which the button was pressed).
quick_inputs = (st.dialog("Adjust inputs", width="large", on_dismiss=close_quick_inputs)(_quick_inputs) if _DIALOG_ON_DISMISS
                else st.dialog("Adjust inputs", width="large")(_quick_inputs))


def _changed(a, b, tol=1e-9):
    return not math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)


QI_TABS = ["Materials", "Prices & on/off", "Sinter & fuel policy", "Slag", "Hot metal & Fe", "Fuel-rate rules",
           "Heat balance", "Stock & constants"]
MAT_FIELDS = [("Fe", "Fe %", 100.0, "%.3f"), ("CaO", "CaO %", 100.0, "%.3f"), ("MgO", "MgO %", 100.0, "%.3f"),
              ("SiO2", "SiO2 %", 100.0, "%.3f"), ("Al2O3", "Al2O3 %", 100.0, "%.3f"), ("Mn", "Mn %", 100.0, "%.3f"),
              ("S", "S %", 100.0, "%.3f"), ("FC", "FC %", 100.0, "%.2f"), ("Moisture_Pct", "Moisture %", 59.9, "%.2f")]
OPT_FIELDS = [("RM_Stock", "RM stock, t", 1e9, "%.0f", "Blank = unlimited. 0 = treated as unavailable."),
              ("Fines_Pct", "Fines %", 99.9, "%.1f", "Ore only. Blank = no fines credit."),
              ("Fines_Credit_Rs_t", "Fines credit, Rs/t", 1e7, "%.0f", "Value of the fines, used with Fines %.")]
HEAT_EDIT_NOTE = "heat settings edited by hand - not calibrated"


def heat_controls(h, key, on_change, cols=3):
    """Every heat-balance setting of the heat dict h. on_change() is called after any edit (the dict is edited in place)."""
    modes = ["check", "floor"]
    lab = {"check": "Check only: report the heat balance, leave the optimum unchanged",
           "floor": "Floor: the LP must also meet the heat-balance carbon need"}
    new = st.selectbox("Heat balance mode", modes, index=modes.index(h.get("mode", "check")), format_func=lab.get, key=key("heat_mode"),
                       help="Use floor only after the heat model has been calibrated with plant data.")
    if new != h.get("mode"):
        h["mode"] = new
        on_change()
    cc = st.columns(cols)
    for i, (hk, (label, lo, hi, step, fmt, hlp)) in enumerate(opt.HEAT_FIELDS.items()):
        cur = float(h.get(hk, opt.HEAT_DEFAULT[hk]))
        v = cc[i % cols].number_input(label, float(min(lo, cur)), float(max(hi, cur)), cur, float(step), format=fmt,
                                      key=key("heat_" + hk), help=hlp)
        if _changed(v, cur, 1e-12):
            h[hk] = float(v)
            h["calibrated"] = False
            h["calibration_note"] = HEAT_EDIT_NOTE
            on_change()
    ok = bool(h.get("calibrated"))
    body = h.get("calibration_note", "")
    if h.get("mode") == "floor" and not ok:
        body += ". Floor mode on uncalibrated placeholders changes the optimum - use it only after calibration."
    st.markdown(notice("Heat model: " + ("calibrated." if ok else "placeholder values, not calibrated."), body, "g" if ok else "w"),
                unsafe_allow_html=True)


def _heat_params(h):
    return {k: h.get(k) for k in opt.HEAT_DEFAULT if k not in ("loss_MJ_tHM", "calibrated", "calibration_note")}


def calibration_block(h, key, apply_new):
    """'Calibrate losses to the last run' - only when the heat settings (other than the losses) match that run."""
    b = S.bundle
    can = b is not None and b.get("status") == "Optimal"
    same = can and _heat_params(b["cfg"].heat) == _heat_params(h)
    a, b_ = st.columns(2)
    if a.button("Calibrate losses to the last run", key=key("heat_calib"), disabled=not same, **W,
                help="Sets the lower-furnace losses so the heat balance closes exactly at the last run's burden. Only differences "
                     "from that burden then carry information, until the heat audit replaces this anchor."):
        try:
            apply_new(opt.calibrate_losses(b["ach"], b["cfg"]))
        except ValueError as e:
            st.markdown(notice("Not calibrated.", str(e), "r"), unsafe_allow_html=True)
    if b_.button("Reset heat settings to the literature placeholders", key=key("heat_reset"), **W):
        apply_new(opt.heat_placeholders())
    if not can:
        st.caption("Calibration needs an optimal run first.")
    elif not same:
        st.caption("The heat settings differ from the last run: apply and run first, then calibrate.")


def quick_inputs_body():
    D = S.qi_draft
    if D is None:
        st.caption("Nothing to edit.")
        return
    c, d = D["cfg"], D["df"]
    k = lambda n: f"qi{S.qi_ver}_{n}"
    st.caption("Every input of the model is here. Your edits stay a draft until you press Apply and run. They change the same "
               "settings as the Inputs and Materials & stock pages, so the pages and this panel always agree.")

    def qnum(label, attr, lo, hi, step, fmt="%.2f", scale=1.0, help=None, where=None, disabled=False):
        cur = float(getattr(c, attr)) * scale
        v = (where or st).number_input(label, float(min(lo, cur)), float(max(hi, cur)), cur, float(step), format=fmt,
                                       key=k("cfg_" + attr), help=help, disabled=disabled)
        if _changed(v, cur, 1e-12):
            setattr(c, attr, float(v) / scale)

    def qsel(label, attr, options, fmt=None, help=None, where=None):
        cur = getattr(c, attr)
        v = (where or st).selectbox(label, options, index=options.index(cur) if cur in options else 0, format_func=fmt or str,
                                    key=k("cfg_" + attr), help=help)
        if v != cur:
            setattr(c, attr, v)

    def qtog(label, attr, help=None, where=None):
        v = (where or st).toggle(label, value=bool(getattr(c, attr)), key=k("cfg_" + attr), help=help)
        setattr(c, attr, bool(v))

    def band(label, lo_attr, hi_attr, a, b_, step, fmt, dec):
        v0, v1 = float(getattr(c, lo_attr)), float(getattr(c, hi_attr))
        x, y = st.slider(label, float(min(a, v0)), float(max(b_, v1)), (v0, v1), float(step), format=fmt, key=k("cfg_" + lo_attr))
        if _changed(x, v0, 1e-9):
            setattr(c, lo_attr, round(float(x), dec))
        if _changed(y, v1, 1e-9):
            setattr(c, hi_attr, round(float(y), dec))

    tabs = st.tabs(QI_TABS)

    # ------------------------------------------------------------------ materials: every column of every material
    with tabs[0]:
        st.caption("Chemistry, moisture, stock and fines of any material, switched on or off. Price and on/off are in the next tab.")
        if len(d.index):
            ov = d.reset_index()[["Material", "Group", "Available", "Price_Rs_t", "Moisture_Pct", "Fe", "CaO", "MgO", "SiO2", "Al2O3",
                                  "Mn", "S", "FC", "RM_Stock", "Fines_Pct", "Fines_Credit_Rs_t"]]
            ov["Group"] = ov["Group"].map(lambda g: GROUP_LABEL.get(g, g))
            ov["Available"] = ov["Available"].map(lambda v: "ON" if v else "OFF")
            show(ov.rename(columns={"Available": "On", "Price_Rs_t": "Price Rs/t", "Moisture_Pct": "Moisture %", "RM_Stock": "Stock t",
                                    "Fines_Pct": "Fines %", "Fines_Credit_Rs_t": "Fines credit"}), status_cols=("On",),
                 height=min(420, 35 * (len(ov) + 1) + 4))
            m = st.selectbox("Material to edit", list(d.index), key=k("mat_pick"),
                             format_func=lambda x: f"{x} ({GROUP_LABEL.get(str(d.loc[x, 'Group']), d.loc[x, 'Group'])}, "
                                                   f"{'on' if bool(d.loc[x, 'Available']) else 'off'})")
            g0 = str(d.loc[m, "Group"])
            g = st.selectbox("Group", opt.GROUPS, index=opt.GROUPS.index(g0) if g0 in opt.GROUPS else 0,
                             format_func=lambda x: GROUP_LABEL.get(x, x), key=k(f"mat_{m}_Group"))
            if g != g0:
                d.loc[m, "Group"] = g
            cc = st.columns(3)
            for i, (col, lab, top, fmt) in enumerate(MAT_FIELDS):
                cur = float(d.loc[m, col])
                v = cc[i % 3].number_input(lab, 0.0, float(max(top, cur)), cur, 0.1, format=fmt, key=k(f"mat_{m}_{col}"))
                if _changed(v, cur, 1e-12):
                    d.loc[m, col] = float(v)
            cc = st.columns(3)
            for i, (col, lab, top, fmt, hlp) in enumerate(OPT_FIELDS):
                cur = d.loc[m, col]
                blank = bool(pd.isna(cur))
                use = cc[i].checkbox(f"Enter {lab.split(',')[0].lower()}", value=not blank, key=k(f"mat_{m}_{col}_on"), help=hlp)
                if use:
                    base_v = 0.0 if blank else float(cur)
                    v = cc[i].number_input(lab, 0.0, float(max(top, base_v)), base_v, 1.0, format=fmt, key=k(f"mat_{m}_{col}"))
                    if (blank and v > 0) or (not blank and _changed(v, base_v, 1e-12)):     # a blank stays blank until a value is typed
                        d.loc[m, col] = float(v)
                elif not blank:
                    d.loc[m, col] = np.nan
            if st.button(f"Remove {m} from the table", key=k(f"mat_{m}_remove")):
                D["df"] = d.drop(index=m)
                S.qi_ver += 1
                st.rerun()
        with st.expander("Add a material"):
            a, b_, c_ = st.columns([1.3, 1.2, 0.8], vertical_alignment="bottom")
            nm = a.text_input("Name", key=k("new_name"), placeholder="for example Ore3")
            ng = b_.selectbox("Group", opt.GROUPS, format_func=lambda x: GROUP_LABEL.get(x, x), key=k("new_group"))
            if c_.button("Add", key=k("new_add"), **W):
                name = opt.sanitize_name(nm) if str(nm).strip() else ""
                if not name:
                    st.markdown(notice("Not added.", "Give the material a name.", "r"), unsafe_allow_html=True)
                elif name in d.index:
                    st.markdown(notice("Not added.", f"{name} is already in the table.", "r"), unsafe_allow_html=True)
                else:
                    row = {col: 0.0 for col in opt.NUM_COLS}
                    row.update({"Group": ng, "Available": False, "RM_Stock": np.nan, "Fines_Pct": np.nan, "Fines_Credit_Rs_t": np.nan})
                    D["df"] = pd.concat([d, pd.DataFrame([row], index=pd.Index([name], name=d.index.name))])[opt.COLUMNS[1:]]
                    S.qi_ver += 1
                    st.rerun()
            st.caption("A new material starts switched off with zero assays and price. Fill it in, then switch it on under Prices & on/off.")

    # ------------------------------------------------------------------ prices and on/off of every material
    with tabs[1]:
        st.caption("Price per dry tonne (per wet tonne if Sinter & fuel policy says so). Switched-off materials keep their price for later.")
        for g in opt.GROUPS:
            ms = [m for m in d.index if str(d.loc[m, "Group"]) == g]
            if not ms:
                continue
            st.markdown(f"<div class='panel-note' style='margin-top:.4rem'><b>{GROUP_LABEL.get(g, g)}</b></div>", unsafe_allow_html=True)
            for m in ms:
                a, b_ = st.columns([1, 1.3], vertical_alignment="center")
                on = a.toggle(m, value=bool(d.loc[m, "Available"]), key=k("on_" + m))
                if on != bool(d.loc[m, "Available"]):
                    d.loc[m, "Available"] = bool(on)
                cur = float(d.loc[m, "Price_Rs_t"])
                v = b_.number_input(f"{m} price, Rs/t", 0.0, float(max(1e7, cur)), cur, 50.0, format="%.0f", key=k("price_" + m),
                                    label_visibility="collapsed")
                if _changed(v, cur):
                    d.loc[m, "Price_Rs_t"] = float(v)

    # ------------------------------------------------------------------ sinter and fuel policy
    with tabs[2]:
        a, b_ = st.columns(2)
        with a:
            st.markdown("<div class='panel-note'><b>Sinter : ore</b></div>", unsafe_allow_html=True)
            qtog("Pin the sinter share (what-if)", "sinter_manual_on")
            pin = bool(c.sinter_manual_on)
            v = st.slider("Pinned sinter share, % of sinter + ore", 0.0, 100.0, float(c.sinter_manual_pct), 0.5,
                          key=k("cfg_sinter_manual_pct"), disabled=not pin)
            if _changed(v, c.sinter_manual_pct):
                c.sinter_manual_pct = round(float(v), 2)
            lo0, hi0 = c.sinter_min * 100, c.sinter_max * 100
            lo, hi = st.slider("Guard rails for the optimiser's free choice, %", 1.0, 99.0,
                               (min(max(lo0, 1.0), 99.0), min(max(hi0, 1.0), 99.0)), 1.0, key=k("cfg_sinter_min"), disabled=pin)
            if _changed(lo, lo0, 1e-6):
                c.sinter_min = round(lo / 100.0, 4)
            if _changed(hi, hi0, 1e-6):
                c.sinter_max = round(hi / 100.0, 4)
            names = list(c.furnace_profiles)
            qsel("Furnace", "furnace_name", names)
            qsel("Prices are per", "price_basis", ["dry", "wet"],
                 lambda v: {"dry": "Dry tonne (plant convention)", "wet": "Wet tonne (converted by moisture)"}[v])
            qnum("O&M cost, Rs per tHM", "om_rs_thm", 0.0, 100_000.0, 50.0, "%.0f", help="Added to the raw-material cost. Does not change the burden.")
        with b_:
            st.markdown("<div class='panel-note'><b>Fuel and minor materials</b></div>", unsafe_allow_html=True)
            qnum("PCI, kg per tHM (fixed)", "pci_fixed_kgthm", 0.0, 300.0, 5.0, "%.1f")
            qnum("Nut coke, kg per tHM", "nut_coke_kgthm", 0.0, 150.0, 5.0, "%.1f")
            qsel("Nut coke rule", "nut_coke_mode", ["fixed", "cap"],
                 lambda v: {"fixed": "Exactly this rate when a nut coke is on", "cap": "At most this rate"}[v])
            qnum("Minor materials cap, kg per tHM", "minor_max_kgthm", 0.0, 500.0, 10.0, "%.0f", help="BHQ, Mn ore and sponge iron together.")
            qtog("Use minor materials only when the limits cannot be met without them", "minor_demand_only")

    # ------------------------------------------------------------------ slag
    with tabs[3]:
        band("Basicity B2 = CaO / SiO2", "basicity_min", "basicity_max", 0.80, 1.40, 0.01, "%.2f", 3)
        band("MgO in slag, %", "mgo_min_pct", "mgo_max_pct", 4.0, 12.0, 0.1, "%.1f", 2)
        band("Al2O3 in slag, %", "al2o3_min_pct", "al2o3_max_pct", 12.0, 24.0, 0.1, "%.1f", 2)
        a, b_, c_ = st.columns(3)
        qnum("Ks (S partition slag/metal)", "ks_fixed", 1.0, 200.0, 1.0, "%.1f", help="Used for the predicted S only.", where=a)
        qnum("MgO/Al2O3 guide, minimum", "mgo_al2o3_guide_lo", 0.0, 2.0, 0.01, "%.2f", help="Diagnostic only.", where=b_)
        qnum("MgO/Al2O3 guide, maximum", "mgo_al2o3_guide_hi", 0.0, 2.0, 0.01, "%.2f", help="Diagnostic only.", where=c_)

    # ------------------------------------------------------------------ hot metal and Fe closure
    with tabs[4]:
        a, b_ = st.columns(2)
        with a:
            qnum("Fe charged per 100 kg hot metal", "fe_required_per_100kg", 50.0, 99.9, 0.1, "%.2f", help="Matched exactly, not a range.")
            qsel("Fe closure counts", "fe_closure_basis", ["burden", "all"],
                 lambda v: {"burden": "Ore + sinter + minor (plant convention)", "all": "Every charged material"}[v])
            qnum("Fe in hot metal, %", "hm_fe_pct", 85.0, 98.0, 0.5, "%.1f")
        with b_:
            qnum("Si in hot metal, %", "hm_si_pct", 0.05, 3.0, 0.05, "%.2f")
            qnum("C in hot metal, %", "hm_c_pct", 3.0, 5.5, 0.1, "%.2f", help="Placeholder pending plant data.")
            x, y = st.columns(2)
            qnum("Fe/C guide", "fe_c_target", 0.5, 5.0, 0.05, "%.2f", help="Diagnostic only.", where=x)
            qnum("Fe/C tolerance, +/-", "fe_c_tol", 0.0, 2.0, 0.01, "%.2f", where=y)

    # ------------------------------------------------------------------ fuel-rate rules
    with tabs[5]:
        st.markdown("<div class='panel-note'><b>Thumb rules in use</b> (base fuel is always on)</div>", unsafe_allow_html=True)
        cols = st.columns(4)
        for i, (key, (lab, desc)) in enumerate(opt.RULE_SWITCHES.items()):
            c.fuel_terms[key] = bool(cols[i % 4].toggle(lab, value=bool(c.fuel_terms.get(key, True)), key=k("rule_" + key), help=desc))
        st.markdown("<div class='panel-note' style='margin-top:.5rem'><b>Base fuel by furnace, kg per tHM</b></div>", unsafe_allow_html=True)
        cols = st.columns(max(2, len(c.furnace_profiles)))
        for i, (fn, pr) in enumerate(list(c.furnace_profiles.items())):
            cur = float(pr["base"])
            v = cols[i].number_input(fn, 0.0, float(max(2000.0, cur)), cur, 1.0, format="%.1f", key=k("base_" + fn))
            if _changed(v, cur):
                c.furnace_profiles[fn] = {"base": float(v)}
        st.markdown("<div class='panel-note' style='margin-top:.5rem'><b>Per-point rules: kg fuel per point, and the reference</b></div>",
                    unsafe_allow_html=True)
        for rk in opt.MATERIAL_RULES:
            a, b_, c_ = st.columns([1.5, 1, 1], vertical_alignment="bottom")
            r = opt.MATERIAL_RULES[rk]
            a.markdown(f"{opt.TERM_LABELS[rk]}<br><span class='panel-note'>{r['attr'].replace('_Pct', '').replace('Moisture', 'moisture')} of "
                       + ", ".join(GROUP_LABEL.get(p, p) for p in r["pools"]) + f"; fuel {'rises' if r['sign'] > 0 else 'falls'} above the reference</span>",
                       unsafe_allow_html=True)
            cc_ = float(c.material_rules[rk]["coef"])
            v = b_.number_input("kg per point", 0.0, float(max(50.0, cc_)), cc_, 0.5, format="%.3f", key=k(f"rule_{rk}_coef"))
            if _changed(v, cc_, 1e-12):
                c.material_rules[rk]["coef"] = float(v)
            rr = float(c.material_rules[rk]["ref"])
            v = c_.number_input("Reference", 0.0, 100.0, rr, 0.1, format="%.3f", key=k(f"rule_{rk}_ref"))
            if _changed(v, rr, 1e-12):
                c.material_rules[rk]["ref"] = float(v)
        st.markdown("<div class='panel-note' style='margin-top:.5rem'><b>Slag, sinter-share and raw-flux rules</b></div>", unsafe_allow_html=True)
        a, b_, c_ = st.columns(3)
        qnum("Reference slag, kg per tHM", "slag_ref_kgthm", 100.0, 600.0, 5.0, "%.0f", where=a)
        qnum("Fuel per kg slag over reference", "fuel_per_kg_slag", 0.0, 1.0, 0.01, "%.3f", where=a)
        qnum("Reference sinter share, %", "sinter_ref_pct", 0.0, 100.0, 1.0, "%.1f", where=b_)
        qnum("Fuel per point of sinter share", "fuel_per_pct_sinter", 0.0, 3.0, 0.05, "%.3f", where=b_,
             help="Plant thumb rule: +10 points of sinter = -10 kg coke, and vice versa, i.e. 1.0.")
        qnum("Reference raw flux, kg per tHM", "raw_flux_ref_kgthm", 0.0, 100.0, 0.5, "%.1f", where=c_)
        qnum("Fuel per kg raw flux over reference", "fuel_per_kg_raw_flux", 0.0, 1.0, 0.01, "%.3f", where=c_)
        st.markdown("<div class='panel-note' style='margin-top:.5rem'><b>Reference charge rates behind the per-point rules, kg per tHM</b></div>",
                    unsafe_allow_html=True)
        cols = st.columns(3)
        for i, rk in enumerate(("ore", "sinter", "coke")):
            cur = float(c.ref_rates.get(rk, opt.REF_RATES[rk]))
            v = cols[i].number_input({"ore": "Ore", "sinter": "Sinter", "coke": "Coke + nut coke"}[rk], 1.0, float(max(3000.0, cur)), cur, 5.0,
                                     format="%.0f", key=k("ref_" + rk), help="Per-kg rule weight = kg per point / this rate.")
            if _changed(v, cur):
                c.ref_rates[rk] = float(v)

    # ------------------------------------------------------------------ heat balance
    with tabs[6]:
        st.caption("Hot zone below the thermal reserve zone. Values are literature placeholders until calibrated with a plant run "
                   "or the Heat audit page.")
        heat_controls(c.heat, k, lambda: None)

        def _new_heat(hd):
            c.heat = hd
            S.qi_ver += 1
            st.rerun()
        calibration_block(c.heat, k, _new_heat)

    # ------------------------------------------------------------------ stock and model constants
    with tabs[7]:
        a, b_ = st.columns(2)
        with a:
            st.markdown("<div class='panel-note'><b>Raw-material stock</b></div>", unsafe_allow_html=True)
            qnum("Planned hot metal for the stock period, t (0 = no stock caps)", "stock_plan_hm_tonnes", 0.0, 10_000_000.0, 1000.0, "%.0f")
            qnum("Stock balance (1 = split by stock, 0 = cost decides)", "stock_balance", 0.0, 1.0, 0.05, "%.2f")
        with b_:
            st.markdown("<div class='panel-note'><b>Model constants</b> (plant defaults; change only with a reason)</div>", unsafe_allow_html=True)
            qnum("Raw-flux threshold, CaO + MgO %", "raw_flux_min_cao_mgo", 0.0, 100.0, 1.0, "%.1f",
                 help="A Flux row at or above this counts as limestone / dolomite for the raw-flux rule.")
            qnum("Direct-reduction degree for the carbon floor", "dr_degree_floor", 0.0, 1.0, 0.01, "%.2f",
                 help="Only sets the stoichiometric carbon sanity floor. The heat balance has its own DRR.")
            qnum("Mn reduction efficiency", "mn_reduction_eff", 0.0, 1.0, 0.01, "%.2f")

    changes = an.describe_changes(S.df, d, S.cfg, c)
    st.markdown(notice("Changes to apply:", "; ".join(changes[:8]) + (f" (+{len(changes) - 8} more)" if len(changes) > 8 else ""), "g")
                if changes else notice("No changes yet.", "Edit any tab, then press Apply and run."), unsafe_allow_html=True)
    x, y, z = st.columns([1.3, 1.2, 1])
    apply = x.button("Apply and run", type="primary", key=k("apply"), disabled=not changes, **W)
    if y.button("Plant default settings", key=k("defaults"), help="Resets the settings in this draft (not the materials).", **W):
        D["cfg"] = opt.Config()
        S.qi_ver += 1
        st.rerun()
    if z.button("Cancel", key=k("cancel"), **W):
        close_quick_inputs()
        st.rerun()
    if apply:
        probs = list(c.problems()) + opt.validate_df(d)
        if probs:
            st.markdown(notice("Not applied:", "<br>".join(f"- {p}" for p in probs[:8]), "r"), unsafe_allow_html=True)
            return
        apply_quick_inputs(c, d)
        st.rerun()


def apply_quick_inputs(c, d):
    """Make the draft the applied settings and materials, then run the optimiser."""
    if opt.fingerprint(d) != opt.fingerprint(opt.ensure_columns(S.df)):
        S.df = d
        if not S.source.endswith("(edited)"):
            S.source = S.source + " (edited)"
        S.mat_ver += 1
    S.cfg = c
    S.cfg_ver += 1
    close_quick_inputs()
    mark_changed("Quick inputs")
    run_optimizer()


# ============================================================================= V6: charts
def fig_oxide_share(frame, name_col, oxides, colors):
    """Stacked % bars: share of each oxide charged, by material or by group."""
    fig = go.Figure()
    tot = {o: float(frame[f"{o} kg"].sum()) for o in oxides}
    ylab = [f"{o}  ({tot[o]:.1f} kg)" for o in oxides]
    for _, r in frame.iterrows():
        vals = [float(r[f"{o} % of total"]) for o in oxides]
        if max(vals) <= 0:
            continue
        kgs = [float(r[f"{o} kg"]) for o in oxides]
        nm = str(r[name_col])
        fig.add_trace(go.Bar(y=ylab, x=vals, orientation="h", name=nm, marker_color=colors.get(nm, "#888"),
                             text=[f"{v:.0f}%" if v >= 5 else "" for v in vals], textposition="inside", insidetextanchor="middle",
                             customdata=kgs, hovertemplate=f"{nm}: %{{x:.1f}} %% (%{{customdata:.2f}} kg/tHM)<extra></extra>"))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(range=[0, 100], title="% of the total charged to the furnace", gridcolor="#22304C")
    fig.update_yaxes(autorange="reversed")
    fig = _layout(fig, 110 + 60 * len(oxides), margin=(10, 10, 46, 30), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)))
    return fig


def fig_oxide_kg(frame, name_col, oxide, colors):
    f = frame[frame[f"{oxide} kg"] > 1e-9].sort_values(f"{oxide} kg")
    fig = go.Figure(go.Bar(x=f[f"{oxide} kg"], y=f[name_col].astype(str), orientation="h",
                           marker_color=[colors.get(str(n), "#888") for n in f[name_col]],
                           text=[f"{v:,.1f} kg ({p:.0f} %)" for v, p in zip(f[f"{oxide} kg"], f[f"{oxide} % of total"])],
                           textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x:,.2f} kg/tHM<extra></extra>"))
    fig.update_xaxes(title=f"{oxide} charged, kg per tHM", gridcolor="#22304C")
    return _layout(fig, max(220, 36 * len(f) + 70), margin=(10, 90, 10, 40))


def fig_oxide_donut(frame, name_col, oxide, colors):
    f = frame[frame[f"{oxide} kg"] > 1e-9]
    fig = go.Figure(go.Pie(labels=f[name_col].astype(str), values=f[f"{oxide} kg"], hole=0.62, sort=True,
                           marker=dict(colors=[colors.get(str(n), "#888") for n in f[name_col]], line=dict(color="#0B1220", width=2)),
                           textinfo="percent", hovertemplate="%{label}: %{value:,.2f} kg (%{percent})<extra></extra>"))
    fig.add_annotation(text=f"<b>{oxide}</b><br><span style='font-size:12px;color:#8B9BC0'>{float(f[f'{oxide} kg'].sum()):,.1f} kg</span>",
                       x=0.5, y=0.5, showarrow=False, font=dict(size=18))
    return _layout(fig, 290, legend=True)


def group_colors(groups):
    return {GROUP_LABEL.get(g, g): GROUP_COLOR.get(g, "#888") for g in groups}


def fig_oxide_area(oxl, sweep_tbl, oxide, run_share, curve):
    """kg of one oxide by material across sinter share 0-100 % (stacked areas), with the slag limit value on a second axis."""
    pv = oxl.pivot_table(index="Sinter %", columns="Material", values=f"{oxide} kg", aggfunc="sum", fill_value=0.0)
    groups = dict(zip(oxl["Material"], oxl["Group"]))
    order = {g: i for i, g in enumerate(opt.GROUPS)}
    mats_ = sorted([m for m in pv.columns if pv[m].abs().max() > 1e-6], key=lambda m: (order.get(groups.get(m), 99), m))
    cols = mat_colors(mats_, groups)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for m in mats_:
        fig.add_trace(go.Scatter(x=pv.index, y=pv[m], name=m, stackgroup="one", mode="lines", line=dict(width=0.5, color=cols[m]),
                                 fillcolor=cols[m], hovertemplate=f"{m}: %{{y:.2f}} kg at %{{x:.0f}} %% sinter<extra></extra>"), secondary_y=False)
    sec = {"CaO": ("B2", "Basicity B2"), "SiO2": ("B2", "Basicity B2"), "Al2O3": ("Al2O3 %", "Al2O3 in slag, %"), "MgO": ("MgO %", "MgO in slag, %")}[oxide]
    ok = sweep_tbl[sweep_tbl["Status"] == "Optimal"].copy()
    if sec[0] in ok.columns and len(ok):
        fig.add_trace(go.Scatter(x=ok["Sinter %"].astype(float), y=ok[sec[0]], name=sec[1], mode="lines+markers",
                                 line=dict(color="#FFFFFF", width=2, dash="dot"), marker=dict(size=5)), secondary_y=True)
    if run_share is not None:
        fig.add_vline(x=run_share, line=dict(color="#3FD6A8", width=2), annotation_text=f"this run {run_share:.1f} %",
                      annotation_font=dict(color="#3FD6A8", size=11))
    lo, hi = (curve.attrs.get("lo"), curve.attrs.get("hi")) if curve is not None else (None, None)
    if hi is not None and hi < 100 - 1e-6:
        fig.add_vrect(x0=hi, x1=100, fillcolor="rgba(255,107,107,.13)", line_width=0, annotation_text="infeasible",
                      annotation_position="top right", annotation_font=dict(size=11, color="#FF6B6B"))
    if lo is not None and lo > 1e-6:
        fig.add_vrect(x0=0, x1=lo, fillcolor="rgba(255,107,107,.13)", line_width=0)
    fig.update_xaxes(range=[0, 100], dtick=10, title="Sinter share of (sinter + ore), %", showgrid=False)
    fig.update_yaxes(title_text=f"{oxide} charged, kg per tHM", gridcolor="#22304C", secondary_y=False)
    fig.update_yaxes(title_text=sec[1], showgrid=False, secondary_y=True)
    fig = _layout(fig, 360, margin=(10, 10, 46, 40), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)))
    return fig


def fig_per_fe(perfe):
    f = perfe.iloc[::-1]
    fig = go.Figure()
    for o, colr in (("SiO2", "#4C8DFF"), ("Al2O3", "#F5A85C")):
        fig.add_trace(go.Bar(y=f["Material"], x=f[f"{o} kg per t Fe"], name=o, orientation="h", marker_color=colr,
                             hovertemplate=f"%{{y}}: %{{x:,.1f}} kg {o} per t Fe<extra></extra>"))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="kg per tonne of Fe delivered", gridcolor="#22304C")
    fig = _layout(fig, max(220, 40 * len(f) + 110), margin=(10, 10, 40, 40), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)))
    return fig


def fig_curve_overlay(items, col, cfg):
    """Coke or cost vs sinter share for this run and saved scenarios. items: (name, curve, choice, color)."""
    fig = go.Figure()
    fig.add_vrect(x0=cfg.sinter_min * 100, x1=cfg.sinter_max * 100, fillcolor="rgba(63,214,168,.08)", line_width=0,
                  annotation_text="guard rails", annotation_position="top left", annotation_font=dict(size=11, color="#8B9BC0"))
    for name, cv, choice, colr in items:
        if cv is None or "Status" not in cv.columns:
            continue
        ok = cv[cv["Status"] == "Optimal"]
        if col in ok.columns and len(ok):
            fig.add_trace(go.Scatter(x=ok["Sinter %"], y=ok[col], mode="lines+markers", line=dict(color=colr, width=3), marker=dict(size=4),
                                     name=name, hovertemplate=f"{name}: %{{x:.1f}} %% sinter, %{{y:,.1f}}<extra></extra>"))
        if choice is not None:
            val = choice[2] if col == "Coke kg" else choice[1]
            fig.add_trace(go.Scatter(x=[choice[0]], y=[val], mode="markers", marker=dict(symbol="star", size=16, color=colr, line=dict(color="#0B1220", width=1)),
                                     name=f"{name}: chosen", showlegend=False, hovertemplate=f"{name}: %{{x:.2f}} %% sinter, %{{y:,.1f}}<extra></extra>"))
    fig.update_xaxes(range=[0, 100], dtick=10, title="Sinter share of (sinter + ore), %", showgrid=False)
    fig.update_yaxes(title="kg per tHM" if col == "Coke kg" else "Rs per tHM", gridcolor="#22304C", tickformat=",")
    fig = _layout(fig, 330, margin=(10, 10, 46, 40), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)))
    return fig


def fig_trend_line(tbl, x, y, xtitle, ytitle, color, x0=None):
    ok = tbl[tbl["Status"] == "Optimal"]
    fig = go.Figure(go.Scatter(x=ok[x], y=ok[y], mode="lines+markers", line=dict(color=color, width=3), marker=dict(size=8),
                               hovertemplate=f"%{{x}}: %{{y:,.1f}}<extra></extra>"))
    if x0 is not None:
        fig.add_vline(x=x0, line=dict(color="#3FD6A8", width=1, dash="dot"))
    fig.update_xaxes(title=xtitle, showgrid=False)
    fig.update_yaxes(title=ytitle, gridcolor="#22304C", tickformat=",")
    return _layout(fig, 280, margin=(10, 10, 10, 40))


def fig_heatmap(hm, metric, run_share, best):
    pv = hm.pivot_table(index="Fe change (pts)", columns="Sinter %", values=metric, aggfunc="first", dropna=False)
    pv = pv.reindex(sorted(hm["Fe change (pts)"].unique()))
    pv = pv.reindex(columns=sorted(hm["Sinter %"].unique()))
    unit = "Rs/tHM" if metric.startswith("Cost") else "kg/tHM"
    fig = go.Figure(go.Heatmap(z=pv.values, x=[float(v) for v in pv.columns], y=[float(v) for v in pv.index],
                               colorscale=[[0, "#1F9C7F"], [0.5, "#16213A"], [1, "#C0504D"]], colorbar=dict(title=unit),
                               hovertemplate="sinter %{x:.1f} %, Fe %{y:+.1f} pt: %{z:,.1f}<extra></extra>", hoverongaps=False))
    ok = best.dropna(subset=["Best sinter %"])
    if len(ok):
        fig.add_trace(go.Scatter(x=ok["Best sinter %"], y=ok["Fe change (pts)"], mode="lines+markers", name="least-cost share",
                                 line=dict(color="#FFFFFF", width=2, dash="dot"), marker=dict(size=7, color="#FFFFFF")))
    if run_share is not None:
        fig.add_trace(go.Scatter(x=[run_share], y=[0.0], mode="markers", name="this run", marker=dict(symbol="star", size=18, color="#3FD6A8",
                                                                                                    line=dict(color="#0B1220", width=1))))
    fig.update_xaxes(title="Sinter share of (sinter + ore), %")
    fig.update_yaxes(title="Fe change, points", dtick=1)
    return _layout(fig, 380, margin=(10, 10, 10, 40), legend=True)


# ============================================================================= SLAG OXIDES
OX_CHOICES = ["All three", "CaO", "SiO2", "Al2O3", "MgO"]


def oxides_page():
    page_header("Slag oxides", "Which material brings the CaO, SiO2, Al2O3 and MgO, how close each oxide sits to its limit, and what would move it.")
    need_result()
    stale_run_notice()
    df, cfg, res, b = run_base()
    ox = an.oxide_table(b)

    # ---- one card per oxide
    cols = st.columns(4)
    for col, cd in zip(cols, an.oxide_cards(b)):
        dec = cd["Dec"]
        sub = (f"{cd['Slag kg']:.1f} kg to slag, {cd['Slag %']:.1f} % of slag<br>{cd['Limit']} {cd['Value']:.{dec}f}{cd['Unit']} "
               f"(allowed {cd['Low']:.{dec}f} to {cd['High']:.{dec}f}{cd['Unit']}): <b>{cd['Status']}</b>")
        col.markdown(kpi(f"{cd['Oxide']} charged", f"{cd['Charged kg']:.1f} kg", sub, STATUS_KIND.get(cd["Status"], ""), icon="science"),
                     unsafe_allow_html=True)
    st.caption("CaO and SiO2 are governed by the basicity band; Al2O3 and MgO by their share of the slag. AT LIMIT means the optimiser is "
               "using that limit in full. SiO2 to slag is the charged SiO2 less the SiO2 reduced into the metal as Si.")

    a, c_ = st.columns([2.2, 1])
    sel = a.radio("Oxide", OX_CHOICES, horizontal=True, key="ox_sel")
    view = c_.radio("Show", ["By material", "By group"], horizontal=True, key="ox_view")
    oxides = ["CaO", "SiO2", "Al2O3"] if sel == "All three" else [sel]
    body = an.oxide_body(ox)
    if view == "By material":
        frame, name_col = body, "Material"
        colors = mat_colors(list(body["Material"]), dict(zip(body["Material"], body["Group"])))
    else:
        g = an.oxide_by_group(ox)
        frame = g[g["Group"] != "TOTAL CHARGED"].copy()
        frame["Group"] = frame["Group"].map(lambda x: GROUP_LABEL.get(x, x))
        name_col = "Group"
        colors = group_colors(opt.GROUPS)

    panel_title("Where it comes from", "kg per tonne of hot metal charged to the furnace, and each source's share of the total.")
    if len(oxides) > 1:
        st.plotly_chart(fig_oxide_share(frame, name_col, oxides, colors), config=PLOT_CFG, **W)
    else:
        l, r = st.columns([1.5, 1])
        l.plotly_chart(fig_oxide_kg(frame, name_col, oxides[0], colors), config=PLOT_CFG, **W)
        r.plotly_chart(fig_oxide_donut(frame, name_col, oxides[0], colors), config=PLOT_CFG, **W)
    keep_cols = [name_col] + (["Group"] if name_col == "Material" else []) + ["kg/tHM charged"] + [x for o in oxides for x in (f"{o} kg", f"{o} % of total")]
    tbl = frame[keep_cols].copy()
    if "Group" in tbl.columns and name_col == "Material":
        tbl["Group"] = tbl["Group"].map(lambda x: GROUP_LABEL.get(x, x))
    tot = {name_col: "TOTAL CHARGED", "kg/tHM charged": float(frame["kg/tHM charged"].sum())}
    if name_col == "Material":
        tot["Group"] = ""
    for o in oxides:
        tot[f"{o} kg"] = float(frame[f"{o} kg"].sum())
        tot[f"{o} % of total"] = 100.0
    show(pd.concat([tbl, pd.DataFrame([tot])], ignore_index=True))
    if ox.attrs.get("note"):
        st.caption(ox.attrs["note"])

    # ---- per tonne of Fe
    panel_title("Oxide per tonne of Fe delivered",
                "Purchasing view: how much SiO2 and Al2O3 each iron-bearing material brings for every tonne of iron it delivers. "
                "Two ores with the same Al2O3 % can differ here if their Fe differs.")
    perfe = cached("perfe", df, cfg, lambda: an.oxide_per_fe(df, cfg, res[1]))
    if len(perfe):
        l, r = st.columns([1, 1.3])
        l.plotly_chart(fig_per_fe(perfe), config=PLOT_CFG, **W)
        pf = perfe.copy()
        pf["Group"] = pf["Group"].map(lambda x: GROUP_LABEL.get(x, x))
        pf = pf.round({c: (2 if c == "Fe %" else 0 if c.startswith("Price") else 1) for c in pf.columns if c not in ("Material", "Group")})
        with r:
            show(pf)
        keep("perfe", perfe, "Oxide per tonne of Fe delivered, by iron-bearing material",
             "kg of each oxide per tonne of Fe the material delivers, and price per tonne of contained Fe. Cleanest first.")
    else:
        st.caption("No iron-bearing material with an Fe assay is switched on.")

    # ---- across sinter share
    panel_title("Across sinter share, 0 to 100 %",
                "The full model re-solved with the sinter share pinned every 5 points. Stacked areas are each material's contribution; "
                "the dotted line is the slag value that limits it (right axis). Shows why a limit blocks higher or lower sinter.")
    try:
        sw, oxl = cached("ox_across", df, cfg, lambda: an.oxides_across_sinter(df, cfg, 5.0))
    except Exception as e:
        sw, oxl = None, None
        st.markdown(notice("Could not re-solve across sinter share.", str(e), "r"), unsafe_allow_html=True)
    if oxl is not None and len(oxl):
        run_share = res[3]["Sinter_share_pct"]
        if len(oxides) == 1:
            st.plotly_chart(fig_oxide_area(oxl, sw, oxides[0], run_share, b.get("curve")), config=PLOT_CFG, **W)
        else:
            tabs = st.tabs(oxides)
            for t, o in zip(tabs, oxides):
                with t:
                    st.plotly_chart(fig_oxide_area(oxl, sw, o, run_share, b.get("curve")), config=PLOT_CFG, **W)
        if sw is not None and sw.attrs.get("note"):
            st.caption(sw.attrs["note"])

    # ---- what-if
    panel_title("What if one material's assay changes?",
                "Change one oxide of one material and re-solve with the same settings. The optimiser re-chooses the whole burden.")
    used = [m for m in body["Material"]]
    a1, a2, a3, a4 = st.columns([1.4, 1, 1, 1.1], vertical_alignment="bottom")
    m = a1.selectbox("Material", used, key="wi_mat")
    o = a2.selectbox("Oxide", an.OXIDE_LIST, index=2, key="wi_ox")
    dl = a3.number_input("Change, points", -10.0, 10.0, 1.0, 0.1, format="%.2f", key="wi_delta")
    cur = float(df.loc[m, o]) if m in df.index else float("nan")
    a1.caption(f"{m}: {o} is {cur:g} % now.")
    if a4.button("Test the change", type="primary", key="wi_run", **W):
        try:
            out, st_, notes = an.oxide_whatif(df, cfg, res, m, o, dl)
            S.ox_whatif = {"out": out, "status": st_, "notes": notes, "run": b["created"]}
        except ValueError as e:
            S.ox_whatif = None
            st.markdown(notice("The what-if could not run.", str(e), "r"), unsafe_allow_html=True)
    w = S.ox_whatif
    if w and w["run"] == b["created"]:
        st.markdown(f"**{w['out'].attrs.get('change', '')}**: {w['status']}")
        if w["status"] == "Optimal":
            show(w["out"])
        else:
            st.markdown(notes_html(w["notes"]), unsafe_allow_html=True)


# ============================================================================= TRENDS
def trends_page():
    page_header("Trends", "What drives cost and coke on this run's data. Every curve re-solves the full model; results are reused until an input changes.")
    need_result()
    stale_run_notice()
    df, cfg, res, b = run_base()
    a0 = res[3]
    mode = f"pinned at {cfg.sinter_manual_pct:g} %" if cfg.sinter_manual_on else "the optimiser's free choice"

    # ---- rules of thumb
    panel_title("The model's rules of thumb on this run",
                f"Each input nudged once and the model re-solved with this run's settings (sinter share: {mode}). "
                "The small print compares it with the thumb-rule term alone.")
    try:
        me = cached("marginal", df, cfg, lambda: an.marginal_effects(df, cfg, res))
        keep("marginal", me, "Rules of thumb from the model on this run's data",
             "Each input nudged once and the model re-solved. 'Rule term alone' is the plant thumb rule at this run's rates; "
             "the difference is the knock-on effect through the slag volume and the re-balanced burden.")
        cols = st.columns(len(me))
        for col, (_, r) in zip(cols, me.iterrows()):
            if r["Status"] != "Optimal" or pd.isna(r["Coke kg"]):
                col.markdown(kpi(r["Effect of"], "n/a", f"{r['Status']}: {r['Change']}", "a", icon="rule"), unsafe_allow_html=True)
                continue
            if r["Key"] == "coke_price":
                val, sub = f"₹ {r['Cost Rs/tHM']:+,.0f}", f"per tHM; {a0['Coke_kg']:.0f} kg coke x Rs 1,000/t"
            else:
                val = f"{r['Coke kg']:+.1f} kg coke"
                direct = r["Rule term alone, kg fuel"]
                sub = f"₹ {r['Cost Rs/tHM']:+,.0f}/tHM" + (f"<br>rule alone {direct:+.1f} kg" if pd.notna(direct) else "")
            col.markdown(kpi(r["Effect of"], val, sub, "g" if r["Cost Rs/tHM"] < 0 else "a", icon="rule"), unsafe_allow_html=True)
        st.caption("Where the model differs from the rule alone, the difference is real model behaviour: the optimiser re-balances ore, "
                   "sinter and flux, which changes the slag volume and so the slag rule. Use these as a check on the plant thumb rules.")
    except ValueError as e:
        st.markdown(notice("Rules of thumb could not be worked out.", str(e), "r"), unsafe_allow_html=True)

    tabs = st.tabs(["Sinter share", "Fe grade", "Heat map", "Ore and prices", "Sinter basicity", "Runs and scenarios"])

    # ---- 1 sinter share (with scenario overlays)
    with tabs[0]:
        names = [s_["name"] for s_ in S.scenarios]
        pick = st.multiselect("Overlay saved scenarios (up to 3)", names, max_selections=3, key="tr_overlay",
                              help="Save a run from the Dashboard or the Runs and scenarios tab to overlay it here.")
        items = [("This run", b.get("curve"), b.get("choice"), "#4C8DFF")]
        for i, nm in enumerate(pick):
            sc = next(x for x in S.scenarios if x["name"] == nm)
            items.append((nm, sc["curve"], (sc["kpis"]["Sinter %"], sc["kpis"]["Cost Rs/tHM"], sc["kpis"]["Regular coke kg"], nm), SCEN_COLORS[i % 3]))
        l, r = st.columns(2)
        l.plotly_chart(fig_curve_overlay(items, "Coke kg", cfg), config=PLOT_CFG, **W)
        r.plotly_chart(fig_curve_overlay(items, "Cost Rs/tHM", cfg), config=PLOT_CFG, **W)
        cv = b.get("curve")
        if cv is not None:
            st.caption(cv.attrs.get("note", ""))
            ok = cv[cv["Status"] == "Optimal"]
            if len(ok):
                panel_title("Ore, sinter and flux behind each point", "Tonnes per tHM at each pinned sinter share.")
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(go.Bar(x=ok["Sinter %"], y=ok["Ore kg"], name="Iron ore", marker_color=GROUP_COLOR["Iron_ore"]), secondary_y=False)
                fig.add_trace(go.Bar(x=ok["Sinter %"], y=ok["Sinter kg"], name="Sinter", marker_color=GROUP_COLOR["Sinter"]), secondary_y=False)
                for colname, colr in (("Acid flux kg", "#FACC15"), ("Raw flux kg", "#A78BFA")):
                    if colname in ok.columns:
                        fig.add_trace(go.Scatter(x=ok["Sinter %"], y=ok[colname], name=colname.replace(" kg", ""), mode="lines+markers",
                                                 line=dict(color=colr, width=2)), secondary_y=True)
                fig.update_layout(barmode="stack")
                fig.update_xaxes(title="Sinter share of (sinter + ore), %", range=[-2, 102], dtick=10)
                fig.update_yaxes(title_text="Ore and sinter, kg per tHM", gridcolor="#22304C", secondary_y=False)
                fig.update_yaxes(title_text="Flux, kg per tHM", showgrid=False, secondary_y=True)
                st.plotly_chart(_layout(fig, 330, margin=(10, 10, 10, 40), legend=True), config=PLOT_CFG, **W)

    # ---- 2 Fe grade
    with tabs[1]:
        a, c_ = st.columns([1, 2], vertical_alignment="bottom")
        grp = a.radio("Fe of", ["Iron_ore", "Sinter"], format_func=lambda g: GROUP_LABEL[g], horizontal=True, key="tr_fe_grp")
        hold = c_.checkbox(f"Hold the sinter share at this run's {a0['Sinter_share_pct']:.1f} % (shows the Fe effect alone)", key="tr_fe_hold")
        try:
            hs = a0["Sinter_share_pct"] if hold else None
            tbl, sl = cached("fe_trend", df, cfg, lambda: an.fe_trend(df, cfg, grp, hold_share=hs), grp, hold)
            if sl["coke"] is not None:
                st.markdown(notice(f"1 point of {GROUP_LABEL[grp].lower()} Fe", f"= {sl['coke']:+.2f} kg coke = ₹ {sl['cost']:+,.0f} per tHM "
                                   f"(straight-line fit across ±3 points, all switched-on {GROUP_LABEL[grp].lower()} materials together).", "g"),
                            unsafe_allow_html=True)
            l, r = st.columns(2)
            l.plotly_chart(fig_trend_line(tbl, "Change (pts)", "Coke kg", "Fe change, points", "Regular coke, kg per tHM", "#4C8DFF", 0), config=PLOT_CFG, **W)
            r.plotly_chart(fig_trend_line(tbl, "Change (pts)", "Cost Rs/tHM", "Fe change, points", "Burden cost, Rs per tHM", "#F5A85C", 0), config=PLOT_CFG, **W)
            cols_ = [x for x in ["Change (pts)", "Status", "Cost Rs/tHM", "Cost vs base Rs", "Coke kg", "Fuel kg", "Fuel vs base kg", "Sinter %",
                                 "Ore kg", "Sinter kg", "Slag kg", "Burden Fe %"] if x in tbl.columns]
            show(tbl[cols_], status_cols=("Status",))
            keep("fe_trend", tbl[cols_], f"Cost and coke vs {GROUP_LABEL[grp]} Fe (+/- 3 points)" + (" - sinter share held" if hold else ""),
                 (f"Slope: {sl['coke']:+.2f} kg coke and Rs {sl['cost']:+,.0f}/tHM per point of Fe." if sl["coke"] is not None else ""))
            if not hold:
                st.caption("With the sinter share free, the optimiser may also move the mix as Fe changes; tick the box to see the Fe effect alone.")
        except ValueError as e:
            st.markdown(notice("The Fe trend could not run.", str(e), "r"), unsafe_allow_html=True)

    # ---- 3 heat map
    with tabs[2]:
        a, c_, d_, e_ = st.columns(4, vertical_alignment="bottom")
        grp = a.radio("Fe of", ["Iron_ore", "Sinter"], format_func=lambda g: GROUP_LABEL[g], horizontal=True, key="hm_grp")
        metric = c_.radio("Colour", ["Cost Rs/tHM", "Coke kg"], horizontal=True, key="hm_metric")
        span = d_.radio("Sinter range", ["Guard rails", "0 to 100 %"], horizontal=True, key="hm_span")
        step = e_.selectbox("Sinter step, points", [5.0, 2.5, 10.0], key="hm_step")
        lo, hi = (cfg.sinter_min * 100, cfg.sinter_max * 100) if span == "Guard rails" else (0.0, 100.0)
        shares = [float(x) for x in np.arange(lo, hi + 1e-9, step)]
        try:
            hm = cached("heatmap", df, cfg, lambda: an.heatmap(df, cfg, grp, shares), grp, tuple(shares))
            st.plotly_chart(fig_heatmap(hm, metric, a0["Sinter_share_pct"], hm.attrs["best"]), config=PLOT_CFG, **W)
            st.caption(f"Each cell is the full model with the sinter share pinned and the Fe of every switched-on {GROUP_LABEL[grp].lower()} "
                       "material shifted. Blank cells are infeasible. The dotted line is the least-cost sinter share on the grid for each Fe level: "
                       "if it bends, the Fe grade moves the best sinter share, which the single-line charts cannot show.")
            best = hm.attrs["best"]
            show(best.round({"Cost Rs/tHM": 0, "Coke kg": 1}))
            keep("heatmap", hm[["Fe change (pts)", "Sinter %", "Status", "Cost Rs/tHM", "Coke kg", "Fuel kg"]],
                 f"Heat map: sinter share x {GROUP_LABEL[grp]} Fe", "Each row one grid cell (sinter share pinned, Fe shifted). Blank = infeasible.")
        except ValueError as e:
            st.markdown(notice("The heat map could not run.", str(e), "r"), unsafe_allow_html=True)

    # ---- 4 ore and prices
    with tabs[3]:
        cand = an.on_materials(df, "Iron_ore") + an.on_materials(df, "Sinter")
        a, c_ = st.columns([1.3, 1], vertical_alignment="bottom")
        tgt = a.selectbox("Price of", cand, key="op_tgt") if cand else None
        rng = c_.selectbox("Range", ["±40 %, 10 % steps", "±20 %, 5 % steps"], key="op_rng")
        pcts = tuple(range(-40, 41, 10)) if rng.startswith("±40") else tuple(range(-20, 21, 5))
        if tgt:
            try:
                ps = cached("price_scan", df, cfg, lambda: an.ore_price_scan(df, cfg, tgt, pcts), tgt, pcts)
                p0 = ps.attrs["base_price"]
                sw_ = ps.attrs["switches"]
                if sw_:
                    st.markdown(notes_html([f"SWITCH: at about Rs {x['Price Rs/t']:,.0f}/t ({x['Price change %']:+.1f} %) {x['What happens']}." for x in sw_]),
                                unsafe_allow_html=True)
                else:
                    st.caption(f"{tgt} stays in the burden across the whole range: no switch point between Rs {p0 * (1 + min(pcts) / 100):,.0f} and Rs {p0 * (1 + max(pcts) / 100):,.0f}/t.")
                okp = ps[ps["Status"] == "Optimal"].reset_index(drop=True)
                jumps = [f"between Rs {okp.loc[i - 1, 'Price Rs/t']:,.0f} and Rs {okp.loc[i, 'Price Rs/t']:,.0f}/t the sinter share moves "
                         f"from {okp.loc[i - 1, 'Sinter %']:.1f} % to {okp.loc[i, 'Sinter %']:.1f} %"
                         for i in range(1, len(okp)) if abs(okp.loc[i, "Sinter %"] - okp.loc[i - 1, "Sinter %"]) >= 1.0]
                if jumps:
                    st.markdown(notes_html(["NOTE: " + "; ".join(jumps) + "."]), unsafe_allow_html=True)
                l, r = st.columns(2)
                l.plotly_chart(fig_trend_line(ps, "Price Rs/t", "Cost Rs/tHM", f"{tgt} price, Rs/t", "Burden cost, Rs per tHM", "#F5A85C", p0), config=PLOT_CFG, **W)
                ok = ps[ps["Status"] == "Optimal"]
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                cols_m = [x for x in ok.columns if x.endswith(" kg") and x not in ("Coke kg",)]
                grp_of = dict(zip(df.index, df["Group"]))
                colors = mat_colors([x[:-3] for x in cols_m], {x[:-3]: grp_of.get(x[:-3], "") for x in cols_m})
                for x in cols_m:
                    fig.add_trace(go.Scatter(x=ok["Price Rs/t"], y=ok[x], name=x[:-3], mode="lines+markers", line=dict(width=3, color=colors[x[:-3]])), secondary_y=False)
                fig.add_trace(go.Scatter(x=ok["Price Rs/t"], y=ok["Sinter %"], name="Sinter share %", mode="lines", line=dict(color="#FFFFFF", dash="dot")), secondary_y=True)
                fig.add_vline(x=p0, line=dict(color="#3FD6A8", width=1, dash="dot"))
                fig.update_xaxes(title=f"{tgt} price, Rs/t", showgrid=False)
                fig.update_yaxes(title_text="kg per tHM", gridcolor="#22304C", secondary_y=False)
                fig.update_yaxes(title_text="Sinter share, %", showgrid=False, secondary_y=True)
                fig = _layout(fig, 300, margin=(10, 10, 46, 40), legend=True)
                fig.update_layout(legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)))
                r.plotly_chart(fig, config=PLOT_CFG, **W)
                show(ps.round({c: (0 if c.startswith(("Price Rs", "Cost")) else 1) for c in ps.columns if c not in ("Status",)}), status_cols=("Status",))
                keep("price_scan", ps, f"Price scan: {tgt}", "; ".join(f"Switch at Rs {x['Price Rs/t']:,.0f}/t: {x['What happens']}" for x in sw_)
                     or "No switch point in the scanned range.")
            except ValueError as e:
                st.markdown(notice("The price scan could not run.", str(e), "r"), unsafe_allow_html=True)

    # ---- 5 sinter basicity what-if
    with tabs[4]:
        sins = an.on_materials(df, "Sinter")
        if not sins:
            st.caption("No sinter is switched on.")
        else:
            now = float(df.loc[sins[0], "CaO"]) / max(float(df.loc[sins[0], "SiO2"]), 1e-9)
            a, c_ = st.columns([2, 1], vertical_alignment="bottom")
            tb = a.slider(f"Sinter basicity CaO / SiO2 for every switched-on sinter (today {now:.2f})", 0.8, 3.5,
                          float(round(min(max(now, 0.8), 3.5), 2)), 0.01, key="sb_target")
            dil = c_.checkbox("Mass balance: extra CaO dilutes the other assays", value=True, key="sb_dilute")
            if abs(tb - now) < 0.006:
                st.caption("This is today's sinter basicity. Move the slider to see how a different sinter basicity changes cost, coke and "
                           "the sinter share the furnace can take.")
            else:
                try:
                    def _basic():
                        d2, tab = an.sinter_basicity_df(df, tb, dil)
                        r2 = opt.solve(d2, cfg, explain=False)
                        cv2 = opt.curve(d2, cfg) if r2[0] in ("Optimal", "Infeasible") else None
                        return tab, r2, cv2
                    tab, r2, cv2 = cached("basicity", df, cfg, _basic, round(tb, 3), dil)
                    if r2[0] == "Optimal":
                        a2 = r2[3]
                        k_ = [("Cost", f"₹ {r2[2]:,.0f}", f"{r2[2] - res[2]:+,.0f} vs this run", "c"),
                              ("Sinter share", f"{a2['Sinter_share_pct']:.1f} %", f"{a2['Sinter_share_pct'] - a0['Sinter_share_pct']:+.1f} pts", "g"),
                              ("Regular coke", f"{a2['Coke_kg']:.1f} kg", f"{a2['Coke_kg'] - a0['Coke_kg']:+.1f} kg", "p"),
                              ("Flux to furnace", f"{a2['Flux_kg']:.1f} kg", f"{a2['Flux_kg'] - a0['Flux_kg']:+.1f} kg", "p"),
                              ("Slag", f"{a2['slag_kg']:.0f} kg", f"{a2['slag_kg'] - a0['slag_kg']:+.0f} kg", "p")]
                        for col, (lab, v, sub, kd) in zip(st.columns(5), k_):
                            col.markdown(kpi(lab, v, sub, kd), unsafe_allow_html=True)
                    else:
                        st.markdown(notice(f"At this basicity the run is {r2[0]}.", "The curve below shows where a feasible burden exists.", "r"), unsafe_allow_html=True)
                    items = [("This run", b.get("curve"), b.get("choice"), "#4C8DFF"),
                             (f"Sinter B2 {tb:.2f}", cv2, (a2["Sinter_share_pct"], r2[2], a2["Coke_kg"], "what-if") if r2[0] == "Optimal" else None, "#F5A85C")]
                    l, r = st.columns(2)
                    l.plotly_chart(fig_curve_overlay(items, "Coke kg", cfg), config=PLOT_CFG, **W)
                    r.plotly_chart(fig_curve_overlay(items, "Cost Rs/tHM", cfg), config=PLOT_CFG, **W)
                    show(tab)
                    keep("basicity", tab, f"Sinter basicity what-if: B2 {tb:.2f}",
                         (f"Cost Rs {r2[2]:,.0f}/tHM ({r2[2] - res[2]:+,.0f}), sinter {r2[3]['Sinter_share_pct']:.1f} %." if r2[0] == "Optimal" else f"Run {r2[0]}.")
                         + " Sinter price and sinter-plant costs unchanged.")
                    st.caption("Assumptions: the sinter price does not change, so the extra lime and sinter-plant fuel are not costed; sinter strength and "
                               "reducibility effects are not modelled. With the mass balance ticked, adding CaO lowers the sinter Fe and the other assays "
                               "in proportion. Treat the result as the furnace-side effect only.")
                except ValueError as e:
                    st.markdown(notice("The basicity what-if could not run.", str(e), "r"), unsafe_allow_html=True)

    # ---- 6 runs and scenarios
    with tabs[5]:
        panel_title("Run history", "Every optimal run this session, with what changed from the run before it.")
        if S.history:
            h = pd.DataFrame(S.history)
            h["Cost change Rs"] = h["Cost Rs/tHM"].diff()
            h = h[["Run", "Time", "Furnace", "Cost Rs/tHM", "Cost change Rs", "Coke kg/tHM", "Fuel kg/tHM", "Sinter %", "Changed"]]
            if len(h) >= 2:
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(go.Scatter(x=h["Run"], y=h["Cost Rs/tHM"], name="Cost, Rs/tHM", mode="lines+markers", line=dict(color="#F5A85C", width=3),
                                         customdata=h["Changed"], hovertemplate="Run %{x}: Rs %{y:,.0f}<br>%{customdata}<extra></extra>"), secondary_y=False)
                fig.add_trace(go.Scatter(x=h["Run"], y=h["Coke kg/tHM"], name="Coke, kg/tHM", mode="lines+markers", line=dict(color="#4C8DFF", width=3)), secondary_y=True)
                fig.update_xaxes(dtick=1, title="Run")
                fig.update_yaxes(title_text="Rs per tHM", gridcolor="#22304C", tickformat=",", secondary_y=False)
                fig.update_yaxes(title_text="kg per tHM", showgrid=False, secondary_y=True)
                st.plotly_chart(_layout(fig, 260, margin=(10, 10, 10, 35), legend=True), config=PLOT_CFG, **W)
            show(h)
        else:
            st.caption("No optimal run yet.")

        panel_title("Saved scenarios", "Name a run to keep it, then compare up to three side by side.")
        save_scenario_row("tr")
        if S.scenarios:
            names = [s_["name"] for s_ in S.scenarios]
            a, c_ = st.columns([3, 1], vertical_alignment="bottom")
            pick = a.multiselect("Compare", names, default=names[-min(3, len(names)):], max_selections=3, key="sc_pick")
            gone = c_.selectbox("Delete a scenario", ["-"] + names, key="sc_del")
            if gone != "-" and c_.button("Delete", key="sc_del_btn"):
                S.scenarios = [x for x in S.scenarios if x["name"] != gone]
                st.rerun()
            chosen = [x for x in S.scenarios if x["name"] in pick]
            if chosen:
                kp, rc, diffs = an.compare_scenarios(chosen)
                show(kp)
                panel_title("Recipe, kg per tHM")
                rc["Group"] = rc["Group"].map(lambda g: GROUP_LABEL.get(g, g))
                show(rc)
                for nm, first, ch in diffs:
                    st.markdown(notes_html([f"NOTE: {nm} vs {first}: " + ("; ".join(ch) if ch else "same inputs and settings")]), unsafe_allow_html=True)
        else:
            st.caption("No scenarios saved yet.")


def extra_export_items():
    """The dashboard's own sheets for the Excel workbook (only those made from the run being exported)."""
    b = S.bundle
    items = []
    names = {"marginal": "Rules of Thumb", "perfe": "Oxides per t Fe", "fe_trend": "Fe Trend", "heatmap": "Heat Map",
             "price_scan": "Price Scan", "basicity": "Sinter Basicity"}
    for k, sheet in names.items():
        it = S.v6.get(k)
        if it and it["run"] == b["created"]:
            items.append((sheet, it["title"], it["df"], it["note"]))
    if S.history:
        items.append(("Run History", "Run history (this session)", pd.DataFrame(S.history), "Every optimal run, with what changed from the run before it."))
    if S.scenarios:
        kp, rc, diffs = an.compare_scenarios(S.scenarios[-3:])
        items.append(("Scenario Comparison", "Saved scenarios - key numbers", kp,
                      " | ".join(f"{n} vs {f}: " + ("; ".join(c) if c else "same inputs") for n, f, c in diffs)))
        items.append(("Scenario Recipes", "Saved scenarios - recipe, kg per tHM", rc, ""))
    return items


# ============================================================================= ROUTER
PAGES = {"Dashboard": dashboard, "Inputs": inputs_page, "Materials & stock": materials_page, "Burden & cost": burden_page,
         "Slag oxides": oxides_page, "Trends": trends_page, "Scenario analysis": scenario_page, "Reports & export": reports_page,
         "Heat audit": audit_page, "Upload & settings": settings_page}

sidebar_nav()
try:
    PAGES.get(S.nav, dashboard)()
finally:
    sidebar_footer()
st.markdown('<div class="foot">Basis: 1 tonne of hot metal, dry and net. Engine: the MBF notebook v11.7 model (plant-reviewed thumb rules, Sep-26; hot-zone heat balance and heat audit). Dashboard V7.</div>', unsafe_allow_html=True)
