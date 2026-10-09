import copy
from io import BytesIO
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np

from sinter import optimizer as opt

st.set_page_config(page_title="Sinter Burden Control", page_icon="⚙️", layout="wide", initial_sidebar_state="expanded")

# ----------------------------- CONSTANTS ---------------------------------
TARGETS = opt.TARGETS
CHEM_COLS = ["Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Moisture_Pct"]
GROUPS = ["Iron_ore", "Flux", "Recycle", "Fuel"]
GROUP_LABEL = {"Iron_ore": "Iron Ore", "Flux": "Flux", "Recycle": "Recycle", "Fuel": "Fuel"}

# ----------------------------- STYLE -------------------------------------
st.markdown("""
<style>
:root { --bg:#071016; --panel:#0d1a21; --panel2:#111f27; --line:#28404d; --text:#edf5fa; --muted:#8ea6b4; --accent:#2f82b3; --good:#25c481; --warn:#f2b94b; --bad:#ff5555; }
html, body, [data-testid="stAppViewContainer"] { background:var(--bg); color:var(--text); }
[data-testid="stHeader"] { background:transparent; }
[data-testid="stSidebar"] { width:220px !important; min-width:220px !important; background:#09131a; border-right:1px solid var(--line); }
[data-testid="stSidebar"] > div:first-child { width:220px !important; }
.block-container { max-width:1450px; padding:1.2rem 1.4rem 2.5rem; }
.small { color:var(--muted); font-size:.72rem; }
.eyebrow { color:#73b5d7; font-size:.62rem; letter-spacing:.16em; font-weight:800; text-transform:uppercase; }
h1 { font-size:2rem !important; margin:.05rem 0 .15rem !important; letter-spacing:.01em; }
h2,h3 { letter-spacing:.01em; }
.panel { background:linear-gradient(180deg,#102029,#0d1a21); border:1px solid var(--line); border-radius:8px; padding:.65rem .7rem; margin:.55rem 0; }
.panel-title { color:#8fd0ef; font-size:.67rem; font-weight:900; letter-spacing:.1em; text-transform:uppercase; margin-bottom:.35rem; }
.notice { background:#12242e; border:1px solid #31566a; border-radius:7px; padding:.6rem .75rem; color:#cce6f3; font-size:.76rem; }
.notice-w { border-color:#795b18; background:#241e0e; }
.notice-r { border-color:#7a2323; background:#241010; }
.hero { background:#0d1a21; border:1px solid var(--line); border-radius:8px; padding:.7rem .85rem; margin:.5rem 0 .7rem; }
.kpi { background:#101d24; border:1px solid var(--line); border-radius:8px; padding:.65rem .7rem; min-height:82px; }
.kpi-label { font-size:.6rem; color:#7da4b7; letter-spacing:.11em; font-weight:900; text-transform:uppercase; }
.kpi-value { font-size:1.2rem; font-weight:900; margin-top:.22rem; }
.kpi-sub { font-size:.62rem; color:#6f8998; margin-top:.12rem; }
.kpi-g { border-left:3px solid var(--good); } .kpi-r { border-left:3px solid var(--bad); } .kpi-a { border-left:3px solid var(--warn); } .kpi-s { border-left:3px solid #4ca8df; }
.nav-title { color:#7397a9; font-size:.58rem; font-weight:900; letter-spacing:.16em; margin:.75rem 0 .3rem; }
.sidebar-brand { font-weight:900; font-size:.86rem; letter-spacing:.03em; }
div.stButton > button { border-radius:6px; border:1px solid #2b4c5c; background:#101f27; color:#edf5fa; font-size:.72rem; min-height:2rem; }
div.stButton > button:hover { border-color:#4e94bb; color:white; }
button[kind="primary"] { background:#2c78a5 !important; border-color:#3e91c1 !important; }
[data-testid="stDataEditor"] { border:1px solid var(--line); border-radius:7px; overflow:hidden; }
[data-testid="stDataEditor"] [role="gridcell"], [data-testid="stDataFrame"] [role="gridcell"] { font-size:11px !important; }
[data-testid="stFileUploader"] { background:#111a21; border-radius:7px; padding:.25rem; }
[data-testid="stMetric"] { background:#101d24; border:1px solid var(--line); border-radius:8px; padding:.45rem; }
.footer { color:#526d7b; font-size:.58rem; text-align:right; margin-top:1rem; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap:.25rem; border-bottom:1px solid var(--line); }
[data-testid="stTabs"] [data-baseweb="tab"] { background:#0d1a21; border:1px solid var(--line); border-bottom:none; border-radius:7px 7px 0 0; color:var(--muted); font-size:.74rem; font-weight:800; letter-spacing:.03em; padding:.5rem 1rem; }
[data-testid="stTabs"] [aria-selected="true"] { background:#12242e; color:var(--text); border-color:var(--accent); }
[data-testid="stTabs"] [data-baseweb="tab-panel"] { padding-top:.7rem; }
</style>
""", unsafe_allow_html=True)

# ----------------------------- STATE -------------------------------------
def initial_df():
    return opt._ensure_material_role(opt.get_default_chemistry().copy())

def _backend_default(name, fallback):
    return float(getattr(opt, name, fallback))

def _init(key, value):
    if key not in st.session_state:
        st.session_state[key] = value

if "master_df" not in st.session_state:
    st.session_state.master_df = initial_df()
    st.session_state.source = "Built-in Master Chemistry"
    st.session_state.production = float(opt.DEFAULT_PLANNING_TONNES)
    st.session_state.available = {m: (float(st.session_state.master_df.loc[m, "Available_Tonnes"]) > 0)
                                   for m in st.session_state.master_df.index}
    st.session_state.result = None
    st.session_state.manual_base = None
    st.session_state.whatif = None
    st.session_state.manual_scenario_result = None
    st.session_state.runs = 0
    st.session_state.changed = False
    st.session_state.changed_source = ""
    st.session_state.nav = "Dashboard"

_init("horizon_days", float(opt.DEFAULT_HORIZON_DAYS))
_init("inventory_weight", float(opt.DEFAULT_INVENTORY_WEIGHT))
_init("apply_tech_ores", False)
_init("max_flux_pct", float(opt.MAX_FLUX_PORTION) * 100)
_init("use_ore_share_cap", False)
_init("ore_share_cap_pct", 35.0)
_init("stock_basis", opt.DEFAULT_STOCK_BASIS)
_init("strand_area", float(opt.DEFAULT_STRAND_AREA_M2))
_init("om_cost", _backend_default("DEFAULT_OM_COST_RS_T", 750.0))
_init("latent_heat", _backend_default("DEFAULT_HEAT_LATENT_MOISTURE", 540.0))
_init("calcination_heat", _backend_default("DEFAULT_HEAT_CALCINATION_PER_LOI_KG", 420.0))
_init("melting_heat", _backend_default("DEFAULT_HEAT_MELTING_PER_KG_SINTER", 60.0))
_init("loss_fraction", _backend_default("DEFAULT_HEAT_LOSS_FRACTION", 0.12))
_init("firing_ratio_max", _backend_default("DEFAULT_FIRING_RATIO_MAX", 1.10))
_init("coke_min_rate", _backend_default("DEFAULT_COKE_MIN_KG_T", 55.0))
_init("coke_max_rate", _backend_default("DEFAULT_COKE_MAX_KG_T", 85.0))
_init("feo_min", _backend_default("DEFAULT_FEO_MIN_PCT", 8.5))
_init("feo_target", _backend_default("DEFAULT_FEO_TARGET_PCT", 9.2))
_init("feo_max", _backend_default("DEFAULT_FEO_MAX_PCT", 10.0))
_init("manual_coke_override", False)
_init("manual_coke_rate", 65.0)
_init("rs_iol_pct", _backend_default("IOL_FINES_NOMINAL_PCT", 0.08) * 100)      # % of the charged mix
_init("rs_bfr_pct", _backend_default("BF_RETURNS_NOMINAL_PCT", 0.17) * 100)     # % of the charged mix
_init("fuel_ash_settings", copy.deepcopy(getattr(opt, "DEFAULT_FUEL_ASH_SETTINGS", {})))
_init("feed_rate_t_h", float(opt.DEFAULT_FEED_RATE_T_H))
_init("productivity_target", float(opt.DEFAULT_PRODUCTIVITY_TARGET))
_init("productivity_metrics", None)
_init("fines_specific_consumption", {})
_init("tolerances", dict(opt.DEFAULT_TOLERANCES))          # plant-approved quality tolerances (placeholders except SiO2 6.2)
_init("float_returns", False)                                # let the model choose IOL / BFR inside their ranges
_init("robust_margin", 0.0)                                  # steer tighter than the spec on SiO2 / Al2O3 / Fe (pp)
_init("ratio_band_pp", float(opt.RATIO_HOLD_BAND_PP))       # "hold the stock ratio" comparison band
_init("feo_pin_tol", float(opt.FEO_PIN_TOL_PCT))            # FeO closeness allowance (FeO points)

# ----------------------------- CHANGE TRACKING ----------------------------
def mark_changed(source):
    """Flip the RERUN REQUIRED indicator. Never call this from Manual Burden Control."""
    st.session_state.changed = True
    st.session_state.changed_source = source

def status_label():
    if st.session_state.runs == 0:
        return "NOT RUN", "s"
    if st.session_state.changed:
        return f"RERUN REQUIRED — Changed in: {st.session_state.changed_source}", "r"
    return "UP TO DATE", "g"

# ----------------------------- HELPERS ------------------------------------
def active_df():
    df = st.session_state.master_df.copy()
    for m in df.index:
        if not st.session_state.available.get(m, True):
            df.loc[m, "Available_Tonnes"] = 0.0
    return df

def _current_fuel_ash_settings():
    return {k: {kk: float(vv) for kk, vv in v.items()} for k, v in st.session_state.fuel_ash_settings.items()}

def _rs_valid():
    iol, bfr = float(st.session_state.rs_iol_pct), float(st.session_state.rs_bfr_pct)
    return opt.IOL_MIN_PCT <= iol <= opt.IOL_MAX_PCT and opt.BFR_MIN_PCT <= bfr <= opt.BFR_MAX_PCT

def _rs_total():
    return float(st.session_state.rs_iol_pct) + float(st.session_state.rs_bfr_pct)

def _rs_total_in_usual_range():
    return opt.RS_TOTAL_MIN_PCT <= _rs_total() <= opt.RS_TOTAL_MAX_PCT

def _solver_kwargs():
    tech = ("Flux", "Recycle") + (("Iron_ore", "Fuel") if st.session_state.apply_tech_ores else ())
    return dict(
        iol_nominal=float(st.session_state.rs_iol_pct) / 100.0,
        bf_nominal=float(st.session_state.rs_bfr_pct) / 100.0,
        latent_heat=st.session_state.latent_heat,
        calcination_heat=st.session_state.calcination_heat,
        melting_heat=st.session_state.melting_heat,
        loss_fraction=st.session_state.loss_fraction,
        firing_ratio_max=st.session_state.firing_ratio_max,
        coke_min_rate=st.session_state.coke_min_rate,
        coke_max_rate=st.session_state.coke_max_rate,
        feo_min=st.session_state.feo_min,
        feo_target=st.session_state.feo_target,
        feo_max=st.session_state.feo_max,
        manual_override=st.session_state.manual_coke_override,
        manual_coke_rate=st.session_state.manual_coke_rate,
        fuel_ash_settings=_current_fuel_ash_settings(),
        inventory_weight=float(st.session_state.inventory_weight),
        apply_tech_limits_to=tech,
        stock_basis=st.session_state.stock_basis,
        max_flux_portion=float(st.session_state.max_flux_pct) / 100.0,
        ore_share_cap=(float(st.session_state.ore_share_cap_pct) / 100.0 if st.session_state.use_ore_share_cap else None),
        tolerances={k: float(v) for k, v in st.session_state.tolerances.items()},
        float_returns=bool(st.session_state.float_returns),
        robust_margin=float(st.session_state.robust_margin),
        ratio_band_pp=float(st.session_state.ratio_band_pp),
        feo_pin_tol=float(st.session_state.feo_pin_tol),
        horizon_days=float(st.session_state.horizon_days),
    )

def _effective_bfr_share(df, prod):
    return opt.resolve_return_sinter(df, prod, float(st.session_state.rs_iol_pct) / 100.0,
                                     float(st.session_state.rs_bfr_pct) / 100.0, st.session_state.stock_basis)["b"]

def _productivity_from(blend, df, b_eff):
    cm = opt.charged_mix_summary(blend, df, b_eff)
    fresh = cm["Fresh_Burden_kg_t"]
    spec = st.session_state.fines_specific_consumption
    total_fines = sum(float(df.loc[m, "Fines_Pct"]) * float(spec.get(m, 0.0)) / 100.0
                      for m in df.index if "Fines_Pct" in df.columns)
    return opt.calculate_productivity_metrics(
        fresh, st.session_state.feed_rate_t_h, st.session_state.productivity_target,
        total_fines_pct=total_fines, charged_mix_kg_t=cm["Charged_Mix_kg_t"],
        gross_sinter_kg_t=cm["Gross_Sinter_kg_t"], strand_area_m2=st.session_state.strand_area)

def run_optimizer():
    if not _rs_valid():
        raise ValueError(f"IOL Fines must be {opt.IOL_MIN_PCT:.0f}–{opt.IOL_MAX_PCT:.0f}% and BF Returns {opt.BFR_MIN_PCT:.0f}–{opt.BFR_MAX_PCT:.0f}% of the charged mix.")
    df = active_df()
    prod = float(st.session_state.production)
    kw = _solver_kwargs()
    status, blend, cost, achieved, diagnostics, is_fallback = opt.solve_blend_with_compensation(
        df, prod, TARGETS, baseline_blend=None, **kw)
    report = opt.get_last_run_report()          # capture now: the next solve clears it
    # Reference: what a pure-cost recipe would cost, so the price of following the stock ratio is visible.
    premium = None
    if blend and status in ("Optimal", "Relaxed") and kw["inventory_weight"] > 0.0:
        kw0 = dict(kw); kw0["inventory_weight"] = 0.0; kw0["compare_ratio_hold"] = False
        s0, b0, c0, *_ = opt.solve_blend_with_compensation(df, prod, TARGETS, baseline_blend=None, **kw0)
        if b0 and s0 == status and c0 is not None:
            premium = {"pure_cost": float(c0), "premium": float(cost - c0)}
    used = report.get("returns_used") or {}
    b_eff = opt.resolve_return_sinter(df, prod, used.get("iol", float(st.session_state.rs_iol_pct) / 100.0),
                                      used.get("bfr", float(st.session_state.rs_bfr_pct) / 100.0), st.session_state.stock_basis)["b"]
    st.session_state.result = {"status": status, "blend": blend, "cost": cost, "achieved": achieved,
                                "diagnostics": diagnostics, "fallback": is_fallback, "df": df.copy(),
                                "premium": premium, "prod": prod, "b_eff": b_eff, "report": report,
                                "tolerances": {k: float(v) for k, v in st.session_state.tolerances.items()}}
    st.session_state.manual_base = blend.copy() if blend else None
    st.session_state.manual_scenario_result = None
    st.session_state.runs += 1
    st.session_state.changed = False
    st.session_state.changed_source = ""
    st.session_state.whatif = None
    if blend:
        total_burden = sum(v for m, v in blend.items() if opt._bfr_name(df) != m)
        st.session_state.fines_specific_consumption = {
            m: (blend.get(m, 0.0) / total_burden * 100.0 if total_burden else 0.0) for m in df.index}
        try:
            st.session_state.productivity_metrics = _productivity_from(blend, df, b_eff)
        except Exception:
            st.session_state.productivity_metrics = None

def _tol():
    return opt._merge_tolerances(st.session_state.tolerances, TARGETS)

def quality_ok(a):
    """Spec met, using the same 0.005 rounding allowance as the solver (so an 'Optimal' run is never shown as REVIEW)."""
    if not a: return False
    return max(opt._spec_gaps(a, TARGETS).values()) <= opt.SPEC_CHECK_TOL

def quality_level(a):
    """'spec' (all met) | 'tolerance' (some spec missed, all inside approved tolerance) | 'outside'."""
    if not a: return "outside"
    gt = opt._goal_table(a, TARGETS, _tol())["Status"]
    if all(s.startswith("Met") for s in gt): return "spec"
    if all(s.startswith("Met") or s.startswith("Relaxed") for s in gt): return "tolerance"
    return "outside"

def kpi(label, value, sub="", kind="s"):
    return f'<div class="kpi kpi-{kind}"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>'

def chemistry_status(value, lower=None, upper=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "r"
    if lower is not None and v < float(lower): return "r"
    if upper is not None and v > float(upper): return "r"
    return "g"

def quality_cards(ach):
    """Green = spec met | amber = spec missed but inside the plant-approved tolerance | red = outside tolerance."""
    gt = opt._goal_table(ach, TARGETS, _tol()).set_index("Goal")["Status"].to_dict()
    def kind(goal):
        s = str(gt.get(goal, ""))
        return "g" if s.startswith("Met") else ("a" if s.startswith("Relaxed") else "r")
    items = [
        ("Fe", ach.get("Fe", np.nan), f"{opt.FE_LOWER:.1f}–{opt.FE_UPPER:.1f}", "%", kind("Fe")),
        ("SiO₂", ach.get("SiO2", np.nan), f"≤ {TARGETS['SiO2_max']}", "%", kind("SiO2")),
        ("Al₂O₃", ach.get("Al2O3", np.nan), f"≤ {TARGETS['Al2O3_max']}", "%", kind("Al2O3")),
        ("Al₂O₃/SiO₂", ach.get("Al2O3/SiO2", np.nan), f"≤ {TARGETS['Al2O3_SiO2_max']}", "", kind("Al2O3/SiO2")),
        ("Basicity", ach.get("Basicity", np.nan), f"{TARGETS['Basicity_min']}–{TARGETS['Basicity_max']}", "", kind("Basicity")),
        ("MgO", ach.get("MgO", np.nan), f"{TARGETS['MgO_min']}–{TARGETS['MgO_max']}", "%", kind("MgO")),
        ("CaO", ach.get("CaO", np.nan), f"{TARGETS['CaO_min']}–{TARGETS['CaO_max']}", "%", kind("CaO")),
        ("B4", ach.get("B4", np.nan), "1.8–2.2 info", "%", "g"),
    ]
    cols = st.columns(8)
    for c, (lab, val, tgt, unit, k) in zip(cols, items):
        c.markdown(kpi(lab, f"{val:.3f}{unit}", tgt, k), unsafe_allow_html=True)

def goal_table_panel(r):
    """Goal table from the v42 solver: spec, achieved, how far outside, approved tolerance, status."""
    rep_ = (r or {}).get("report") or {}
    gt = rep_.get("goal_table")
    if gt is None or not len(gt):
        return
    st.markdown('<div class="panel"><div class="panel-title">QUALITY GOALS vs SPEC AND APPROVED TOLERANCE</div>'
                '<div class="small">Approved tolerances are placeholders (except SiO₂ 6.2) until confirmed with the plant — edit them under Inputs → Quality & Solver.</div></div>',
                unsafe_allow_html=True)
    st.dataframe(gt, hide_index=True, use_container_width=True, height=min(330, 36 * len(gt) + 45),
                 column_config={"Achieved": st.column_config.NumberColumn(format="%.3f"),
                                "Outside spec by": st.column_config.NumberColumn(format="%.3f")})

def master_data_warnings(df):
    """Flag rows that look like placeholders: they would otherwise be used by the optimizer as if the data were real."""
    out = []
    for m in df.index:
        g = str(df.loc[m, "Group"]).strip()
        if g not in ("Iron_ore", "Flux", "Recycle"):
            continue
        stock = float(df.loc[m, "Available_Tonnes"])
        if stock <= 0:
            continue
        fe, si, al, ca, mg, loi = (float(df.loc[m, c]) for c in ("Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI"))
        if fe + si + al + ca + mg <= 0:
            out.append(f"{m}: stock {stock:,.0f} t but ALL chemistry values are 0 — looks like a placeholder row. Mark it unavailable or enter its chemistry.")
        elif g == "Iron_ore" and fe > 0 and si <= 0 and al <= 0 and loi <= 0:
            out.append(f"{m}: Fe {fe:.1f}% but SiO₂, Al₂O₃ and LOI are all 0 — chemistry looks incomplete, so the optimizer will treat it as a very clean ore.")
    return out

def page_header(title, subtitle):
    st.markdown('<div class="eyebrow">HOSPET ALLOY STEEL PLANT</div>', unsafe_allow_html=True)
    st.markdown(f"<h1>{title}</h1><div class='small'>{subtitle}</div>", unsafe_allow_html=True)

def aligned_result_table(blend, df, include_total=True):
    bfr = opt._bfr_name(df)
    mats = [str(m) for m in df.index if m != bfr]          # BFR is chemistry-only: shown in the memo panel
    total = float(sum(float(blend.get(m, 0.0)) for m in mats))
    total_cost = float(sum(float(blend.get(m, 0.0)) * float(df.loc[m, "Price_Rs_t"]) / 1000 for m in mats))
    rows = []
    for m in mats:
        q = float(blend.get(m, 0.0))
        cost = q * float(df.loc[m, "Price_Rs_t"]) / 1000
        rows.append({"Material": m, "Group": GROUP_LABEL.get(df.loc[m, "Group"], df.loc[m, "Group"]),
                     "kg/t": q, "% Burden": q / total * 100 if total else 0.0,
                     "Cost ₹/t": cost, "% Cost": cost / total_cost * 100 if total_cost else 0.0})
    if include_total:
        rows.append({"Material": "TOTAL", "Group": "", "kg/t": total, "% Burden": 100.0 if total else 0.0,
                     "Cost ₹/t": total_cost, "% Cost": 100.0 if total_cost else 0.0})
    return pd.DataFrame(rows)

def result_table(blend, df, include_zero=False):
    rows = []
    if not blend:
        return pd.DataFrame(columns=["Material", "Group", "kg/t", "% Burden", "Cost ₹/t", "% Cost"])
    bfr = opt._bfr_name(df)
    items = {m: q for m, q in blend.items() if m != bfr}
    total = sum(float(v) for v in items.values())
    total_cost = sum(float(q) * float(df.loc[m, "Price_Rs_t"]) / 1000 for m, q in items.items())
    for m in df.index:
        if m == bfr: continue
        q = float(items.get(m, 0))
        if not include_zero and q <= 1e-8: continue
        cost = q * float(df.loc[m, "Price_Rs_t"]) / 1000
        rows.append({"Material": m, "Group": GROUP_LABEL.get(df.loc[m, "Group"], df.loc[m, "Group"]),
                     "kg/t": q, "% Burden": (q / total * 100 if total else 0), "Cost ₹/t": cost,
                     "% Cost": (cost / total_cost * 100 if total_cost else 0)})
    rows.append({"Material": "TOTAL", "Group": "", "kg/t": total, "% Burden": 100.0, "Cost ₹/t": total_cost, "% Cost": 100.0})
    return pd.DataFrame(rows)

def return_sinter_panel(r):
    """Fresh burden vs BFR poured (memo) vs charged mix, plus the price of following the stock ratio."""
    cm = opt.charged_mix_summary(r["blend"], r["df"], r.get("b_eff", 0.0))
    st.markdown('<div class="panel"><div class="panel-title">RETURN SINTER ACCOUNTING — CHARGED MIX</div>'
                '<div class="small">BF Returns is poured into the mix and adds chemistry, but it is not in the burden and costs ₹0. '
                'IOL Fines is in the burden and the cost. Both are shares of the charged mix.</div></div>', unsafe_allow_html=True)
    cols = st.columns(5)
    items = [("FRESH BURDEN", f"{cm['Fresh_Burden_kg_t']:,.1f} kg/t", "dry, BFR excluded", "g"),
             ("BFR POURED (MEMO)", f"{cm['BFR_Poured_kg_t']:,.1f} kg/t", f"{cm['BFR_Pct_of_Mix']:.1f}% of mix • not in burden • ₹0", "s"),
             ("CHARGED MIX", f"{cm['Charged_Mix_kg_t']:,.1f} kg/t", "fresh burden + BFR", "s"),
             ("RETURN SINTER", f"{cm['Return_Sinter_Pct_of_Mix']:.1f}% of mix", f"IOL {cm['IOL_Pct_of_Mix']:.1f}% + BFR {cm['BFR_Pct_of_Mix']:.1f}%", "a"),
             ("GROSS SINTER", f"{cm['Gross_Sinter_kg_t']:,.0f} kg", "net 1,000 kg + BFR-derived sinter", "s")]
    for c, (l, v, s, k) in zip(cols, items):
        c.markdown(kpi(l, v, s, k), unsafe_allow_html=True)
    pr = r.get("premium")
    if pr:
        st.markdown(f'<div class="notice">📦 <b>Price of following the stock ratio:</b> ₹{pr["premium"]:+,.0f}/t versus the pure-cost recipe '
                    f'(₹{pr["pure_cost"]:,.0f}/t). Lower the <b>inventory weight</b> under Inputs → Inventory Rules to trade stock balance for cost.</div>',
                    unsafe_allow_html=True)

def status_banner(r):
    s = r.get("status")
    rep_ = r.get("report") or {}
    if s == "Optimal":
        st.markdown('<div class="notice"><b>OPTIMAL</b> — every quality spec is met.</div>', unsafe_allow_html=True)
    elif s == "Relaxed":
        st.markdown('<div class="notice notice-w"><b>RELAXED — INSIDE APPROVED TOLERANCE</b> — with the available materials one or more specs cannot be met exactly, '
                    'but every value is inside the plant-approved tolerance. See the goal table for what was given up.</div>', unsafe_allow_html=True)
    elif s == "Production_Risk" and r.get("blend"):
        st.markdown('<div class="notice notice-r"><b>PRODUCTION RISK — OUTSIDE APPROVED TOLERANCE</b> — no recipe stays inside the approved tolerances. '
                    'The closest achievable recipe is shown; review the goal table before using it.</div>', unsafe_allow_html=True)
    elif s == "Production_Risk":
        st.markdown('<div class="notice notice-r"><b>PRODUCTION RISK — NO VALID RECIPE</b> — the hard limits (RM stock, mass balance, Mill Scale, fixed recycle, '
                    'coke / heat balance, FeO band) cannot all be met with the available materials.</div>', unsafe_allow_html=True)
    elif s == "No_Production":
        st.markdown('<div class="notice notice-r"><b>NO PRODUCTION</b> — fuel requirement cannot be met.</div>', unsafe_allow_html=True)
    msgs = [m for m in (rep_.get("messages") or []) if m]
    if msgs:
        st.markdown('<div class="panel"><div class="panel-title">WHAT THE MODEL WANTS YOU TO KNOW</div></div>', unsafe_allow_html=True)
        for m in msgs:
            st.markdown(f"- {m}")
    notes = [d for d in (r.get("diagnostics") or []) if d and d not in msgs]
    if notes:
        with st.expander("Model notes"):
            for d in notes:
                st.markdown(f"- {d}")

def coke_diagnostic(blend, df):
    if not blend: return None
    try:
        return opt.compute_coke_heat_balance_diagnostic(
            blend, df, 1000, latent_heat=st.session_state.latent_heat, calcination_heat=st.session_state.calcination_heat,
            melting_heat=st.session_state.melting_heat, loss_fraction=st.session_state.loss_fraction,
            feo_min=st.session_state.feo_min, feo_target=st.session_state.feo_target, feo_max=st.session_state.feo_max)
    except Exception:
        return None

# ----------------------------- SETUP GATE ---------------------------------
def setup_missing_items():
    missing = []
    if not _rs_valid():
        missing.append(f"Return sinter — IOL Fines {opt.IOL_MIN_PCT:.0f}–{opt.IOL_MAX_PCT:.0f}% and BF Returns {opt.BFR_MIN_PCT:.0f}–{opt.BFR_MAX_PCT:.0f}% of the charged mix (Inputs → Return Sinter)")
    df = st.session_state.master_df
    for fuel in [m for m in df.index if str(df.loc[m, "Group"]) == "Fuel"]:
        if not st.session_state.available.get(fuel, False):
            missing.append(f"{fuel.replace('_',' ').title()} — mark available (Inputs → Coke & Fuel)")
        elif float(df.loc[fuel, "Price_Rs_t"]) <= 0:
            missing.append(f"{fuel.replace('_',' ').title()} — enter a price (Inputs → Coke & Fuel)")
    return missing

# ----------------------------- SIDEBAR -------------------------------------
with st.sidebar:
    st.markdown('<div class="sidebar-brand">HOSPET STEELS LIMITED</div><div class="small">Kalyani Steels × Mukand • Hospet</div>', unsafe_allow_html=True)
    st.markdown("---")
    nav_groups = [
        ("WORKSPACE", ["Dashboard", "Inputs"]),
        ("OPERATIONS", ["RM Stock & Materials", "Inventory Usage", "Recipe & Composition", "Manual Burden Control"]),
        ("ANALYSIS", ["Scenario Analysis", "Plant Run Validation", "Productivity", "Wet Specific Consumption"]),
        ("REPORTING", ["Reports"]),
        ("SYSTEM", ["Upload & Settings"]),
    ]
    for head, items in nav_groups:
        st.markdown(f'<div class="nav-title">{head}</div>', unsafe_allow_html=True)
        for item in items:
            if st.button(item, key="nav_" + item, use_container_width=True,
                         type="primary" if st.session_state.nav == item else "secondary"):
                st.session_state.nav = item; st.rerun()
    st.markdown("---")
    label, _ = status_label()
    st.markdown(f'<div class="small"><b>DATA</b><br>{st.session_state.source}<br>{len(st.session_state.master_df)} materials<br><br>'
                f'<b>OPTIMIZER</b><br>{label}<br><br><b>MODEL</b><br>{opt.VERSION}</div>', unsafe_allow_html=True)

# ----------------------------- DASHBOARD -----------------------------------
def dashboard():
    page_header("SINTER BURDEN CONTROL", "Cost optimization • quality assurance • raw material decision support")
    st.markdown('<div class="hero"><div class="panel-title">DATA CONTROL CENTER</div>'
                 '<div class="small">Upload one Master Chemistry Excel. Once activated, the raw material table below shows immediately with editable availability. Return Sinter, coke and fuel inputs are entered under Inputs before the optimizer can run for the first time.</div></div>',
                 unsafe_allow_html=True)

    up1, up2 = st.columns([1.7, 1])
    with up1:
        f = st.file_uploader("MASTER EXCEL • XLSX", type=["xlsx"], key="dash_master")
        if f is not None:
            try:
                newdf = opt.load_master_chemistry_excel({f.name: f.getvalue()})
                newdf = opt._ensure_material_role(newdf)
                if st.button("ACTIVATE MASTER", type="primary", key="activate_dash"):
                    st.session_state.master_df = newdf
                    st.session_state.source = f.name
                    st.session_state.available = {m: (float(newdf.loc[m, "Available_Tonnes"]) > 0) for m in newdf.index}
                    st.session_state.result = None
                    st.session_state.changed = False
                    st.session_state.runs = 0
                    st.session_state.manual_scenario_result = None
                    st.rerun()
                st.markdown(f'<div class="small">Detected {len(newdf)} materials in the uploaded workbook.</div>', unsafe_allow_html=True)
                for w_ in master_data_warnings(newdf):
                    st.warning(w_)
            except Exception as e:
                st.error(str(e))
    with up2:
        st.markdown(f'<div class="notice"><b>ACTIVE MASTER</b><br>{st.session_state.source}<br>{len(st.session_state.master_df)} materials</div>', unsafe_allow_html=True)
    _mw = master_data_warnings(active_df())
    if _mw:
        st.markdown('<div class="notice notice-w"><b>CHECK THE MASTER DATA</b> — these available rows look incomplete and the optimizer will use them as entered:<br>'
                    + "<br>".join(f"• {w}" for w in _mw) + '</div>', unsafe_allow_html=True)

    c1, c1b, c2, c3 = st.columns([1, 0.8, 1.4, 1])
    with c1:
        new_prod = st.number_input("Planning production (t)", min_value=1.0, value=float(st.session_state.production), step=100.0, key="prod",
                                   help="Sinter tonnes over the planning horizon. RM stock over this horizon is a hard cap on every material.")
        if new_prod != st.session_state.production:
            st.session_state.production = new_prod
            mark_changed("Dashboard → Planning production")
    with c1b:
        new_days = st.number_input("Horizon (days)", min_value=0.5, value=float(st.session_state.horizon_days), step=0.5, key="horizon_days_input",
                                   help="Used for days-of-cover reporting.")
        if new_days != st.session_state.horizon_days:
            st.session_state.horizon_days = new_days

    missing = setup_missing_items() if st.session_state.runs == 0 else []
    can_run = st.session_state.runs > 0 or len(missing) == 0

    if st.session_state.runs == 0:
        st.markdown('<div class="panel"><div class="panel-title">SETUP CHECKLIST</div>'
                     '<div class="small">Complete these once, under the Inputs page, before the first optimizer run.</div></div>', unsafe_allow_html=True)
        if missing:
            st.markdown('<div class="notice notice-w"><b>Still needed:</b><br>' + "<br>".join(f"• {m}" for m in missing) + '</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="notice"><b>✅ All required inputs are set.</b> You can run the optimizer.</div>', unsafe_allow_html=True)

    with c2:
        if st.button("🚀 RUN OPTIMIZER", type="primary", use_container_width=True, disabled=not can_run):
            try:
                with st.spinner(f"Optimizing {opt.VERSION}…"):
                    run_optimizer()
                st.rerun()
            except Exception as e:
                st.error(str(e))
    with c3:
        label, kind = status_label()
        st.markdown(f'<div class="notice {"notice-r" if kind=="r" else ("notice-w" if kind=="a" else "")}" style="text-align:center"><b>RUN #{st.session_state.runs}</b><br>{label}</div>', unsafe_allow_html=True)

    r = st.session_state.result

    # ---------------- KPI ROW 1 — exactly 5 KPIs ----------------
    if r and r["blend"]:
        bd = aligned_result_table(r["blend"], r["df"])
        rm_cost = float(bd.iloc[-1]["Cost ₹/t"])
        total_sinter_cost = rm_cost + float(st.session_state.om_cost)
        ql = quality_level(r["achieved"])
        pm = st.session_state.productivity_metrics
        if pm and "Target_Achieved" in pm:
            prod_val = "TARGET MET" if pm["Target_Achieved"] else "BELOW TARGET"
            prod_sub = f"{pm['Achievement_Pct']:.1f}% of target"
            prod_kind = "g" if pm["Target_Achieved"] else "r"
        else:
            prod_val = "PENDING"; prod_sub = "Set feed rate on Productivity page"; prod_kind = "s"
        cols = st.columns(5)
        cards = [
            ("RAW MATERIAL COST", f"₹{rm_cost:,.2f}/t", "Optimised sinter RM cost", "s"),
            ("O&M COST", f"₹{st.session_state.om_cost:,.2f}/t", "Set under Inputs", "a"),
            ("TOTAL SINTER COST", f"₹{total_sinter_cost:,.2f}/t", "RM + O&M", "s"),
            ("QUALITY", {"spec": "PASS", "tolerance": "WITHIN TOLERANCE", "outside": "OUT OF TOLERANCE"}[ql],
             {"spec": "All specs met", "tolerance": "Spec missed, inside approved tolerance", "outside": "Outside approved tolerance"}[ql],
             {"spec": "g", "tolerance": "a", "outside": "r"}[ql]),
            ("PRODUCTIVITY TARGET REACHED", prod_val, prod_sub, prod_kind),
        ]
        for c, (l, v, s, k) in zip(cols, cards):
            c.markdown(kpi(l, v, s, k), unsafe_allow_html=True)
    else:
        cols = st.columns(5)
        cards = [
            ("RAW MATERIAL COST", "—", "Run optimizer", "s"),
            ("O&M COST", f"₹{st.session_state.om_cost:,.2f}/t", "Set under Inputs", "a"),
            ("TOTAL SINTER COST", "—", "Run optimizer", "s"),
            ("QUALITY", "READY", "Awaiting run", "s"),
            ("PRODUCTIVITY TARGET REACHED", "PENDING", "Run optimizer first", "s"),
        ]
        for c, (l, v, s, k) in zip(cols, cards):
            c.markdown(kpi(l, v, s, k), unsafe_allow_html=True)

    if r:
        status_banner(r)
    if r and r.get("blend"):
        return_sinter_panel(r)

    # ---------------- KPI ROW 2 — CHEMISTRY ACHIEVEMENT ----------------
    st.markdown('<div class="panel"><div class="panel-title">CHEMISTRY CONSTRAINTS / ACHIEVED</div>'
                 '<div class="small">Green = within the defined target range. Red = outside the target range.</div></div>', unsafe_allow_html=True)
    if r and r["achieved"]:
        quality_cards(r["achieved"])
        goal_table_panel(r)
    else:
        st.info("Chemistry achievement will appear after optimization.")

    # ---------------- DRY BURDEN & COST TABLE ----------------
    st.markdown('<div class="panel"><div class="panel-title">DRY BURDEN & COST COMPOSITION</div>'
                 '<div class="small">RM Cost is editable and feeds the optimizer — changing it flags RERUN REQUIRED.</div></div>', unsafe_allow_html=True)
    if r and r["blend"]:
        df = r["df"]
        bd = aligned_result_table(r["blend"], df, include_total=True)
        view = bd.copy()
        view.insert(1, "RM Cost ₹/t", view["Material"].map(lambda m: float(df.loc[m, "Price_Rs_t"]) if m in df.index else np.nan))
        view = view.rename(columns={"kg/t": "kg/t", "% Burden": "% of Total Burden", "Cost ₹/t": "Cost/t", "% Cost": "% Cost"})
        view = view[["Material", "RM Cost ₹/t", "kg/t", "% of Total Burden", "Cost/t", "% Cost"]]
        edited = st.data_editor(
            view, hide_index=True, use_container_width=True, height=max(320, 34 * len(view) + 45),
            key="dashboard_dry_burden_editor",
            disabled=["Material", "kg/t", "% of Total Burden", "Cost/t", "% Cost"],
            column_config={
                "RM Cost ₹/t": st.column_config.NumberColumn("RM Cost ₹/t", min_value=0, step=1, format="%.0f"),
                "kg/t": st.column_config.NumberColumn("kg/t", format="%.2f"),
                "% of Total Burden": st.column_config.NumberColumn("% of Total Burden", format="%.2f"),
                "Cost/t": st.column_config.NumberColumn("Cost/t", format="₹ %.2f"),
                "% Cost": st.column_config.NumberColumn("% Cost", format="%.2f"),
            },
        )
        if not edited["RM Cost ₹/t"].equals(view["RM Cost ₹/t"]):
            for _, row in edited.iterrows():
                m = row["Material"]
                if m in st.session_state.master_df.index and pd.notna(row["RM Cost ₹/t"]):
                    st.session_state.master_df.loc[m, "Price_Rs_t"] = float(row["RM Cost ₹/t"])
            mark_changed("Dashboard → Dry Burden & Cost (RM Cost)")
            st.rerun()
    else:
        st.info("Run the optimizer to populate the dry burden and cost table.")

    # ---------------- RAW MATERIAL MASTER TABLE (always visible) ----------------
    st.markdown('<div class="panel"><div class="panel-title">RAW MATERIAL INPUTS — FULL WIDTH</div>'
                 '<div class="small">Chemistry, moisture, price, stock, optional Tech Min/Max (0 = no limit) and availability. Shown as soon as a master is activated; editable at any time.</div></div>', unsafe_allow_html=True)
    material_count = len(st.session_state.master_df.index)
    full_table_height = max(220, 34 * material_count + 52)
    inp = st.session_state.master_df.reset_index()[["Material", "Group"] + CHEM_COLS + ["Price_Rs_t", "Available_Tonnes", "Tech_Min", "Tech_Max"]].copy()
    inp.rename(columns={"SiO2": "SiO₂", "Al2O3": "Al₂O₃", "Moisture_Pct": "Moisture %", "Price_Rs_t": "Price ₹/t",
                        "Available_Tonnes": "RM Stock t", "Tech_Min": "Tech Min", "Tech_Max": "Tech Max"}, inplace=True)
    inp["Availability"] = [st.session_state.available.get(m, False) for m in inp.Material]
    ed = st.data_editor(
        inp, key="merged_master_editor", hide_index=True, use_container_width=True, height=full_table_height,
        disabled=["Group"],
        column_config={
            "Material": st.column_config.TextColumn("Raw Material", width="medium"),
            "Group": st.column_config.TextColumn("Group", width="small"),
            "Fe": st.column_config.NumberColumn("Fe %", width="small", format="%.2f"),
            "SiO₂": st.column_config.NumberColumn("SiO₂ %", width="small", format="%.2f"),
            "Al₂O₃": st.column_config.NumberColumn("Al₂O₃ %", width="small", format="%.2f"),
            "CaO": st.column_config.NumberColumn("CaO %", width="small", format="%.2f"),
            "MgO": st.column_config.NumberColumn("MgO %", width="small", format="%.2f"),
            "LOI": st.column_config.NumberColumn("LOI %", width="small", format="%.2f"),
            "Moisture %": st.column_config.NumberColumn("Moisture %", width="small", format="%.2f"),
            "Price ₹/t": st.column_config.NumberColumn("Price ₹/t", width="small", min_value=0, step=1, format="%.0f"),
            "RM Stock t": st.column_config.NumberColumn("RM Stock t", width="small", min_value=0, step=100, format="%.0f"),
            "Tech Min": st.column_config.NumberColumn("Tech Min", width="small", format="%.0f"),
            "Tech Max": st.column_config.NumberColumn("Tech Max", width="small", min_value=0, step=1, format="%.0f"),
            "Availability": st.column_config.CheckboxColumn("Use / Available"),
        }
    )
    if not ed.equals(inp):
        for _, row in ed.iterrows():
            m = row["Material"]
            for src, dst in [("Fe", "Fe"), ("SiO₂", "SiO2"), ("Al₂O₃", "Al2O3"), ("CaO", "CaO"), ("MgO", "MgO"),
                              ("LOI", "LOI"), ("Moisture %", "Moisture_Pct"), ("Tech Min", "Tech_Min"), ("Tech Max", "Tech_Max"),
                              ("Price ₹/t", "Price_Rs_t"), ("RM Stock t", "Available_Tonnes")]:
                st.session_state.master_df.loc[m, dst] = float(row[src])
            st.session_state.available[m] = bool(row["Availability"])
        mark_changed("Dashboard → Raw Material Inputs")
        st.rerun()

# ----------------------------- INPUTS PAGE ----------------------------------
def inputs_page():
    page_header("Inputs", "Return sinter, coke & fuel, inventory rules, fuel-ash chemistry, O&M / thermal parameters, quality tolerances and solver options — all feed the optimizer.")
    tabs = st.tabs(["RETURN SINTER", "COKE & FUEL", "INVENTORY RULES", "FUEL ASH CHEMISTRY", "O&M & THERMAL", "QUALITY & SOLVER"])

    with tabs[0]:
        st.markdown('<div class="small">Return sinter is poured into the mix. <b>IOL Fines</b> is a physical burden material (in the burden and cost). '
                    '<b>BF Returns</b> is chemistry-only (not in the burden, ₹0). Both are shares of the <b>charged mix</b> (fresh burden + BFR).</div>', unsafe_allow_html=True)
        rs1, rs2, rs3, rs4 = st.columns(4)
        with rs1:
            new_iol = st.number_input("IOL Fines (% of charged mix)", min_value=float(opt.IOL_MIN_PCT), max_value=float(opt.IOL_MAX_PCT),
                                       value=float(st.session_state.rs_iol_pct), step=0.5, key="rs_iol_input")
        with rs2:
            new_bfr = st.number_input("BF Returns (% of charged mix) — chemistry only", min_value=float(opt.BFR_MIN_PCT), max_value=float(opt.BFR_MAX_PCT),
                                       value=float(st.session_state.rs_bfr_pct), step=0.5, key="rs_bfr_input")
        if (new_iol, new_bfr) != (st.session_state.rs_iol_pct, st.session_state.rs_bfr_pct):
            st.session_state.rs_iol_pct, st.session_state.rs_bfr_pct = new_iol, new_bfr
            mark_changed("Inputs → Return Sinter")
        with rs3:
            ok = _rs_total_in_usual_range()
            st.markdown(kpi("TOTAL RETURN SINTER", f"{_rs_total():.1f}%", f"usual range {opt.RS_TOTAL_MIN_PCT:.0f}–{opt.RS_TOTAL_MAX_PCT:.0f}%", "g" if ok else "a"), unsafe_allow_html=True)
        with rs4:
            df_ = st.session_state.master_df
            bfr = opt._bfr_name(df_)
            note = "available" if (bfr and st.session_state.available.get(bfr, False) and float(df_.loc[bfr, "Available_Tonnes"]) > 0) else "unavailable → share 0%"
            st.markdown(kpi("BF RETURNS", note, "toggle under RM Stock & Materials", "s"), unsafe_allow_html=True)
        if not _rs_total_in_usual_range():
            st.warning(f"Total return sinter {_rs_total():.1f}% is outside the usual {opt.RS_TOTAL_MIN_PCT:.0f}–{opt.RS_TOTAL_MAX_PCT:.0f}% range. The optimizer will still run.")
        st.caption("If BF Returns is marked unavailable, or its stock cannot cover the planning horizon, its chemistry share is reduced automatically.")

    with tabs[1]:
        st.markdown('<div class="small">KSL and Local coke usage follows their dry stock × quality index (fixed carbon × calorific value). Enter the real CV, FC and price of each source — '
                    'identical values give identical quality. Total coke comes from the heat balance / FeO logic.</div>', unsafe_allow_html=True)
        df = st.session_state.master_df
        fuels = [m for m in df.index if df.loc[m, "Group"] == "Fuel"]
        for fuel in fuels:
            fc1, fc2, fc3, fc4, fc5 = st.columns(5)
            avail = fc1.checkbox(f"{fuel} available", value=st.session_state.available.get(fuel, False), key=f"avail_{fuel}")
            price = fc2.number_input(f"{fuel} Price ₹/t", min_value=0.0, value=float(df.loc[fuel, "Price_Rs_t"]), step=50.0, key=f"price_{fuel}")
            stock = fc3.number_input(f"{fuel} Stock t", min_value=0.0, value=float(df.loc[fuel, "Available_Tonnes"]), step=100.0, key=f"stock_{fuel}")
            cv = fc4.number_input(f"{fuel} CV (kcal/kg)", min_value=1000.0, value=float(df.loc[fuel, "CV_kcal_kg"]) or opt.DEFAULT_COKE_CV_KCAL_KG, step=50.0, key=f"cv_{fuel}")
            fcv = fc5.number_input(f"{fuel} FC (%)", min_value=1.0, max_value=100.0, value=float(df.loc[fuel, "FC_Pct"]) or opt.DEFAULT_COKE_FC_PCT, step=0.1, key=f"fc_{fuel}")
            cur_cv = float(df.loc[fuel, "CV_kcal_kg"]) or opt.DEFAULT_COKE_CV_KCAL_KG
            cur_fc = float(df.loc[fuel, "FC_Pct"]) or opt.DEFAULT_COKE_FC_PCT
            if (avail != st.session_state.available.get(fuel, False) or price != float(df.loc[fuel, "Price_Rs_t"])
                    or stock != float(df.loc[fuel, "Available_Tonnes"]) or cv != cur_cv or fcv != cur_fc):
                st.session_state.available[fuel] = avail
                st.session_state.master_df.loc[fuel, "Price_Rs_t"] = price
                st.session_state.master_df.loc[fuel, "Available_Tonnes"] = stock
                st.session_state.master_df.loc[fuel, "CV_kcal_kg"] = cv
                st.session_state.master_df.loc[fuel, "FC_Pct"] = fcv
                mark_changed("Inputs → Coke & Fuel")

        st.markdown('<div class="panel"><div class="panel-title">COKE PRACTICAL LIMITS</div></div>', unsafe_allow_html=True)
        q3, q4 = st.columns(2)
        new_min = q3.number_input("Coke Min (kg/t)", min_value=0.0, value=float(st.session_state.coke_min_rate), step=1.0)
        new_max = q4.number_input("Coke Max (kg/t)", min_value=0.0, value=float(st.session_state.coke_max_rate), step=1.0)
        if (new_min, new_max) != (st.session_state.coke_min_rate, st.session_state.coke_max_rate):
            st.session_state.coke_min_rate, st.session_state.coke_max_rate = new_min, new_max
            mark_changed("Inputs → Coke & Fuel")
        new_override = st.checkbox("Manual Coke Override (fix total fuel rate)", value=bool(st.session_state.manual_coke_override))
        if new_override != st.session_state.manual_coke_override:
            st.session_state.manual_coke_override = new_override
            mark_changed("Inputs → Coke & Fuel")
        if st.session_state.manual_coke_override:
            new_rate = st.number_input("Fixed Coke Rate (kg/t)", min_value=float(st.session_state.coke_min_rate),
                                        max_value=float(st.session_state.coke_max_rate), value=min(max(float(st.session_state.manual_coke_rate), float(st.session_state.coke_min_rate)), float(st.session_state.coke_max_rate)), step=.5)
            if new_rate != st.session_state.manual_coke_rate:
                st.session_state.manual_coke_rate = new_rate
                mark_changed("Inputs → Coke & Fuel")

    with tabs[2]:
        st.markdown('<div class="small">Iron ores (except Mill Scale) and coke sources are used in proportion to their RM stock, as closely as the quality limits allow. '
                    'Mill Scale stays at 5–15% of the burden. Flux is chosen by chemistry requirement and cost.</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            new_w = st.slider("Inventory weight  (0 = pure cost, 1 = follow the stock ratio as closely as possible)", 0.0, 1.0,
                              float(st.session_state.inventory_weight), 0.05, key="inv_weight_slider")
            new_basis = st.selectbox("Stock is entered as", ["as_received", "dry"], index=0 if st.session_state.stock_basis == "as_received" else 1,
                                     format_func=lambda v: "As-received (wet) tonnes — converted to dry with moisture" if v == "as_received" else "Dry tonnes",
                                     key="stock_basis_select")
            new_flux = st.number_input("Max total flux (% of burden) — safety guard", min_value=10.0, max_value=45.0,
                                       value=float(st.session_state.max_flux_pct), step=1.0, key="max_flux_input")
        with c2:
            new_tech = st.checkbox("Also apply Tech Min/Max to iron ores and coke (off by default)", value=bool(st.session_state.apply_tech_ores), key="apply_tech_check")
            new_cap_on = st.checkbox("Guard rail: cap each ore's share of the non-Mill-Scale ore total", value=bool(st.session_state.use_ore_share_cap), key="cap_on_check")
            new_cap = st.number_input("Per-ore share cap (%) — scales up automatically when only 2–3 ores are available", min_value=20.0, max_value=100.0,
                                      value=float(st.session_state.ore_share_cap_pct), step=5.0, key="cap_pct_input", disabled=not new_cap_on)
        new_vals = (new_w, new_basis, new_flux, new_tech, new_cap_on, new_cap)
        old_vals = (st.session_state.inventory_weight, st.session_state.stock_basis, st.session_state.max_flux_pct,
                    st.session_state.apply_tech_ores, st.session_state.use_ore_share_cap, st.session_state.ore_share_cap_pct)
        if new_vals != old_vals:
            (st.session_state.inventory_weight, st.session_state.stock_basis, st.session_state.max_flux_pct,
             st.session_state.apply_tech_ores, st.session_state.use_ore_share_cap, st.session_state.ore_share_cap_pct) = new_vals
            mark_changed("Inputs → Inventory Rules")
        st.caption("Planning horizon (production tonnes and days) is set on the Dashboard. RM stock over that horizon is a hard cap on every material.")

    with tabs[3]:
        st.markdown('<div class="small">Ash is entered on as-received basis. The backend converts retained fuel ash to the dry-burden calculation.</div>', unsafe_allow_html=True)
        ash_rows = []
        for fuel, vals in st.session_state.fuel_ash_settings.items():
            ash_rows.append({"Fuel": fuel, "Ash % AR": float(vals.get("Ash_AR_Pct", 0)), "SiO₂ % in ash": float(vals.get("Ash_SiO2_Pct", 0)),
                             "Al₂O₃ % in ash": float(vals.get("Ash_Al2O3_Pct", 0)), "CaO % in ash": float(vals.get("Ash_CaO_Pct", 0)),
                             "MgO % in ash": float(vals.get("Ash_MgO_Pct", 0)), "Fe₂O₃ % in ash": float(vals.get("Ash_Fe2O3_Pct", 0))})
        ash_df = pd.DataFrame(ash_rows)
        ash_ed = st.data_editor(ash_df, hide_index=True, use_container_width=True, key="inputs_fuel_ash", column_config={
            "Fuel": st.column_config.TextColumn("Fuel", disabled=True),
            "Ash % AR": st.column_config.NumberColumn("Ash % AR", min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
            "SiO₂ % in ash": st.column_config.NumberColumn("SiO₂ % in ash", min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
            "Al₂O₃ % in ash": st.column_config.NumberColumn("Al₂O₃ % in ash", min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
            "CaO % in ash": st.column_config.NumberColumn("CaO % in ash", min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
            "MgO % in ash": st.column_config.NumberColumn("MgO % in ash", min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
            "Fe₂O₃ % in ash": st.column_config.NumberColumn("Fe₂O₃ % in ash", min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
        })
        if not ash_ed.equals(ash_df):
            for _, row in ash_ed.iterrows():
                fuel = str(row["Fuel"])
                st.session_state.fuel_ash_settings[fuel] = {
                    "Ash_AR_Pct": float(row["Ash % AR"]), "Ash_SiO2_Pct": float(row["SiO₂ % in ash"]),
                    "Ash_Al2O3_Pct": float(row["Al₂O₃ % in ash"]), "Ash_CaO_Pct": float(row["CaO % in ash"]),
                    "Ash_MgO_Pct": float(row["MgO % in ash"]), "Ash_Fe2O3_Pct": float(row["Fe₂O₃ % in ash"])}
            mark_changed("Inputs → Fuel Ash Chemistry")

    with tabs[4]:
        c1, c2 = st.columns([1, 2])
        with c1:
            new_om = st.number_input("O&M Cost ₹/t", min_value=0.0, value=float(st.session_state.om_cost), step=50.0, key="om_cost_input")
            if new_om != st.session_state.om_cost:
                st.session_state.om_cost = new_om
        with c2:
            st.caption("O&M is added to total sinter cost; it does not change the raw-material optimizer objective, so it updates immediately without requiring a rerun.")
        st.markdown('<div class="panel"><div class="panel-title">THERMAL PARAMETERS</div><div class="small">These feed the heat-balance / FeO model. Changing them requires a rerun.</div></div>', unsafe_allow_html=True)
        q5, q6, q7 = st.columns(3)
        new_latent = q5.number_input("Latent Heat", value=float(st.session_state.latent_heat), step=10.0)
        new_calc = q6.number_input("Calcination Heat", value=float(st.session_state.calcination_heat), step=10.0)
        new_melt = q7.number_input("Melting Heat", value=float(st.session_state.melting_heat), step=5.0)
        q8, q9 = st.columns(2)
        new_loss = q8.number_input("Heat Loss Fraction (0-1)", min_value=0.0, max_value=0.9, value=float(st.session_state.loss_fraction), step=0.01)
        new_firing = q9.number_input("Firing Ratio Max", value=float(st.session_state.firing_ratio_max), step=0.01)
        q10, q11, q12 = st.columns(3)
        new_feo_min = q10.number_input("FeO Min (%)", value=float(st.session_state.feo_min), step=.1)
        new_feo_target = q11.number_input("FeO Target (%)", value=float(st.session_state.feo_target), step=.1)
        new_feo_max = q12.number_input("FeO Max (%)", value=float(st.session_state.feo_max), step=.1)
        thermal_vals = (new_latent, new_calc, new_melt, new_loss, new_firing, new_feo_min, new_feo_target, new_feo_max)
        thermal_old = (st.session_state.latent_heat, st.session_state.calcination_heat, st.session_state.melting_heat,
                       st.session_state.loss_fraction, st.session_state.firing_ratio_max, st.session_state.feo_min,
                       st.session_state.feo_target, st.session_state.feo_max)
        if thermal_vals != thermal_old:
            (st.session_state.latent_heat, st.session_state.calcination_heat, st.session_state.melting_heat,
             st.session_state.loss_fraction, st.session_state.firing_ratio_max, st.session_state.feo_min,
             st.session_state.feo_target, st.session_state.feo_max) = thermal_vals
            mark_changed("Inputs → O&M & Thermal")

        r = st.session_state.result
        if r and r.get("blend"):
            diag = coke_diagnostic(r["blend"], r["df"])
            if diag:
                st.markdown('<div class="panel"><div class="panel-title">LIVE DIAGNOSTICS</div></div>', unsafe_allow_html=True)
                x, y, z = st.columns(3)
                x.metric("Predicted FeO", f"{diag.get('FeO_Estimate_Pct', np.nan):.2f}%")
                y.metric("Thermal Surplus", f"{diag.get('Thermal_Surplus_kcal', np.nan):,.0f}")
                z.metric("Firing Ratio", f"{diag.get('Firing_Ratio', np.nan):.3f}")
                st.caption(str(diag.get("Controller_Suggestion", "")))

    with tabs[5]:
        st.markdown('<div class="small">The solver first tries to meet every quality <b>spec</b>. If it cannot, it gives up as little as possible, in priority order '
                    '(Fe and Basicity first, then CaO, MgO, Al₂O₃, SiO₂, Al₂O₃/SiO₂) and never goes past the <b>approved tolerance</b> below unless nothing else is possible '
                    '(then the run is labelled Production Risk).</div>', unsafe_allow_html=True)
        st.markdown('<div class="notice notice-w"><b>PLACEHOLDERS</b> — every tolerance except SiO₂ max 6.2 is a placeholder. Confirm each one with the plant before using the results for decisions. '
                    'A tolerance can never be tighter than its spec; the model widens it to the spec automatically.</div>', unsafe_allow_html=True)
        tol_defs = [("Fe_min", "Fe min %"), ("Fe_max", "Fe max %"), ("SiO2_max", "SiO₂ max %"), ("Al2O3_max", "Al₂O₃ max %"),
                    ("Al2O3_SiO2_max", "Al₂O₃/SiO₂ max"), ("Basicity_min", "Basicity min"), ("Basicity_max", "Basicity max"),
                    ("MgO_min", "MgO min %"), ("MgO_max", "MgO max %"), ("CaO_min", "CaO min %"), ("CaO_max", "CaO max %")]
        new_tol = {}
        for row_start in range(0, len(tol_defs), 4):
            cols = st.columns(4)
            for col, (k, lab) in zip(cols, tol_defs[row_start:row_start + 4]):
                new_tol[k] = col.number_input(lab, min_value=0.0, max_value=100.0, value=float(st.session_state.tolerances[k]), step=0.05, key=f"tol_in_{k}")
        if st.button("↺ Reset tolerances to defaults", key="tol_reset"):
            st.session_state.tolerances = dict(opt.DEFAULT_TOLERANCES)
            for k in opt.DEFAULT_TOLERANCES:
                st.session_state.pop(f"tol_in_{k}", None)
            mark_changed("Inputs → Quality & Solver"); st.rerun()
        if new_tol != {k: float(v) for k, v in st.session_state.tolerances.items()}:
            st.session_state.tolerances = new_tol
            mark_changed("Inputs → Quality & Solver")
        st.markdown('<div class="panel"><div class="panel-title">SOLVER OPTIONS</div></div>', unsafe_allow_html=True)
        s1, s2 = st.columns(2)
        with s1:
            new_float = st.checkbox("Let the model choose IOL Fines / BF Returns inside their ranges (total ≤ 25% of the charged mix)",
                                     value=bool(st.session_state.float_returns), key="float_returns_check")
            new_margin = st.number_input("Safety margin on SiO₂ / Al₂O₃ / Fe (pp)", min_value=0.0, max_value=1.0, step=0.05,
                                          value=float(st.session_state.robust_margin), key="robust_margin_input",
                                          help="Steers the recipe this far inside the spec so small lab variations do not push it out.")
        with s2:
            new_band = st.number_input("Hold-the-ratio comparison band (± pp)", min_value=1.0, max_value=30.0, step=1.0,
                                        value=float(st.session_state.ratio_band_pp), key="ratio_band_input",
                                        help="Option B on the Inventory Usage page keeps every ore within this many points of its stock share.")
            new_feo_tol = st.number_input("FeO closeness allowance (FeO points)", min_value=0.0, max_value=2.0, step=0.05,
                                           value=float(st.session_state.feo_pin_tol), key="feo_pin_input",
                                           help="How far FeO may drift from its best value while later goals (stock ratio, cost) are optimised.")
        solver_new = (new_float, new_margin, new_band, new_feo_tol)
        solver_old = (st.session_state.float_returns, st.session_state.robust_margin, st.session_state.ratio_band_pp, st.session_state.feo_pin_tol)
        if solver_new != solver_old:
            (st.session_state.float_returns, st.session_state.robust_margin, st.session_state.ratio_band_pp, st.session_state.feo_pin_tol) = solver_new
            mark_changed("Inputs → Quality & Solver")

# ----------------------------- RM STOCK & MATERIALS -------------------------
def rm_stock_materials():
    page_header("RM Stock & Materials", "Daily material availability, price and stock — all from the same master workbook.")
    st.markdown('<div class="notice">Availability, price and RM stock are editable here. Stock is a hard cap over the planning horizon and steers ore and coke usage. '
                'Tech Min / Max are <b>optional</b> (0 = no limit) and apply only to flux and recycle materials unless enabled under Inputs → Inventory Rules.</div>', unsafe_allow_html=True)
    inp = st.session_state.master_df.reset_index()[["Material", "Group", "Price_Rs_t", "Available_Tonnes", "Tech_Min", "Tech_Max"]].copy()
    inp.rename(columns={"Price_Rs_t": "Price ₹/t", "Available_Tonnes": "RM Stock t",
                        "Tech_Min": "Tech Min kg/t (optional)", "Tech_Max": "Tech Max kg/t (optional)"}, inplace=True)
    inp["Availability / Include"] = [st.session_state.available.get(m, True) for m in inp.Material]
    ed = st.data_editor(inp, key="rm_editor", hide_index=True, use_container_width=True,
                         height=max(250, 38 * len(inp) + 45), disabled=["Material", "Group"])
    if not ed.equals(inp):
        for _, row in ed.iterrows():
            m = row.Material
            st.session_state.master_df.loc[m, "Price_Rs_t"] = float(row["Price ₹/t"])
            st.session_state.master_df.loc[m, "Available_Tonnes"] = float(row["RM Stock t"])
            st.session_state.master_df.loc[m, "Tech_Min"] = float(row["Tech Min kg/t (optional)"])
            st.session_state.master_df.loc[m, "Tech_Max"] = float(row["Tech Max kg/t (optional)"])
            st.session_state.available[m] = bool(row["Availability / Include"])
        mark_changed("RM Stock & Materials")
        st.rerun()

# ----------------------------- RECIPE & COMPOSITION --------------------------
def composition_content(r, kind):
    df = r["df"]; blend = r["blend"]
    total = sum(float(v) for v in blend.values())
    cost = sum(float(blend[m]) * float(df.loc[m, "Price_Rs_t"]) / 1000 for m in blend)
    vals = ({GROUP_LABEL[g]: sum(float(blend[m]) for m in blend if df.loc[m, "Group"] == g) for g in GROUPS} if kind == "burden"
            else {GROUP_LABEL[g]: sum(float(blend[m]) * float(df.loc[m, "Price_Rs_t"]) / 1000 for m in blend if df.loc[m, "Group"] == g) for g in GROUPS})
    center = total if kind == "burden" else cost
    rows = [{"Group": g, "Value": v, ("% of Burden" if kind == "burden" else "% of Cost"): (v / center * 100 if center else 0)} for g, v in vals.items()]
    st.markdown('<div class="panel"><div class="panel-title">GROUP SUMMARY</div></div>', unsafe_allow_html=True)
    st.table(pd.DataFrame(rows).round(3))
    st.markdown('<div class="panel"><div class="panel-title">MATERIAL BREAKDOWN</div></div>', unsafe_allow_html=True)
    st.table(result_table(blend, df).round(3))

def recipe_content(r):
    bd = aligned_result_table(r["blend"], r["df"]); ach = r["achieved"]
    cost = float(bd.iloc[-1]["Cost ₹/t"]); total = float(bd.iloc[-1]["kg/t"])
    c = st.columns(4)
    for col, (l, v, s, k) in zip(c, [("TOTAL COST", f"₹{cost:,.2f}/t", "Optimized", "s"),
                                       ("BURDEN", f"{total:,.2f} kg/t", "Fresh burden, BFR excluded", "g"),
                                       ("Fe", f"{ach['Fe']:.3f}%", f"{opt.FE_LOWER:.1f}–{opt.FE_UPPER:.1f}", "a"),
                                       ("QUALITY", {"spec": "PASS", "tolerance": "WITHIN TOLERANCE", "outside": "OUT OF TOLERANCE"}[quality_level(ach)], "Spec / approved tolerance", {"spec": "g", "tolerance": "a", "outside": "r"}[quality_level(ach)])]):
        col.markdown(kpi(l, v, s, k), unsafe_allow_html=True)
    status_banner(r)
    st.markdown('<div class="panel"><div class="panel-title">QUALITY</div></div>', unsafe_allow_html=True)
    quality_cards(ach)
    goal_table_panel(r)
    return_sinter_panel(r)
    st.markdown('<div class="panel"><div class="panel-title">RECIPE</div></div>', unsafe_allow_html=True)
    st.table(bd.round(3))
    cm = opt.charged_mix_summary(r["blend"], r["df"], r.get("b_eff", 0.0))
    st.caption(f"Memo: BF Returns poured ≈ {cm['BFR_Poured_kg_t']:,.1f} kg/t ({cm['BFR_Pct_of_Mix']:.1f}% of the charged mix) — chemistry only, not in the burden, ₹0.")

def recipe_composition():
    page_header("Recipe & Composition", "Optimized recipe and burden/cost composition — one page, one source of truth.")
    r = st.session_state.result
    if not r or not r["blend"]:
        st.info("Run the optimizer first."); return
    tabs = st.tabs(["RECIPE", "BURDEN COMPOSITION", "COST COMPOSITION"])
    with tabs[0]: recipe_content(r)
    with tabs[1]: composition_content(r, "burden")
    with tabs[2]: composition_content(r, "cost")

# ----------------------------- MANUAL BURDEN CONTROL --------------------------
def manual():
    page_header("Manual Burden Control", "Practical scenario analysis — the optimized recipe remains the frozen theoretical baseline. Changes here never require the main optimizer to be rerun.")
    r = st.session_state.result
    if not r or not r.get("blend"):
        st.info("Run the optimizer first."); return

    df = r["df"]
    base = dict(st.session_state.get("manual_base") or r["blend"])
    st.session_state.manual_base = base

    st.markdown('<div class="notice"><b>THEORETICAL BASELINE → PRACTICAL SCENARIO</b><br>Change one or more raw materials. The optimizer re-optimizes everything else while preserving availability, chemistry, RM stock, coke/thermal limits, the BFR chemistry-only rule and the IOL share of the charged mix.</div>', unsafe_allow_html=True)

    mode = st.radio("Adjustment mode", ["kg/t", "%"], horizontal=True, key="manual_mode")
    rows = []
    for m in df.index:
        b = float(base.get(m, 0.0))
        if b <= 1e-9 and float(r["blend"].get(m, 0.0)) <= 1e-9: continue
        total = sum(float(v) for v in base.values())
        opct = b / total * 100 if total else 0.0
        locked = str(df.loc[m, "Group"]) in {"IOL_Fines_Mandate", "BF_Returns_Mandate"}
        rows.append({"Raw Material": m, "Optimized kg/t": b, "Optimized %": opct, "Change": 0.0, "Locked": locked})
    base_table = pd.DataFrame(rows)
    ed = st.data_editor(
        base_table, hide_index=True, use_container_width=True, key="manual_scenario_editor",
        disabled=["Raw Material", "Optimized kg/t", "Optimized %", "Locked"],
        column_config={
            "Optimized kg/t": st.column_config.NumberColumn("Optimized kg/t", format="%.2f"),
            "Optimized %": st.column_config.NumberColumn("Optimized %", format="%.2f%%"),
            "Change": st.column_config.NumberColumn("Change kg/t" if mode == "kg/t" else "Change %", step=0.5, format="%+.2f" if mode == "kg/t" else "%+.2f%%"),
            "Locked": st.column_config.CheckboxColumn("Locked"),
        }, height=max(180, 38 * len(base_table) + 45))

    # Only materials that the user actually changed are fixed in the practical
    # scenario. Unchanged materials must remain free so the optimiser can
    # re-optimise them around the practical constraint. Previously every
    # unlocked material was being added to `fixed`, which effectively froze the
    # entire baseline recipe and could return no practical result.
    fixed = {}
    for _, row in ed.iterrows():
        m = row["Raw Material"]
        if bool(row["Locked"]):
            continue
        b = float(row["Optimized kg/t"]); ch = float(row["Change"] or 0.0)
        if abs(ch) <= 1e-12:
            continue
        q = b + ch if mode == "kg/t" else b * (1 + ch / 100.0)
        fixed[m] = max(0.0, q)

    if st.button("🔄 RECALCULATE PRACTICAL SCENARIO", type="primary", use_container_width=True, key="manual_reopt"):
        with st.spinner("Re-optimizing around the practical constraints…"):
            status, blend, cost, achieved, diagnostics, is_fallback = opt.solve_manual_scenario(
                df, float(st.session_state.production), TARGETS, base, fixed, **{**_solver_kwargs(), "compare_ratio_hold": False})
            st.session_state.manual_scenario_result = {"status": status, "blend": blend, "cost": cost,
                                                          "achieved": achieved, "diagnostics": diagnostics, "df": df.copy()}
        st.rerun()

    practical = st.session_state.get("manual_scenario_result")
    if practical and practical.get("blend"):
        pbd = aligned_result_table(practical["blend"], df, include_total=True)
        base_cost = sum(float(base[m]) * float(df.loc[m, "Price_Rs_t"]) / 1000 for m in base if m in df.index)
        practical_cost = float(pbd.iloc[-1]["Cost ₹/t"])
        base_total = sum(float(v) for v in base.values()); practical_total = sum(float(v) for v in practical["blend"].values())
        a, b, c, d = st.columns(4)
        a.markdown(kpi("BASELINE COST", f"₹{base_cost:,.2f}/t", "Theoretical", "s"), unsafe_allow_html=True)
        b.markdown(kpi("PRACTICAL COST", f"₹{practical_cost:,.2f}/t", f"Δ ₹{practical_cost-base_cost:+,.2f}/t", "a"), unsafe_allow_html=True)
        c.markdown(kpi("BASELINE BURDEN", f"{base_total:,.2f} kg/t", "Theoretical", "g"), unsafe_allow_html=True)
        d.markdown(kpi("PRACTICAL BURDEN", f"{practical_total:,.2f} kg/t", f"Δ {practical_total-base_total:+,.2f}", "g"), unsafe_allow_html=True)

        compare = []
        for m in df.index:
            ov = float(base.get(m, 0)); pv = float(practical["blend"].get(m, 0))
            if ov == 0 and pv == 0: continue
            compare.append({"Raw Material": m, "Optimized kg/t": ov, "Optimized %": ov/base_total*100 if base_total else 0,
                            "Practical kg/t": pv, "Practical %": pv/practical_total*100 if practical_total else 0,
                            "Change kg/t": pv-ov, "Change %": (pv-ov)/ov*100 if ov else np.nan})
        comp = pd.DataFrame(compare)
        comp.loc[len(comp)] = {"Raw Material": "TOTAL", "Optimized kg/t": base_total, "Optimized %": 100.0,
                               "Practical kg/t": practical_total, "Practical %": 100.0,
                               "Change kg/t": practical_total-base_total, "Change %": (practical_total-base_total)/base_total*100 if base_total else 0}
        st.subheader("Theoretical vs Practical")
        st.table(comp.round(3))
        ach = practical["achieved"]
        st.subheader("Practical chemistry / compliance")
        quality_cards(ach)
        st.table(opt.quality_table(ach, TARGETS).round(3))
    else:
        st.info("Enter a change and press RE-CALCULATE PRACTICAL SCENARIO. The original optimized recipe remains unchanged.")

# ----------------------------- SCENARIO ANALYSIS ------------------------------
def whatif_content():
    r = st.session_state.result
    if not r or not r.get("blend"):
        st.info("Run the optimizer first."); return
    if st.button("▶ RUN MATERIAL SHORTAGE SCENARIOS", type="primary", key="run_whatif"):
        with st.spinner("Evaluating scenarios…"):
            base_cost = float(r.get("cost") or 0)
            scenarios = opt.what_if_analysis(active_df(), TARGETS, production_tonnes=float(st.session_state.production), **_solver_kwargs())
            if "Cost ₹/t" in scenarios.columns:
                scenarios["Cost Impact ₹/t"] = scenarios["Cost ₹/t"].apply(lambda x: round(float(x) - base_cost, 2) if pd.notna(x) else np.nan)
                # a recipe outside the approved tolerance is only the "closest" one, so its cost is not comparable
                risk = scenarios["Status"].astype(str).eq("Production_Risk")
                scenarios.loc[risk, ["Cost ₹/t", "Cost Impact ₹/t"]] = np.nan
                scenarios["Status"] = scenarios["Status"].replace({"Production_Risk": "Production risk (outside tolerance, cost not comparable)",
                                                                    "Quality relaxed": "Relaxed (inside tolerance)"})
            st.session_state.whatif = scenarios
    if st.session_state.whatif is not None:
        st.table(st.session_state.whatif.round(3))
    else:
        st.info("Run the scenario analysis.")

def bottleneck_content():
    r = st.session_state.result
    if not r or not r["achieved"]:
        st.info("Run optimizer first."); return
    st.table(opt.quality_table(r["achieved"], TARGETS).round(3))

def scenario_analysis():
    page_header("Scenario Analysis", "Material shortage stress-testing and quality-constraint pressure, side by side.")
    tabs = st.tabs(["MATERIAL SHORTAGE", "CONSTRAINT PRESSURE"])
    with tabs[0]:
        st.caption("Test one-at-a-time material unavailability against the current model.")
        whatif_content()
    with tabs[1]:
        st.caption("Identify the constraints closest to their limits.")
        bottleneck_content()

# ----------------------------- PLANT RUN VALIDATION ----------------------------
def plant_run_validation():
    page_header("Plant Run Validation", "Validate the same backend calculation engine against a fixed actual plant recipe and plant laboratory results.")
    df = active_df().copy()
    if df.empty:
        st.warning("Load a valid master chemistry file first."); return
    st.markdown('<div class="notice">The actual plant recipe is FIXED. The optimizer is NOT allowed to change it. Moisture is excluded from sinter chemistry; actual dry burden and actual finished sinter are retained exactly as entered.</div>', unsafe_allow_html=True)
    mats = list(df.index)
    st.markdown('<div class="panel"><div class="panel-title">STEP 1 — ACTUAL PLANT RECIPE</div></div>', unsafe_allow_html=True)
    default_recipe = pd.DataFrame({"Material": mats, "Actual kg/t sinter": [
        float(st.session_state.result["blend"].get(m, 0.0)) if st.session_state.result and st.session_state.result.get("blend") else 0.0 for m in mats]})
    recipe = st.data_editor(default_recipe, key="validation_recipe", hide_index=True, use_container_width=True,
                             column_config={"Material": st.column_config.TextColumn("Material", disabled=True),
                                           "Actual kg/t sinter": st.column_config.NumberColumn("Actual kg/t sinter", min_value=0.0, format="%.2f")})
    finished = st.number_input("Finished (net) sinter made by this burden (kg)", min_value=0.01, value=1000.0, step=1.0, key="validation_finished_sinter")
    val_bfr = st.number_input("BF Returns poured in this run (% of charged mix)", min_value=0.0, max_value=30.0,
                               value=float(st.session_state.rs_bfr_pct), step=0.5, key="validation_bfr_pct")
    st.markdown('<div class="panel"><div class="panel-title">STEP 2 — ACTUAL PLANT-RUN MATERIAL CHEMISTRY</div><div class="small">Edit the chemistry for this specific plant run.</div></div>', unsafe_allow_html=True)
    chem_view = df[["Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Moisture_Pct"]].reset_index().rename(columns={"SiO2": "SiO₂", "Al2O3": "Al₂O₃", "Moisture_Pct": "Moisture %"})
    chem_edit = st.data_editor(chem_view, key="validation_chemistry", hide_index=True, use_container_width=True,
                                column_config={c: st.column_config.NumberColumn(c, min_value=0.0, format="%.2f") for c in ["Fe", "SiO₂", "Al₂O₃", "CaO", "MgO", "LOI", "Moisture %"]})
    st.markdown('<div class="panel"><div class="panel-title">STEP 3 — ACTUAL PLANT LAB RESULT</div></div>', unsafe_allow_html=True)
    lab_cols = st.columns(6); lab = {}
    for col, key in zip(lab_cols, ["Fe", "SiO2", "Al2O3", "CaO", "MgO", "Basicity"]):
        lab[key] = col.number_input(key, min_value=0.0, value=54.0 if key == "Fe" else 0.0, step=0.01, key="lab_" + key)
    if st.button("🔬 RUN PLANT VALIDATION", type="primary", use_container_width=True):
        try:
            actual_blend = {str(row["Material"]): float(row["Actual kg/t sinter"]) for _, row in recipe.iterrows() if float(row["Actual kg/t sinter"]) > 0}
            if not actual_blend: raise ValueError("Enter the actual plant recipe.")
            if any(m not in df.index for m in actual_blend): raise ValueError("Plant recipe contains a material missing from the active master.")
            run_df = df.copy()
            cv = chem_edit.copy().set_index("Material").rename(columns={"SiO₂": "SiO2", "Al₂O₃": "Al2O3", "Moisture %": "Moisture_Pct"})
            for m in run_df.index:
                if m in cv.index:
                    for c in ["Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Moisture_Pct"]:
                        run_df.loc[m, c] = float(cv.loc[m, c])
            if any(float(lab[k]) <= 0 for k in lab): raise ValueError("Enter all six plant laboratory results before running validation.")
            predicted = opt.compute_validation_chemistry(actual_blend, run_df, float(finished), fuel_ash_settings=_current_fuel_ash_settings(), bf_nominal=float(val_bfr) / 100.0)
            compare = []
            for key in ["Fe", "SiO2", "Al2O3", "CaO", "MgO", "Basicity"]:
                model = float(predicted.get(key, 0)); plant = float(lab[key]); err = model - plant; ae = abs(err)
                status = "🟢 EXCELLENT" if ae <= 0.20 else ("🟡 ACCEPTABLE" if ae <= 0.50 else "🔴 SIGNIFICANT")
                compare.append({"Parameter": key, "Model": model, "Plant Lab": plant, "Model - Plant": err, "Absolute Error": ae, "Status": status})
            comp = pd.DataFrame(compare)
            max_err = float(comp["Absolute Error"].max())
            verdict = "🟢 EXCELLENT" if max_err <= 0.20 else ("🟡 ACCEPTABLE" if max_err <= 0.50 else "🔴 SIGNIFICANT DEVIATION")
            total_burden = sum(actual_blend.values())
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Actual dry burden", f"{total_burden:,.2f} kg/t")
            c2.metric("Finished sinter", f"{finished:,.2f} kg")
            c3.metric("Yield on fresh burden", f"{predicted.get('Actual_yield_pct', 0):.2f}%")
            c4.metric("Validation verdict", verdict)
            st.dataframe(comp, hide_index=True, use_container_width=True,
                         column_config={c: st.column_config.NumberColumn(c, format="%.2f") for c in ["Model", "Plant Lab", "Model - Plant", "Absolute Error"]})
        except Exception as e:
            st.error(f"Validation error: {e}")

# ----------------------------- PRODUCTIVITY ------------------------------------
def productivity_page():
    page_header("Productivity", "Net sinter and productivity from the optimized recipe, and the fines-contribution breakdown of the current burden.")
    r = st.session_state.result
    if not r or not r.get("blend"):
        st.info("Run the optimizer first."); return

    c1, c2, c3 = st.columns(3)
    with c1:
        new_feed = st.number_input("Feed Rate — charged mix (t/h, dry)", min_value=0.1, value=float(st.session_state.feed_rate_t_h), step=0.5)
    with c2:
        new_area = st.number_input("Strand area (m²) — enter your machine's value", min_value=0.1, value=float(st.session_state.strand_area), step=1.0)
    with c3:
        new_target = st.number_input("Productivity Target (t/m²/h)", min_value=0.01, value=float(st.session_state.productivity_target), step=0.01)
    if (new_feed, new_area, new_target) != (st.session_state.feed_rate_t_h, st.session_state.strand_area, st.session_state.productivity_target):
        st.session_state.feed_rate_t_h, st.session_state.strand_area, st.session_state.productivity_target = new_feed, new_area, new_target

    df = r["df"]; blend = r["blend"]; b_eff = r.get("b_eff", 0.0)
    bfr = opt._bfr_name(df)
    total_burden = sum(float(v) for m, v in blend.items() if m != bfr)
    if not st.session_state.fines_specific_consumption:
        st.session_state.fines_specific_consumption = {m: (blend.get(m, 0.0) / total_burden * 100.0 if total_burden else 0.0) for m in df.index}
    try:
        metrics = _productivity_from(blend, df, b_eff)
        st.session_state.productivity_metrics = metrics
    except Exception as e:
        st.error(str(e)); return

    cols = st.columns(4)
    cols[0].markdown(kpi("PRODUCTIVITY TARGET", f"{metrics['Productivity_Target']:.3f}", "t/m²/h • user-set", "s"), unsafe_allow_html=True)
    kind = "g" if metrics["Target_Achieved"] else "r"
    cols[1].markdown(kpi("PRODUCTIVITY ACHIEVED", f"{metrics['Final_Productivity']:.3f}",
                          f"{metrics['Achievement_Pct']:.1f}% of target — {'MET' if metrics['Target_Achieved'] else 'NOT MET'}", kind), unsafe_allow_html=True)
    cols[2].markdown(kpi("NET SINTER", f"{metrics['Net_Sinter_t_h']:.2f} t/h", f"yield {metrics['Sinter_Yield_Pct']:.1f}% of charged mix", "g"), unsafe_allow_html=True)
    cols[3].markdown(kpi("PRODUCTIVITY LOSS", f"{metrics['Productivity_Loss_Pct']:.2f}%", f"from total fines = {metrics['Total_Fines_Pct']:.2f}%",
                          "r" if metrics["Productivity_Loss_Pct"] > 0 else "g"), unsafe_allow_html=True)
    st.markdown('<div class="notice notice-w">⚠ The strand area default is a placeholder. Productivity is reported per m² of strand; set your machine\'s area, and check the feed-rate basis (charged mix, dry) with the plant.</div>', unsafe_allow_html=True)

    with st.expander("How this is calculated"):
        st.markdown(f"""
- Charged mix = fresh burden + BFR poured = **{metrics['Charged_Mix_kg_t']:,.1f} kg/t sinter**
- Net sinter (t/h) = feed rate × 1000 / charged mix = **{metrics['Net_Sinter_t_h']:.2f} t/h** (a heavier recipe now lowers productivity)
- Base productivity = net sinter / strand area = **{metrics['Base_Productivity']:.3f} t/m²/h**
- Final productivity = base × (1 − fines loss %) = **{metrics['Final_Productivity']:.3f} t/m²/h**
- The fines loss uses the 13–26 % fines table with linear interpolation. Fines % per material comes from the master (currently 0 unless you fill it in).
""")

    st.markdown('<div class="panel"><div class="panel-title">FINES CONTRIBUTION TABLE</div>'
                '<div class="small">Specific consumption % starts from the optimized burden and is editable here as a what-if — it does not change the optimizer result.</div></div>', unsafe_allow_html=True)
    rows = []
    for m in df.index:
        if m == bfr: continue
        rows.append({"Raw Material": m, "% Fines": float(df.loc[m, "Fines_Pct"]),
                     "Specific Consumption %": float(st.session_state.fines_specific_consumption.get(m, 0.0))})
    table = pd.DataFrame(rows)
    edited = st.data_editor(table, hide_index=True, use_container_width=True, key="fines_table_editor",
                             disabled=["Raw Material", "% Fines"],
                             column_config={"% Fines": st.column_config.NumberColumn("% Fines", format="%.2f"),
                                           "Specific Consumption %": st.column_config.NumberColumn("Specific Consumption % in Optimised Burden", min_value=0.0, format="%.2f")})
    if not edited["Specific Consumption %"].equals(table["Specific Consumption %"]):
        for _, row in edited.iterrows():
            st.session_state.fines_specific_consumption[row["Raw Material"]] = float(row["Specific Consumption %"])
        st.rerun()
    edited["Fines × Consumption"] = edited["% Fines"] * edited["Specific Consumption %"] / 100.0
    total_spec = float(edited["Specific Consumption %"].sum()); total_fines = float(edited["Fines × Consumption"].sum())
    display_table = edited.copy()
    display_table.loc[len(display_table)] = {"Raw Material": "TOTAL", "% Fines": np.nan, "Specific Consumption %": total_spec, "Fines × Consumption": total_fines}
    st.table(display_table.round(3))
    if abs(total_spec - 100.0) > 0.5:
        st.warning(f"Specific-consumption total is {total_spec:.2f}%, not 100%.")
    st.markdown(f'<div class="notice"><b>TOTAL WEIGHTED FINES = {total_fines:.3f}%</b></div>', unsafe_allow_html=True)
    if st.button("↩ RESET TO OPTIMISED BURDEN", key="reset_fines"):
        st.session_state.fines_specific_consumption = {m: (blend.get(m, 0.0) / total_burden * 100.0 if total_burden else 0.0) for m in df.index}
        st.rerun()

# ----------------------------- WET SPECIFIC CONSUMPTION ------------------------
def wet_specific_consumption():
    page_header("Wet Specific Consumption", "As-received (wet) burden and cost composition, alongside the dry basis for comparison.")
    r = st.session_state.result
    if not r or not r.get("blend"):
        st.info("Run the optimizer first."); return
    df = r["df"]; blend = r["blend"]

    dry_table, rm_cost_dry, total_dry = opt.compute_dry_cost_table(blend, df, st.session_state.om_cost)
    wet_table, rm_cost_wet, total_wet = opt.compute_wet_cost_table(blend, df, st.session_state.om_cost)

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(kpi("DRY BURDEN", f"{sum(blend.values()):,.2f} kg/t", "Optimizer / chemistry basis", "s"), unsafe_allow_html=True)
    wet_total = float(wet_table.loc["TOTAL", "Wet (As-Received) kg"])
    c2.markdown(kpi("WET BURDEN", f"{wet_total:,.2f} kg/t", "As-received basis", "g"), unsafe_allow_html=True)
    c3.markdown(kpi("DRY TOTAL COST", f"₹{total_dry:,.2f}/t", "RM + O&M", "a"), unsafe_allow_html=True)
    c4.markdown(kpi("WET TOTAL COST", f"₹{total_wet:,.2f}/t", "RM + O&M", "a"), unsafe_allow_html=True)

    a, b = st.columns(2, gap="small")
    with a:
        st.markdown('<div class="panel"><div class="panel-title">DRY BASIS</div></div>', unsafe_allow_html=True)
        st.table(dry_table.round(2))
    with b:
        st.markdown('<div class="panel"><div class="panel-title">WET / AS-RECEIVED BASIS</div></div>', unsafe_allow_html=True)
        st.table(wet_table.round(2))

# ----------------------------- REPORTS ------------------------------------
def reports():
    page_header("Reports & Export", "Export the latest optimized recipe with reconciled burden, cost and inventory usage as an Excel workbook.")
    r = st.session_state.result
    if not r or not r["blend"]:
        st.info("Run optimizer first."); return
    df = r["df"]; blend = r["blend"]; b_eff = r.get("b_eff", 0.0)
    bd = aligned_result_table(blend, df)
    st.table(bd.round(3))

    dry_table, rm_cost_dry, total_dry = opt.compute_dry_cost_table({m: v for m, v in blend.items() if m != opt._bfr_name(df)}, df, st.session_state.om_cost)
    master_view = st.session_state.master_df.reset_index()
    chem_view = pd.DataFrame([opt.compute_achieved(blend, df, 1000, _current_fuel_ash_settings(), bf_nominal=b_eff)])
    cm = opt.charged_mix_summary(blend, df, b_eff)
    rs_view = pd.DataFrame([{"Fresh burden kg/t": cm["Fresh_Burden_kg_t"], "BFR poured kg/t (memo, not in burden, Rs 0)": cm["BFR_Poured_kg_t"],
                             "Charged mix kg/t": cm["Charged_Mix_kg_t"], "IOL % of mix": cm["IOL_Pct_of_Mix"],
                             "BFR % of mix": cm["BFR_Pct_of_Mix"], "Return sinter % of mix": cm["Return_Sinter_Pct_of_Mix"],
                             "Gross sinter kg": cm["Gross_Sinter_kg_t"]}])
    inputs_view = pd.DataFrame([{
        "IOL Fines % of charged mix": st.session_state.rs_iol_pct, "BF Returns % of charged mix (requested)": st.session_state.rs_bfr_pct,
        "BF Returns % used": b_eff * 100, "O&M Cost ₹/t": st.session_state.om_cost, "Production t (horizon)": r.get("prod", st.session_state.production),
        "Horizon days": st.session_state.horizon_days, "Inventory weight": st.session_state.inventory_weight,
        "Stock basis": st.session_state.stock_basis, "Max flux % of burden": st.session_state.max_flux_pct,
        "Float IOL/BFR": st.session_state.float_returns, "Safety margin pp": st.session_state.robust_margin,
    }])
    pr = r.get("premium") or {}
    summary_view = pd.DataFrame([{
        "Run #": st.session_state.runs, "Optimizer Status": r["status"], "RM Cost ₹/t": rm_cost_dry,
        "O&M ₹/t": st.session_state.om_cost, "Total Cost ₹/t": rm_cost_dry + st.session_state.om_cost,
        "Pure-cost recipe RM ₹/t": pr.get("pure_cost", np.nan), "Cost of following stock ₹/t": pr.get("premium", np.nan),
        "Quality": {"spec": "PASS", "tolerance": "WITHIN TOLERANCE", "outside": "OUT OF TOLERANCE"}[quality_level(r["achieved"])],
    }])
    usage = opt.inventory_usage_report(blend, df, r.get("prod", st.session_state.production), st.session_state.horizon_days, st.session_state.stock_basis)
    usage = usage.replace([np.inf, -np.inf], np.nan)

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary_view.to_excel(writer, sheet_name="Summary", index=False)
        bd.to_excel(writer, sheet_name="Dry Burden Cost", index=False)
        rs_view.to_excel(writer, sheet_name="Return Sinter", index=False)
        usage.to_excel(writer, sheet_name="Inventory Usage", index=False)
        master_view.to_excel(writer, sheet_name="Raw Material Master", index=False)
        chem_view.to_excel(writer, sheet_name="Chemistry", index=False)
        inputs_view.to_excel(writer, sheet_name="Inputs", index=False)
        rep_ = r.get("report") or {}
        if rep_.get("goal_table") is not None and len(rep_["goal_table"]):
            rep_["goal_table"].to_excel(writer, sheet_name="Quality Goals", index=False)
        if rep_.get("ore_shares") is not None and len(rep_["ore_shares"]):
            rep_["ore_shares"].replace([np.inf, -np.inf], np.nan).to_excel(writer, sheet_name="Ore Drift", index=False)
        if rep_.get("ratio_options") is not None and len(rep_["ratio_options"]):
            rep_["ratio_options"].to_excel(writer, sheet_name="Ratio Options", index=False)
        if rep_.get("messages"):
            pd.DataFrame({"Message": rep_["messages"]}).to_excel(writer, sheet_name="Messages", index=False)
        pd.DataFrame([{k: v for k, v in (r.get("tolerances") or st.session_state.tolerances).items()}]).to_excel(writer, sheet_name="Tolerances", index=False)
        if st.session_state.whatif is not None:
            st.session_state.whatif.to_excel(writer, sheet_name="Scenario Analysis", index=False)
    st.download_button("⬇ DOWNLOAD OPTIMIZATION REPORT (.xlsx)", buf.getvalue(), "sinter_optimization_report.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

# ----------------------------- UPLOAD & SETTINGS -------------------------------
def settings():
    page_header("Upload & Settings", "Single master workbook management.")
    tpl = Path(__file__).parent / "Master_Chemistry_Input_Template.xlsx"
    if tpl.exists():
        st.download_button("⬇ DOWNLOAD MASTER TEMPLATE (.xlsx)", tpl.read_bytes(), "Master_Chemistry_Input_Template.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    st.caption("Tech Min / Tech Max columns are optional (blank = no limit): they only matter for flux limits (e.g. quicklime floor / cap) and fixed recycle rates. "
               "Optional columns also read: Material_Role, CV_kcal_kg, FC_Pct (Fuel rows) and Fines_% / Fines_Pct.")
    f = st.file_uploader("UPLOAD MASTER CHEMISTRY EXCEL", type=["xlsx"], key="settings_master")
    if f:
        try:
            df = opt.load_master_chemistry_excel({f.name: f.getvalue()})
            df = opt._ensure_material_role(df)
            st.success(f"Validated: {len(df)} materials")
            for w_ in master_data_warnings(df):
                st.warning(w_)
            if st.button("ACTIVATE MASTER", type="primary"):
                st.session_state.master_df = df
                st.session_state.source = f.name
                st.session_state.available = {m: (float(df.loc[m, "Available_Tonnes"]) > 0) for m in df.index}
                st.session_state.result = None; st.session_state.runs = 0
                st.session_state.manual_scenario_result = None
                st.rerun()
        except Exception as e:
            st.error(str(e))
    if st.button("↺ RESTORE BUILT-IN MASTER", use_container_width=True):
        st.session_state.master_df = initial_df()
        st.session_state.source = "Built-in Master Chemistry"
        st.session_state.available = {m: (float(st.session_state.master_df.loc[m, "Available_Tonnes"]) > 0) for m in st.session_state.master_df.index}
        st.session_state.result = None; st.session_state.runs = 0
        st.rerun()
    # edit what was uploaded (availability, price, stock, chemistry, rows), then confirm; shared/editor.py draws it
    try:
        from shared import editor as _editor
    except ImportError:                     # run alone without the combined package: the other pages still edit the table
        _editor = None
    if _editor is not None:
        _editor.sinter_editor(st.session_state, on_change=mark_changed)

def inventory_usage_page():
    page_header("Inventory Usage", "How each material follows the RM stock over the planning horizon — target versus actual share and days of cover.")
    r = st.session_state.result
    if not r or not r.get("blend"):
        st.info("Run the optimizer first."); return
    rep = opt.inventory_usage_report(r["blend"], r["df"], r.get("prod", st.session_state.production),
                                     st.session_state.horizon_days, st.session_state.stock_basis)
    if rep.empty:
        st.info("No materials in use."); return
    used = rep[rep["kg/t"] > 0]
    finite_days = used["Days of cover"].replace([np.inf], np.nan).dropna()
    limited = rep[rep["Note"].eq("limited by chemistry / other rules")]
    a, b, c, d = st.columns(4)
    a.markdown(kpi("PLANNING HORIZON", f"{r.get('prod', st.session_state.production):,.0f} t", f"{st.session_state.horizon_days:g} days", "s"), unsafe_allow_html=True)
    if len(finite_days):
        idx = finite_days.idxmin()
        b.markdown(kpi("SOONEST STOCK-OUT", f"{finite_days.min():,.1f} days", str(used.loc[idx, "Material"]), "a" if finite_days.min() < st.session_state.horizon_days * 2 else "g"), unsafe_allow_html=True)
    c.markdown(kpi("INVENTORY WEIGHT", f"{st.session_state.inventory_weight:.2f}", "0 = pure cost • 1 = follow stock", "s"), unsafe_allow_html=True)
    d.markdown(kpi("CHEMISTRY-LIMITED", f"{len(limited)} material(s)", ", ".join(limited["Material"]) or "none", "a" if len(limited) else "g"), unsafe_allow_html=True)
    pr = r.get("premium")
    if pr:
        st.markdown(f'<div class="notice">📦 Following the stock ratio costs <b>₹{pr["premium"]:+,.0f}/t</b> versus the pure-cost recipe (₹{pr["pure_cost"]:,.0f}/t).</div>', unsafe_allow_html=True)
    show = rep.replace([np.inf, -np.inf], np.nan)
    cols = ["Material", "Group", "Stock t", "kg/t", "Tonnes used", "% of stock used", "Days of cover", "Ratio group",
            "Target share %", "Actual share %", "Deviation pp", "Note"]
    st.dataframe(show[cols], hide_index=True, use_container_width=True, height=max(300, 36 * len(show) + 45),
                 column_config={"Stock t": st.column_config.NumberColumn(format="%.0f"), "kg/t": st.column_config.NumberColumn(format="%.1f"),
                                "Tonnes used": st.column_config.NumberColumn(format="%.0f"),
                                "% of stock used": st.column_config.NumberColumn(format="%.1f"),
                                "Days of cover": st.column_config.NumberColumn(format="%.1f"),
                                "Target share %": st.column_config.NumberColumn(format="%.1f"),
                                "Actual share %": st.column_config.NumberColumn(format="%.1f"),
                                "Deviation pp": st.column_config.NumberColumn(format="%+.1f")})
    rep_ = r.get("report") or {}
    sh = rep_.get("ore_shares")
    if sh is not None and len(sh):
        st.markdown('<div class="panel"><div class="panel-title">ORE SHARE DRIFT vs STOCK SHARE</div>'
                    '<div class="small">Recipe share of the (non-Mill-Scale) ore against each ore\'s share of the ore stock, with days of cover at this recipe.</div></div>', unsafe_allow_html=True)
        st.dataframe(sh.replace([np.inf, -np.inf], np.nan), hide_index=True, use_container_width=True, height=min(420, 36 * len(sh) + 45),
                     column_config={"Stock share %": st.column_config.NumberColumn(format="%.1f"), "Recipe share %": st.column_config.NumberColumn(format="%.1f"),
                                    "Drift pp": st.column_config.NumberColumn(format="%+.1f"), "kg/t": st.column_config.NumberColumn(format="%.1f"),
                                    "Days of cover": st.column_config.NumberColumn(format="%.1f")})
    ro = rep_.get("ratio_options")
    if ro is not None and len(ro):
        st.markdown('<div class="panel"><div class="panel-title">STOCK-RATIO OPTIONS — A: QUALITY FIRST  |  B: HOLD EVERY ORE WITHIN THE BAND</div>'
                    '<div class="small">Shown when an ore drifts more than 5 points from its stock share. Option B is only comparable when its status is Optimal or Relaxed.</div></div>', unsafe_allow_html=True)
        st.dataframe(ro, hide_index=True, use_container_width=True,
                     column_config={"Cost Rs/t": st.column_config.NumberColumn(format="%.0f"), "SiO2 %": st.column_config.NumberColumn(format="%.2f"),
                                    "Fe %": st.column_config.NumberColumn(format="%.2f"), "Basicity": st.column_config.NumberColumn(format="%.3f"),
                                    "Max ore drift pp": st.column_config.NumberColumn(format="%.1f")})
    st.caption("Target share = the material's share of the group's dry stock (coke: stock × quality index). Actual share can differ where quality limits force it — "
               "for example a high-SiO₂ ore is used less than its stock share. Days of cover = stock ÷ daily use over the planning horizon. "
               "Stock is not reduced by deliveries in transit.")


# ----------------------------- ROUTING -----------------------------------
pages = {
    "Dashboard": dashboard,
    "Inputs": inputs_page,
    "RM Stock & Materials": rm_stock_materials,
    "Inventory Usage": inventory_usage_page,
    "Recipe & Composition": recipe_composition,
    "Manual Burden Control": manual,
    "Scenario Analysis": scenario_analysis,
    "Plant Run Validation": plant_run_validation,
    "Productivity": productivity_page,
    "Wet Specific Consumption": wet_specific_consumption,
    "Reports": reports,
    "Upload & Settings": settings,
}
pages[st.session_state.nav]()
st.markdown('<div class="footer">Sinter Burden Control • Hospet Alloy Steel Plant • Production decision-support interface</div>', unsafe_allow_html=True)
