"""
MBF mini blast furnace burden cost optimiser - ENGINE (backend)
================================================================
Basis: 1 tonne of hot metal (1 tHM), dry and net kg.  Least-cost burden by linear programming (PuLP; HiGHS, CBC fallback).

This module is the notebook **v11.7** model (ideal plant conditions, plant-reviewed thumb rules, Sep-26, hot-zone heat
balance and heat audit) with the
Colab / ipywidgets front end removed, wrapped in the dashboard layer:

  * Every editable setting lives in a `Config` object.  The dashboard keeps one Config per user session.
  * `session(cfg)` loads a Config into the engine for the duration of one call and restores the defaults afterwards,
    behind a lock, so two users' settings can never mix.
  * The functions the dashboard calls (`solve`, `make_bundle`, `sweep`, `sensitivity`, `price_sens`, `tornado_run`,
    `breakeven`, `curve`, `export_bytes`, `load_master`, `template_bytes`, ...) do this for you.  The notebook-style
    functions (`solve_mbf`, `sinter_sweep`, `sinter_curve`, ...) are still here and can be used inside `with session(cfg):`.

What the v11.7 engine is (full history in the notebook header):
  * Sinter : ore is a FREE choice inside 50-80 % guard rails, solved as an exact LP at every sinter share; every run
    says what set the share (cost balance, a chemistry limit, or a guard rail).  Pins allowed from 0 % to 100 %.
  * Fuel rule = base 545 (both furnaces) + sinter share (1 kg per point vs 65 %) + ore Fe + sinter Fe + slag
    (0.18 kg/kg over 330) + raw flux (0.30 kg/kg) + ore / coke moisture + sinter/flux/minor moisture (extension).
    Each rule can be switched off (Config.fuel_terms).  Removed: PCI rate / FC / moisture, coke ash, CSR, DRI,
    hot blast, furnace offset, burden-Fe form, and the ore-grade cost model.
  * Slag: B2 0.99-1.01, MgO 7-8 %, Al2O3 17-18.5 %; MgO/Al2O3 reported against 0.40-0.55.  Ks fixed at 25.
  * New outputs: sinter decision, slag oxide sources, 0-100 % sinter curve, price sensitivity, tornado, sinter break-even.
  * v11.6: hot-zone HEAT BALANCE below the thermal reserve zone. Mode "check" (default) reports carbon needed vs charged
    and the heat-balance minimum fuel and leaves the LP unchanged; mode "floor" also makes the LP meet it.
  * v11.7: HEAT AUDIT on real plant months (carbon / nitrogen balances, DRR two ways, hot-zone cooling losses,
    measured heat residual, DRR vs sinter fit) and an explicit apply step.  The heat settings live in Config.heat,
    so a calibration in one browser session never reaches another.

Materials-chemistry defaults (DEFAULT_ROWS) are DEMO ONLY.  Real runs use an uploaded master Excel file.
"""

import re, io, math, copy, threading, contextlib, warnings
import datetime as _dt
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pulp

ENGINE_VERSION = "14.0 (notebook v11.7 engine)"

# PuLP 3.x announces API changes that arrive in PuLP 4.  requirements.txt pins PuLP below 4, so these notices are
# noise.  Remove this filter when moving to PuLP 4.
warnings.filterwarnings("ignore", message=r".*(LpVariable|PULP_CBC_CMD|HiGHS|_v4_deprecation|PuLP 4).*", category=DeprecationWarning)


def _make_solver():
    try:
        s = pulp.HiGHS(msg=False)
        if s.available():
            return s
    except Exception:
        pass
    return pulp.PULP_CBC_CMD(msg=0)


SOLVER = _make_solver()


# ================================================================
# 1. PLANT / MODEL SETTINGS  (all quantities: kg per tHM, dry & net)
# ================================================================
HM_BASIS_KG = 1000.0

# --- Hot-metal assay targets (overwrite with validated plant values)
HM_FE_PCT = 94.0          # Fe% in hot metal (used only for the stoichiometric carbon floor)
HM_SI_PCT = 0.6           # Si% in hot metal
HM_C_PCT = 4.3            # dissolved C%

# --- Fe CLOSURE: one value, matched exactly ------------------------
FE_REQUIRED_PER_100KG_HM = 96.5                       # kg Fe charged per 100 kg hot metal
FE_CLOSURE_BASIS = "burden"                           # "burden" = ore+sinter+minor | "all" = every charged material
REQUIRED_FE_KGTHM = HM_BASIS_KG * FE_REQUIRED_PER_100KG_HM / 100.0   # 965 kg/tHM
HM_FE_KGTHM = HM_BASIS_KG * HM_FE_PCT / 100.0

# --- Stoichiometric constants
SIO2_TO_SI_CONST = 0.4674
C_PER_KG_FE_DIRECT = 0.2148
C_PER_KG_SI = 0.8544
C_PER_KG_MN = 0.2184
MN_REDUCTION_EFF = 0.85
DIRECT_REDUCTION_DEGREE = 0.40

# --- Operating policy
SINTER_MIN = 0.50                # GUARD RAILS for the free sinter:ore choice (not a decision band)
SINTER_MAX = 0.80
PCI_FIXED_KGTHM = 120.0
NUT_COKE_KGTHM = 40.0
NUT_COKE_MODE = "fixed"          # "fixed" = exactly NUT_COKE_KGTHM when a nut coke is ON | "cap" = at most

BASICITY_MIN, BASICITY_MAX = 0.99, 1.01    # B2 = 1.00 +/- 0.01 (plant review, Sep-26)
MGO_MIN_PCT, MGO_MAX_PCT = 7.0, 8.0
AL2O3_MIN_PCT, AL2O3_MAX_PCT = 17.0, 18.5
MGO_AL2O3_GUIDE = (0.40, 0.55)   # MgO/Al2O3 guide for slag drainage - DIAGNOSTIC ONLY

# --- FUEL-RATE RULE SET (fixed by design; edit coefficients to recalibrate) ----------------
FURNACE_PROFILES = {"MBF-2": {"base": 545.0}, "MBF-3": {"base": 545.0}}   # one baseline for both furnaces (plant review)
FURNACE = {"name": "MBF-3"}
SLAG_REF_KGTHM = 330.0
FUEL_PER_KG_SLAG = 0.18          # kg fuel per kg slag above reference
SINTER_REF_PCT = 65.0
FUEL_PER_PCT_SINTER = 1.0        # PLANT THUMB RULE: +10 pts sinter in (sinter + ore) = -10 kg coke, and vice versa
RAW_FLUX_REF_KGTHM = 2.0
FUEL_PER_KG_RAW_FLUX = 0.30      # kg fuel per kg limestone/dolomite charged to the furnace (literature 0.20-0.35)
RAW_FLUX_MIN_CAO_MGO = 15.0      # a Flux material counts as raw (carbonate) flux if CaO+MgO >= this %

# "per point" rules: coef kg fuel per 1 point of the assay at the reference group rate -> per-kg form coef / rate.
REF_RATES = {"ore": 600.0, "sinter": 1115.0, "coke": 402.0}
MATERIAL_RULES = {
    "ore_fe":         dict(pools=["Iron_ore"], attr="Fe", coef=3.0, rate="ore", ref=61.5, sign=-1),
    "sinter_fe":      dict(pools=["Sinter"], attr="Fe", coef=3.0, rate="sinter", ref=53.5, sign=-1),
    "ore_moisture":   dict(pools=["Iron_ore"], attr="Moisture_Pct", coef=2.0, rate="ore", ref=3.0, sign=+1),
    "coke_moisture":  dict(pools=["Fuel_Coke", "Fuel_NutCoke"], attr="Moisture_Pct", coef=3.0, rate="coke", ref=5.0, sign=+1),
    "other_moisture": dict(pools=["Sinter", "Flux", "Minor"], attr="Moisture_Pct", coef=2.0, rate="ore", ref=0.0, sign=+1),
}
EXTENSION_TERMS = ("other_moisture",)     # not a plant thumb rule
FUEL_TERM_NAMES = ["slag", "sinter_share", "raw_flux"] + list(MATERIAL_RULES)
FUEL_TERMS = {k: True for k in FUEL_TERM_NAMES}   # internal switches (used by tests / analysis only; not a user option)
FUEL_TERMS_DEFAULT = dict(FUEL_TERMS)
FUEL_TERM_GROUPS = {"slag": ["slag"], "sinter share": ["sinter_share"], "Fe rules": ["ore_fe", "sinter_fe"],
                    "moisture rules": ["ore_moisture", "coke_moisture"], "sinter/flux moisture": ["other_moisture"],
                    "raw flux": ["raw_flux"]}
PRICE_BASIS = "dry"              # "dry" = Price_Rs_t per DRY tonne | "wet" = per wet tonne
SHARE_GRID_STEP = 1.0            # coarse grid over the guard rails (points of sinter share)
SHARE_REFINE_STEP = 0.1          # fine refinement around the best coarse point

SINTER_MANUAL = {"on": False, "pct": 70.0}

# --- Fe/C : DIAGNOSTIC ONLY
FE_C_RATIO_TARGET = 2.0
FE_C_RATIO_TOL = 0.1

# --- Minor materials (BHQ / Mn ore / sponge iron)
MINOR_MAX_KGTHM = 100.0
MINOR_DEMAND_ONLY = True
MINOR_PENALTY_RS_PER_KG = 1000.0  # internal ranking weight; NOT part of the reported cost

MIN_SHARE_MULTI_SOURCE = 0.0

# --- RM STOCK
STOCK = {"plan_hm_tonnes": 0.0, "balance": 1.0}
BALANCE_GROUPS = ["Iron_ore", "Sinter", "Fuel_Coke", "Fuel_NutCoke", "Fuel_PCI"]

BIG_M_KGTHM = 5000.0
RAW_FLUX_NOTE_KGTHM = 20.0       # results flag raw flux above this
SLAG_NOTE_MARGIN_KG = 20.0       # results flag slag this far above the reference

GROUPS = ["Iron_ore", "Sinter", "Minor", "Flux", "Fuel_Coke", "Fuel_NutCoke", "Fuel_PCI"]
FUEL_GROUPS = ["Fuel_Coke", "Fuel_NutCoke", "Fuel_PCI"]
MIN_SHARE_GROUPS = ["Iron_ore", "Sinter", "Fuel_Coke"]
MINOR_NAME_HINTS = ("bhq", "mnore", "sponge", "dri")
COLUMNS_BASE = ["Material", "Group", "Fe", "CaO", "MgO", "SiO2", "Al2O3", "Mn", "S", "FC",
                "Moisture_Pct", "Price_Rs_t", "Available"]
COLUMNS = ["Material", "Group", "Fe", "CaO", "MgO", "SiO2", "Al2O3", "Mn", "S", "FC",
           "Moisture_Pct", "Price_Rs_t", "RM_Stock", "Fines_Pct", "Fines_Credit_Rs_t", "Available"]
OPT_COLS = ["RM_Stock", "Fines_Pct", "Fines_Credit_Rs_t"]
NUM_COLS = ["Fe", "CaO", "MgO", "SiO2", "Al2O3", "Mn", "S", "FC", "Moisture_Pct", "Price_Rs_t"]
OXIDES = ["CaO", "SiO2", "Al2O3", "MgO"]


# ================================================================
# 2. DEMO / DEVELOPMENT DATA ONLY
# ================================================================
DEFAULT_ROWS = [
    # Material,   Group,         Fe,      CaO,     MgO,    SiO2,   Al2O3, Mn,     S,      FC,   Moist, Price, Available
    ("Ore1",      "Iron_ore",    62.0,    0.0,     0.0,    4.0,    3.5,   0.02,   0.07,   0.0,  3.0,   6300,  True),
    ("Ore2",      "Iron_ore",    0.0,     0.0,     0.0,    0.0,    0.0,   0.0,    0.0,    0.0,  0.0,   6300,  False),
    ("Sinter1",   "Sinter",      52.8517, 10.4765, 2.3605, 5.1732, 2.8661, 0.3137, 0.0492, 0.0, 0.0,   5500,  True),
    ("Sinter2",   "Sinter",      0.0,     0.0,     0.0,    0.0,    0.0,   0.0,    0.0,    0.0,  0.0,   5500,  False),
    ("BHQ",       "Minor",       0.0,     0.0,     0.0,    0.0,    0.0,   0.0,    0.0,    0.0,  5.7,   500,   False),
    ("Mn_Ore",    "Minor",       62.0,    0.0,     0.0,    3.5,    3.5,   0.15,   0.0,    0.0,  1.5,   4000,  False),
    ("Sponge_Iron", "Minor",     0.0,     0.0,     0.0,    0.0,    0.0,   0.0,    0.0,    0.0,  0.0,   0,     False),
    ("Limestone", "Flux",        0.0,     46.0,    1.8,    5.9,    0.86,  0.0,    0.35,   0.0,  3.5,   800,   True),
    ("Dolomite",  "Flux",        2.0,     26.0,    19.8,   2.4,    0.9,   0.0,    0.39,   0.0,  3.5,   800,   True),
    ("Quartzite", "Flux",        0.6,     0.0,     0.0,    97.0,   1.0,   0.0,    0.08,   0.0,  3.0,   500,   True),
    ("Coke1",     "Fuel_Coke",   0.0,     0.0,     0.0,    0.0,    0.0,   0.0,    0.0,    0.0,  4.73,  20000, False),
    ("Coke2",     "Fuel_Coke",   0.0,     0.0,     0.0,    0.0,    0.0,   0.0,    0.0,    0.0,  4.6,   20000, False),
    ("Coke3",     "Fuel_Coke",   0.72,    0.25,    0.09,   6.05,   3.73,  0.0,    0.47,   86.5, 5.5,   20000, True),
    ("Nut_Coke",  "Fuel_NutCoke", 0.0,    0.0,     0.0,    0.0,    0.0,   0.0,    0.0,    0.0,  2.0,   20000, False),
    ("PCI",       "Fuel_PCI",    2.2,     0.416,   0.24,   9.304,  5.0,   0.0,    0.5,    75.0, 1.4,   11000, True),
]


def make_df(rows=DEFAULT_ROWS):
    df = pd.DataFrame(rows, columns=COLUMNS_BASE).set_index("Material")
    df[NUM_COLS] = df[NUM_COLS].astype(float)
    for c in OPT_COLS:
        df.insert(list(df.columns).index("Available"), c, np.nan)
    df["Available"] = df["Available"].astype(bool)
    return df[COLUMNS[1:]]


plant_df = make_df()


def demo_df():
    """A fresh copy of the built-in DEMO material table (placeholder prices)."""
    return make_df()


LEGACY_COLS = ("Ash_Pct", "CSR")         # columns of the pre-v11.1 material table; no longer used


def ensure_columns(df):
    """Older tables still work: missing optional columns are added blank; retired Ash / CSR columns are dropped."""
    drop = [c for c in LEGACY_COLS if c in df.columns]
    missing = [c for c in OPT_COLS if c not in df.columns]
    if not drop and not missing:
        return df
    df = df.drop(columns=drop).copy()
    for c in missing:
        df[c] = np.nan
    return df[COLUMNS[1:]]


# ================================================================
# 3. HELPERS
# ================================================================
def sanitize_name(x):
    s = re.sub(r"\s+", "_", str(x).strip())
    s = re.sub(r"[^A-Za-z0-9_\-]", "", s)
    return s or "Material"


def mats(df, group):
    return [m for m in df.index if str(df.loc[m, "Group"]).strip() == group]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def get_bounds(df, use_stock_caps=True):
    """ON/OFF availability plus stock. ON -> (0, BIG_M). OFF -> (0, 0). RM_Stock == 0 -> unavailable."""
    plan = float(STOCK["plan_hm_tonnes"])
    bounds = {}
    for m in df.index:
        ub = BIG_M_KGTHM if bool(df.loc[m, "Available"]) else 0.0
        st = df.loc[m, "RM_Stock"] if "RM_Stock" in df.columns else np.nan
        if ub > 0 and pd.notna(st):
            if float(st) <= 0:
                ub = 0.0
            elif use_stock_caps and plan > 0:
                ub = min(ub, float(st) * 1000.0 / plan)
        bounds[m] = (0.0, ub)
    return bounds


def balance_groups(df, bounds):
    out = {}
    for g in BALANCE_GROUPS:
        av = [m for m in mats(df, g) if bounds[m][1] > 0]
        if len(av) >= 2 and all(pd.notna(df.loc[m, "RM_Stock"]) for m in av):
            tot = sum(float(df.loc[m, "RM_Stock"]) for m in av)
            out[g] = {m: float(df.loc[m, "RM_Stock"]) / tot for m in av}
    return out


def validate_df(df):
    errs = []
    if df.index.has_duplicates:
        errs.append(f"Duplicate material names: {sorted(set(df.index[df.index.duplicated()]))}")
    for m in df.index:
        g = str(df.loc[m, "Group"]).strip()
        if g not in GROUPS:
            errs.append(f"{m}: invalid Group '{g}' (must be one of {GROUPS})")
            continue
        for c in NUM_COLS:
            v = df.loc[m, c]
            if pd.isna(v) or float(v) < 0:
                errs.append(f"{m}: {c} must be a number >= 0")
        for c in OPT_COLS:
            v = df.loc[m, c]
            if pd.notna(v) and float(v) < 0:
                errs.append(f"{m}: {c} must be blank or >= 0")
        v = df.loc[m, "Fines_Pct"] if "Fines_Pct" in df.columns else np.nan
        if pd.notna(v) and float(v) >= 100:
            errs.append(f"{m}: Fines_Pct must be below 100")
        mo = df.loc[m, "Moisture_Pct"]
        if pd.notna(mo) and float(mo) >= 60:
            errs.append(f"{m}: Moisture_Pct must be below 60")
        try:
            fe, oxides = float(df.loc[m, "Fe"]), sum(float(df.loc[m, c]) for c in OXIDES)
            if fe > 100 or oxides > 100:
                errs.append(f"{m}: assay is impossible (Fe {fe:.1f}%, CaO+MgO+SiO2+Al2O3 {oxides:.1f}%)")
        except Exception:
            pass
        if bool(df.loc[m, "Available"]):
            assay = sum(float(df.loc[m, c]) for c in ["Fe", "CaO", "MgO", "SiO2", "Al2O3", "FC"])
            if assay <= 0:
                errs.append(f"{m}: toggled ON but has no assay (all Fe/CaO/MgO/SiO2/Al2O3/FC are zero)")
            if float(df.loc[m, "Price_Rs_t"]) <= 0:
                errs.append(f"{m}: toggled ON but has no price (Price_Rs_t must be > 0)")
            if g in FUEL_GROUPS and float(df.loc[m, "FC"]) <= 0:
                errs.append(f"{m}: FC% is required (> 0) for fuel group '{g}' when toggled ON")
            if g in ("Iron_ore", "Sinter") and float(df.loc[m, "Fe"]) <= 0:
                errs.append(f"{m}: Fe% is required (> 0) for group '{g}' when toggled ON")
    return errs


def chem_expr(x, df, col, groups=None):
    ms = df.index if groups is None else [m for g in groups for m in mats(df, g)]
    return pulp.lpSum(x[m] * float(df.loc[m, col]) / 100.0 for m in ms)


# ================================================================
# 4. SULPHUR PARTITION (fixed Ks, plant review)
# ================================================================
KS_FIXED = 25.0                  # (%S in slag)/(%S in metal); S in HM = S charged / (1 + Ks x slag / HM)
OM_RS_THM = 0.0                  # furnace operations & maintenance cost, Rs per tonne of hot metal (added to the raw-material cost; never changes the burden)


# ================================================================
# 5. FUEL RULE + POST-SOLVE CHEMISTRY
# ================================================================
def eff_price(df, m):
    """Rs per DRY tonne (fines credit applied if entered)."""
    p = float(df.loc[m, "Price_Rs_t"])
    if PRICE_BASIS == "wet":
        p = p / (1.0 - float(df.loc[m, "Moisture_Pct"]) / 100.0)
    f = df.loc[m, "Fines_Pct"] if "Fines_Pct" in df.columns else np.nan
    if pd.notna(f) and float(f) > 0:
        cr = df.loc[m, "Fines_Credit_Rs_t"]
        f = float(f) / 100.0
        p = (p - f * (0.0 if pd.isna(cr) else float(cr))) / (1.0 - f)
    return p


def term_active(name):
    return bool(FUEL_TERMS.get(name, True))


def rules_off():
    """Plain names of the fuel rules currently switched off."""
    lab = {"slag": "Slag", "sinter_share": "Sinter share", "raw_flux": "Raw flux", "ore_fe": "Ore Fe", "sinter_fe": "Sinter Fe",
           "ore_moisture": "Ore moisture", "coke_moisture": "Coke moisture", "other_moisture": "Sinter/flux/minor moisture"}
    return [lab.get(k, k) for k in FUEL_TERM_NAMES if not FUEL_TERMS.get(k, True)]


def rule_weights(df, name):
    r = MATERIAL_RULES[name]
    per_kg = r["coef"] / REF_RATES[r["rate"]]
    w = {}
    for m in df.index:
        if str(df.loc[m, "Group"]).strip() in r["pools"]:
            a = df.loc[m, r["attr"]]
            if pd.notna(a):
                w[m] = per_kg * r["sign"] * (float(a) - r["ref"])
    return w


def is_raw_flux(df, m):
    return (str(df.loc[m, "Group"]).strip() == "Flux"
            and float(df.loc[m, "CaO"]) + float(df.loc[m, "MgO"]) >= RAW_FLUX_MIN_CAO_MGO)


def fuel_terms(X, df, slag, sinter_pct):
    """Every term of the fuel-rate rule (kg/tHM). Works with numbers OR LP expressions (one formula for LP and report)."""
    t = {"base": FURNACE_PROFILES[FURNACE["name"]]["base"]}
    t["slag"] = FUEL_PER_KG_SLAG * (slag - SLAG_REF_KGTHM)
    t["sinter_share"] = FUEL_PER_PCT_SINTER * (SINTER_REF_PCT - sinter_pct)
    t["raw_flux"] = FUEL_PER_KG_RAW_FLUX * (sum(X[m] for m in df.index if is_raw_flux(df, m)) - RAW_FLUX_REF_KGTHM)
    for name in MATERIAL_RULES:
        t[name] = sum(w * X[m] for m, w in rule_weights(df, name).items())
    for k in list(t):
        if k != "base" and not term_active(k):
            t[k] = 0.0
    return t


def fuel_term_groups(t):
    g = lambda *ks: sum(float(t[k]) for k in ks)
    return {"slag": g("slag"), "fe": g("ore_fe", "sinter_fe"), "sinter": g("sinter_share"),
            "raw_flux": g("raw_flux"), "moisture": g("ore_moisture", "coke_moisture"), "moisture_ext": g("other_moisture")}


def _calc_achieved_core(blend, df):
    def s(col, groups=None):
        ms = df.index if groups is None else [m for g in groups for m in mats(df, g)]
        return sum(float(blend.get(m, 0.0)) * float(df.loc[m, col]) / 100.0 for m in ms)

    def tot(g):
        return sum(float(blend.get(m, 0.0)) for m in mats(df, g))

    Fe, CaO, MgO, SiO2_ch, Al2O3 = s("Fe"), s("CaO"), s("MgO"), s("SiO2"), s("Al2O3")
    Mn_ch, FC, S_total = s("Mn"), s("FC"), s("S")
    S_fuel = s("S", FUEL_GROUPS)
    si_in_hm = HM_BASIS_KG * HM_SI_PCT / 100.0
    sio2_red = si_in_hm / SIO2_TO_SI_CONST
    slag_SiO2 = max(SiO2_ch - sio2_red, 1e-9)
    slag = CaO + MgO + slag_SiO2 + Al2O3
    B2 = CaO / slag_SiO2
    MgO_pct, Al2O3_pct = 100 * MgO / slag, 100 * Al2O3 / slag
    CaO_pct, SiO2_pct = 100 * CaO / slag, 100 * slag_SiO2 / slag
    B4 = (CaO + MgO) / (slag_SiO2 + Al2O3)
    ore, sinter, minor = tot("Iron_ore"), tot("Sinter"), tot("Minor")
    coke, nut, pci = tot("Fuel_Coke"), tot("Fuel_NutCoke"), tot("Fuel_PCI")
    sinter_pct = 100 * sinter / (ore + sinter) if (ore + sinter) > 0 else np.nan
    burden_mass = ore + sinter
    fe_burden = s("Fe", ["Iron_ore", "Sinter"])
    burden_fe_pct = 100 * fe_burden / burden_mass if burden_mass > 0 else np.nan
    fuel_supplied = coke + nut + pci
    X = {m: float(blend.get(m, 0.0)) for m in df.index}
    terms = fuel_terms(X, df, slag, sinter_pct)
    fuel_rule = sum(terms.values())
    terms["total"] = fuel_rule
    raw_cost = sum(X[m] * eff_price(df, m) / 1000 for m in df.index)
    cost = raw_cost + float(OM_RS_THM)          # O&M is a fixed Rs/tHM on top of the raw materials
    fc_floor = (DIRECT_REDUCTION_DEGREE * HM_FE_KGTHM * C_PER_KG_FE_DIRECT + si_in_hm * C_PER_KG_SI
                + MN_REDUCTION_EFF * Mn_ch * C_PER_KG_MN + HM_BASIS_KG * HM_C_PCT / 100.0)
    s_hm_kg = S_total / (1.0 + KS_FIXED * slag / HM_BASIS_KG)     # all charged S, fixed Ks
    raw_flux = sum(X[m] for m in df.index if is_raw_flux(df, m))
    flux_all = tot("Flux")
    return {
        "Fe_kg": Fe, "Fe_burden_kg": s("Fe", ["Iron_ore", "Sinter", "Minor"]), "CaO_kg": CaO, "MgO_kg": MgO,
        "SiO2_charged_kg": SiO2_ch, "SiO2_reduced_kg": sio2_red, "slag_SiO2_kg": slag_SiO2,
        "Al2O3_kg": Al2O3, "Mn_kg": Mn_ch, "S_kg": S_total, "S_fuel_kg": S_fuel, "FC_kg": FC,
        "slag_kg": slag, "B2": B2, "B4": B4, "MgO_pct": MgO_pct, "Al2O3_pct": Al2O3_pct,
        "CaO_pct": CaO_pct, "SiO2_slag_pct": SiO2_pct, "MgO_Al2O3": MgO / Al2O3 if Al2O3 > 1e-9 else np.nan,
        "Slag_min_for_Al2O3_cap": Al2O3 / (AL2O3_MAX_PCT / 100.0),
        "Ore_kg": ore, "Sinter_kg": sinter, "Minor_kg": minor, "Coke_kg": coke, "NutCoke_kg": nut, "PCI_kg": pci,
        "Flux_kg": flux_all, "Raw_flux_kg": raw_flux,
        "Sinter_share_pct": sinter_pct, "Burden_Fe_pct": burden_fe_pct,
        "Fuel_supplied": fuel_supplied, "Fuel_rule": fuel_rule, "Fuel_terms": terms,
        "Fe_C_ratio": Fe / FC if FC > 1e-9 else np.nan, "FC_floor": fc_floor,
        "Total_burden_kg": sum(float(v) for v in blend.values()), "Cost_Rs_tHM": cost,
        "Raw_cost_Rs_tHM": raw_cost, "OM_Rs_tHM": float(OM_RS_THM),
        "Ks": KS_FIXED, "S_HM_pct": s_hm_kg / 10.0,
    }


def calc_achieved(blend, df):
    """Post-solve chemistry, fuel and cost of a burden, plus the hot-zone heat check (v11.6+)."""
    a = _calc_achieved_core(blend, df)
    a["Heat"] = heat_check(blend, df, a)
    return a


# ================================================================
# 5b. HEAT BALANCE - HOT-ZONE (LOWER FURNACE) CHECK   [notebook v11.6, carried in v11.7]
# ================================================================
# The furnace is split at the thermal reserve zone (TRZ). Below it, the heat delivered by carbon burnt to CO at
# the tuyeres, together with the hot blast, must cover: direct reduction of FeO, Si and Mn reduction, carbon
# dissolving in the metal, the late share of raw-flux calcination (and its CO2 reacting with coke), heating and
# melting the metal and slag from the TRZ to tapping, and the heat lost from the lower furnace.
# Accounting rules (so nothing is counted twice):
#   * reference state 25 C, one unit (kJ), every stream counted once;
#   * carbon enters only as C -> CO at the tuyeres and inside the net reactions (FeO + C -> Fe + CO etc.);
#     fuel is never entered at its calorific value and top-gas chemical energy never appears;
#   * gas leaves the hot zone at the TRZ temperature; solids enter it at the TRZ temperature.
# With the operating parameters fixed, every term is linear in the burden masses, so the same formula feeds the
# report (numbers) and, in "floor" mode, the LP (expressions). The sinter share is pinned in each LP slice, so a
# degree of direct reduction that depends on sinter share keeps the problem linear.
# EVERY VALUE BELOW IS A LITERATURE PLACEHOLDER until it is replaced or calibrated with plant data.
# Dashboard: these values live in Config.heat and are loaded per session (never shared between users).
HEAT = {
    "mode": "check",               # "check" = report only (default) | "floor" = LP also needs fixed carbon >= heat need
    "blast_temp_C": 1000.0,        # hot-blast temperature at the tuyeres                     [placeholder]
    "blast_humidity_g_Nm3": 15.0,  # moisture + steam per Nm3 of dry blast                     [placeholder]
    "blast_O2_pct": 21.0,          # O2 in the dry blast, % (21 = no enrichment)               [placeholder]
    "trz_temp_C": 1000.0,          # thermal reserve zone: gas leaves, solids enter the hot zone [placeholder]
    "drr": DIRECT_REDUCTION_DEGREE,  # degree of direct reduction at the reference sinter share  [calibrate]
    "drr_per_pt_sinter": 0.0,      # change in DRR per +1 pt sinter share vs SINTER_REF_PCT    [0 = not modelled]
    "calc_hot_frac": 0.5,          # share of raw-flux carbonate that decomposes in the hot zone [placeholder]
    "sol_loss_frac": 1.0,          # share of that CO2 that reacts with coke (CO2 + C -> 2CO)  [placeholder]
    "h_hm_kJ_kg": 1300.0,          # enthalpy of tapped hot metal vs 25 C                      [placeholder]
    "h_fe_trz_kJ_kg": 600.0,       # enthalpy of iron at the TRZ vs 25 C                       [placeholder]
    "h_slag_kJ_kg": 1750.0,        # enthalpy of tapped slag vs 25 C                           [placeholder]
    "h_gangue_trz_kJ_kg": 900.0,   # enthalpy of gangue/flux oxides at the TRZ vs 25 C         [placeholder]
    "loss_MJ_tHM": 500.0,          # lower-furnace heat losses (cooling water, shell)           [CALIBRATE]
    "calibrated": False,
    "calibration_note": "not calibrated - absolute surplus/deficit is indicative only",
}
HEAT_DEFAULT = copy.deepcopy(HEAT)
HEAT_NUMERIC_KEYS = [k for k, v in HEAT_DEFAULT.items() if isinstance(v, float)]

# H(T) - H(298.15 K), kJ/mol. Approximate JANAF-table values: VERIFY against the tables before calibration.
_HT_K = [298.15, 1000.0, 1200.0, 1400.0, 1600.0]
_HT = {
    "N2":  [0.0, 21.463, 28.108, 34.936, 41.903],
    "O2":  [0.0, 22.707, 29.765, 36.966, 44.267],
    "CO":  [0.0, 21.690, 28.440, 35.338, 42.322],
    "CO2": [0.0, 33.397, 44.473, 55.896, 67.569],
    "H2":  [0.0, 20.680, 26.797, 33.183, 39.844],
    "H2O": [0.0, 26.000, 34.506, 43.447, 52.767],
    "C":   [0.0, 11.795, 16.325, 21.075, 26.006],
}
# Reaction enthalpies at 25 C, kJ/mol, from standard enthalpies of formation (VERIFY against one source).
HEAT_DH = {
    "C_to_CO": 110.5,       # C + 1/2 O2 -> CO                (released)
    "FeO_C": 161.5,         # FeO + C -> Fe + CO               (absorbed)
    "SiO2_2C": 689.7,       # SiO2 + 2C -> Si + 2CO            (absorbed)
    "MnO_C": 274.7,         # MnO + C -> Mn + CO               (absorbed)
    "H2O_C": 131.3,         # H2O(g) + C -> CO + H2            (absorbed)
    "CaCO3": 179.2,         # CaCO3 -> CaO + CO2               (absorbed)
    "MgCO3": 100.7,         # MgCO3 -> MgO + CO2               (absorbed)
    "boudouard": 172.5,     # CO2 + C -> 2CO                   (absorbed)
    "Si_solution": -120.0,  # Si -> [Si] in iron  (literature spread roughly -80 to -130; VERIFY)
    "C_solution": 22.6,     # C(graphite) -> [C] in iron
}
_MW = {"C": 12.011, "Fe": 55.845, "Si": 28.086, "Mn": 54.938, "CaO": 56.077, "MgO": 40.304}
HEAT_TERM_LABELS = {
    "direct_reduction": "Direct reduction of FeO (FeO + C -> Fe + CO)",
    "si_reduction": "Si reduction into the metal (incl. heat of solution)",
    "mn_reduction": "Mn reduction into the metal",
    "c_dissolution": "Carbon dissolving in the metal",
    "calcination": "Raw-flux calcination in the hot zone",
    "solution_loss": "CO2 from that flux reacting with coke (CO2 + C -> 2CO)",
    "hot_metal": "Hot metal: heating/melting from TRZ to tapping",
    "slag": "Slag: heating/melting from TRZ to tapping",
    "pci_cold": "PCI carbon injected cold (no preheat, unlike coke)",
    "losses": "Lower-furnace heat losses (cooling water, shell)",
}


def _h(sp, T_C):
    return float(np.interp(float(T_C) + 273.15, _HT_K, _HT[sp]))


def tuyere_supply():
    """Useful heat that 1 kg of carbon gasified at the tuyeres leaves in the hot zone (kJ/kg C), with the blast it needs.
    Per mol O2 of blast: 2 mol C burn to CO and k mol C react with the blast moisture; the blast brings its sensible
    heat, the coke carbon arrives at the TRZ temperature, and CO, H2 and N2 leave the hot zone at the TRZ temperature."""
    Tb, Tr = HEAT["blast_temp_C"], HEAT["trz_temp_C"]
    y = max(min(HEAT["blast_O2_pct"], 100.0), 1.0) / 100.0
    r = (1.0 - y) / y                                                     # mol N2 per mol O2
    k = HEAT["blast_humidity_g_Nm3"] / 18.015 * 22.414 / 1000.0 / y       # mol H2O per mol O2
    q = (2 * HEAT_DH["C_to_CO"] - k * HEAT_DH["H2O_C"]
         + _h("O2", Tb) + r * _h("N2", Tb) + k * _h("H2O", Tb)
         + (2 + k) * _h("C", Tr) - (2 + k) * _h("CO", Tr) - k * _h("H2", Tr) - r * _h("N2", Tr))
    kg_c = (2 + k) * _MW["C"] / 1000.0
    return {"kJ_per_kgC": q / kg_c, "blast_Nm3_per_kgC": (1.0 / y) * 0.022414 / kg_c,
            "blast_only_kJ_per_kgC": (_h("O2", Tb) + r * _h("N2", Tb) + k * _h("H2O", Tb)) / kg_c}


def _drr_at(sinter_pct):
    v = HEAT["drr"] + HEAT["drr_per_pt_sinter"] * (float(sinter_pct) - SINTER_REF_PCT)
    return min(max(v, 0.0), 1.0)


def heat_terms(X, df, slag, sinter_pct):
    """Hot-zone heat demand (kJ/tHM) and the carbon consumed by the reactions (kg/tHM).
    X: numbers or LP variables; slag: number or LP expression; sinter_pct: number (pinned share)."""
    Tr = HEAT["trz_temp_C"]
    dC = _h("CO", Tr) - _h("C", Tr)                  # coke carbon enters at the TRZ, its CO leaves at the TRZ
    fe_dr = _drr_at(sinter_pct) * HM_FE_KGTHM
    si = HM_BASIS_KG * HM_SI_PCT / 100.0
    mn = MN_REDUCTION_EFF * sum(X[m] * (float(df.loc[m, "Mn"]) / 100.0) for m in df.index)
    c_hm = HM_BASIS_KG * HM_C_PCT / 100.0
    raw = [m for m in df.index if is_raw_flux(df, m)]
    n_ca = sum(X[m] * (float(df.loc[m, "CaO"]) / 100.0 * 1000.0 / _MW["CaO"]) for m in raw)   # mol CaCO3
    n_mg = sum(X[m] * (float(df.loc[m, "MgO"]) / 100.0 * 1000.0 / _MW["MgO"]) for m in raw)   # mol MgCO3
    f, sl = HEAT["calc_hot_frac"], HEAT["sol_loss_frac"]
    pci_c = sum(X[m] * (float(df.loc[m, "FC"]) / 100.0) for m in mats(df, "Fuel_PCI"))
    D = {
        "direct_reduction": fe_dr * 1000.0 / _MW["Fe"] * (HEAT_DH["FeO_C"] + dC),
        "si_reduction": si * 1000.0 / _MW["Si"] * (HEAT_DH["SiO2_2C"] + HEAT_DH["Si_solution"] + 2 * dC),
        "mn_reduction": mn * (1000.0 / _MW["Mn"] * (HEAT_DH["MnO_C"] + dC)),
        "c_dissolution": c_hm * 1000.0 / _MW["C"] * HEAT_DH["C_solution"],
        "calcination": f * (HEAT_DH["CaCO3"] * n_ca + HEAT_DH["MgCO3"] * n_mg),
        "solution_loss": (f * sl * (HEAT_DH["boudouard"] + 2 * _h("CO", Tr) - _h("CO2", Tr) - _h("C", Tr))) * (n_ca + n_mg),
        "hot_metal": HM_BASIS_KG * (HEAT["h_hm_kJ_kg"] - HEAT["h_fe_trz_kJ_kg"]),
        "slag": (HEAT["h_slag_kJ_kg"] - HEAT["h_gangue_trz_kJ_kg"]) * slag,
        "pci_cold": pci_c * (1000.0 / _MW["C"] * _h("C", Tr)),
        "losses": HEAT["loss_MJ_tHM"] * 1000.0,
    }
    C = {
        "direct_reduction": fe_dr * C_PER_KG_FE_DIRECT,
        "si_reduction": si * C_PER_KG_SI,
        "mn_reduction": mn * C_PER_KG_MN,
        "c_dissolution": c_hm,
        "solution_loss": (f * sl * _MW["C"] / 1000.0) * (n_ca + n_mg),
    }
    return D, C


def heat_carbon_need(X, df, slag, sinter_pct):
    """Fixed carbon the burden needs so that the hot zone closes (kg/tHM): tuyere carbon for the heat demand plus the
    carbon the reactions consume. Linear in X. Returns (need, D, C, supply)."""
    D, C = heat_terms(X, df, slag, sinter_pct)
    sup = tuyere_supply()
    need = (1.0 / sup["kJ_per_kgC"]) * sum(D.values()) + sum(C.values())
    return need, D, C, sup


def _coke_fc_pct(blend, df):
    used = [m for m in mats(df, "Fuel_Coke") if float(blend.get(m, 0.0)) > 1e-7]
    if used:
        return sum(float(blend[m]) * float(df.loc[m, "FC"]) for m in used) / sum(float(blend[m]) for m in used)
    on = [float(df.loc[m, "FC"]) for m in mats(df, "Fuel_Coke") if bool(df.loc[m, "Available"]) and float(df.loc[m, "FC"]) > 0]
    return max(on) if on else 85.0


def heat_check(blend, df, ach):
    """Post-solve heat check of one burden (numbers only)."""
    X = {m: float(blend.get(m, 0.0)) for m in df.index}
    need, D, C, sup = heat_carbon_need(X, df, ach["slag_kg"], ach["Sinter_share_pct"])
    fc_coke = _coke_fc_pct(blend, df)
    surplus_c = ach["FC_kg"] - need
    to_coke = 100.0 / fc_coke
    return {"C_need_kg": need, "C_supplied_kg": ach["FC_kg"], "C_surplus_kg": surplus_c,
            "surplus_MJ": surplus_c * sup["kJ_per_kgC"] / 1000.0,
            "coke_min_kg": ach["Coke_kg"] - surplus_c * to_coke, "fuel_min_kg": ach["Fuel_supplied"] - surplus_c * to_coke,
            "tuyere_C_kg": sum(D.values()) / sup["kJ_per_kgC"], "reaction_C_kg": sum(C.values()),
            "D": D, "C": C, "supply": sup, "demand_MJ": sum(D.values()) / 1000.0, "coke_FC_pct": fc_coke,
            "drr": _drr_at(ach["Sinter_share_pct"]), "calibrated": bool(HEAT["calibrated"]), "mode": HEAT["mode"]}


def _dt_now():
    return _dt.datetime.now()


def calibrate_heat_losses(ach, target_fc_kg=None, note=None):
    """Set the lower-furnace loss so that the heat balance closes exactly for this burden.
    target_fc_kg = the fixed carbon actually charged per tHM (plant month); default = the carbon of this run, which
    anchors the heat model to the plant thumb rules at this burden, so only DIFFERENCES from it carry information."""
    h = ach["Heat"]
    target = ach["FC_kg"] if target_fc_kg is None else float(target_fc_kg)
    new = HEAT["loss_MJ_tHM"] + (target - h["C_need_kg"]) * h["supply"]["kJ_per_kgC"] / 1000.0
    if new < 0:
        raise ValueError(f"Calibration would need negative losses ({new:,.0f} MJ/tHM): the other placeholders "
                         "(blast temperature, DRR, enthalpies) do not fit this burden - check them first.")
    HEAT["loss_MJ_tHM"] = new
    HEAT["calibrated"] = True
    HEAT["calibration_note"] = note or (f"losses calibrated to {'the fuel of a model run' if target_fc_kg is None else 'a plant carbon rate'} "
                                        f"({target:.1f} kg C/tHM at {ach['Sinter_share_pct']:.1f}% sinter) on {_dt_now():%d %b %Y %H:%M}")
    return new


def heat_note(h):
    flag = "calibrated" if h["calibrated"] else "PLACEHOLDER values, not calibrated"
    word = "surplus" if h["C_surplus_kg"] >= 0 else "deficit"
    return (f"HEAT CHECK (hot zone, {flag}): carbon charged {h['C_supplied_kg']:.1f} vs needed {h['C_need_kg']:.1f} kg/tHM - "
            f"{word} {abs(h['C_surplus_kg']):.1f} kg C ({h['surplus_MJ']:+,.0f} MJ/tHM). Heat-balance minimum fuel "
            f"{h['fuel_min_kg']:.1f} kg/tHM" + (" (floor mode: enforced in the LP)." if HEAT["mode"] == "floor" else " (report only)."))


def heat_table(ach):
    h = ach["Heat"]
    s = h["supply"]["kJ_per_kgC"]
    rows = []
    tot = sum(h["D"].values())
    for k, v in h["D"].items():
        rows.append({"Item": HEAT_TERM_LABELS[k], "MJ/tHM": round(v / 1000.0, 1), "% of demand": round(100 * v / tot, 1) if tot else np.nan,
                     "kg C/tHM": round(v / s + float(h["C"].get(k, 0.0)), 2)})
    rows.append({"Item": "TOTAL hot-zone heat demand", "MJ/tHM": round(tot / 1000.0, 1), "% of demand": 100.0, "kg C/tHM": np.nan})
    rows += [
        {"Item": "Useful heat per kg carbon burnt at the tuyeres (kJ/kg C)", "MJ/tHM": round(s, 0), "% of demand": np.nan, "kg C/tHM": np.nan},
        {"Item": "  of which hot-blast sensible heat (kJ/kg C)", "MJ/tHM": round(h["supply"]["blast_only_kJ_per_kgC"], 0), "% of demand": np.nan, "kg C/tHM": np.nan},
        {"Item": "Blast needed per kg tuyere carbon (Nm3 dry)", "MJ/tHM": round(h["supply"]["blast_Nm3_per_kgC"], 2), "% of demand": np.nan, "kg C/tHM": np.nan},
        {"Item": "Tuyere carbon needed for the heat demand", "MJ/tHM": np.nan, "% of demand": np.nan, "kg C/tHM": round(h["tuyere_C_kg"], 2)},
        {"Item": "Carbon consumed by the reactions (DR, Si, Mn, C in HM, solution loss)", "MJ/tHM": np.nan, "% of demand": np.nan, "kg C/tHM": round(h["reaction_C_kg"], 2)},
        {"Item": "TOTAL carbon needed", "MJ/tHM": np.nan, "% of demand": np.nan, "kg C/tHM": round(h["C_need_kg"], 2)},
        {"Item": "Fixed carbon charged (coke + nut coke + PCI)", "MJ/tHM": np.nan, "% of demand": np.nan, "kg C/tHM": round(h["C_supplied_kg"], 2)},
        {"Item": "SURPLUS (+) / DEFICIT (-)", "MJ/tHM": round(h["surplus_MJ"], 1), "% of demand": np.nan, "kg C/tHM": round(h["C_surplus_kg"], 2)},
        {"Item": "Heat-balance minimum fuel (kg/tHM) vs plant rule", "MJ/tHM": round(h["fuel_min_kg"], 1), "% of demand": np.nan,
         "kg C/tHM": round(ach["Fuel_rule"], 1)},
    ]
    out = pd.DataFrame(rows)
    out.attrs["note"] = (f"Degree of direct reduction {h['drr']:.3f}; coke FC {h['coke_FC_pct']:.1f}%. {HEAT['calibration_note']}. "
                         "Moisture is evaporated at the top of the furnace, outside the hot zone, so it does not enter this balance.")
    return out


def heat_vs_rules(df, ach):
    """Marginal effects: what the plant thumb rules say vs what the hot-zone balance says (kg fuel per unit)."""
    s = ach["Heat"]["supply"]["kJ_per_kgC"]
    to_coke = 100.0 / ach["Heat"]["coke_FC_pct"]
    rows = []
    slag_c = (HEAT["h_slag_kJ_kg"] - HEAT["h_gangue_trz_kJ_kg"]) / s
    rows.append({"Change": "+100 kg slag", "Plant rule (kg fuel)": round(100 * FUEL_PER_KG_SLAG, 1),
                 "Heat balance (kg coke)": round(100 * slag_c * to_coke, 1), "Note": "slag heating/melting only"})
    for m in df.index:
        if is_raw_flux(df, m):
            X = {k: 0.0 for k in df.index}; X[m] = 100.0
            D, C = heat_terms(X, df, 0.0, SINTER_REF_PCT)
            base = heat_terms({k: 0.0 for k in df.index}, df, 0.0, SINTER_REF_PCT)
            dc = sum(D[k] - base[0][k] for k in D) / s + sum(C[k] - base[1][k] for k in C)
            slag_kg = sum(100.0 * float(df.loc[m, o]) / 100.0 for o in OXIDES)
            rows.append({"Change": f"+100 kg {m} (raw flux)", "Plant rule (kg fuel)": round(100 * FUEL_PER_KG_RAW_FLUX, 1),
                         "Heat balance (kg coke)": round(dc * to_coke, 1),
                         "Note": f"calcination + solution loss; its own ~{slag_kg:.0f} kg slag adds {slag_kg * slag_c * to_coke:.1f} kg more"})
    rows.append({"Change": "+10 pts sinter share", "Plant rule (kg fuel)": round(-10 * FUEL_PER_PCT_SINTER, 1),
                 "Heat balance (kg coke)": round(10 * HEAT["drr_per_pt_sinter"] * HM_FE_KGTHM * 1000 / _MW["Fe"]
                                                 * (HEAT_DH["FeO_C"] + _h("CO", HEAT["trz_temp_C"]) - _h("C", HEAT["trz_temp_C"])) / s * to_coke
                                                 + 10 * HEAT["drr_per_pt_sinter"] * HM_FE_KGTHM * C_PER_KG_FE_DIRECT * to_coke, 1),
                 "Note": "reducibility effect only (DRR slope); flux and slag effects appear in the 0-100% curve"})
    return pd.DataFrame(rows)


def heat_sinter_slope(curve, a=60.0, b=70.0):
    """Coke change per +10 pts sinter between a and b on the 0-100% curve: model coke vs heat-balance minimum coke."""
    if curve is None or "Heat-min coke kg" not in curve.columns:
        return None
    ok = curve[curve["Status"] == "Optimal"].set_index("Sinter %")
    if a not in ok.index or b not in ok.index:
        return None
    k = 10.0 / (b - a)
    dm = (ok.loc[b, "Coke kg"] - ok.loc[a, "Coke kg"]) * k
    dh = (ok.loc[b, "Heat-min coke kg"] - ok.loc[a, "Heat-min coke kg"]) * k
    return (f"Per +10 pts sinter between {a:g}% and {b:g}%: model coke {dm:+.1f} kg/tHM (plant rules), "
            f"heat-balance minimum coke {dh:+.1f} kg/tHM.")


# ================================================================
# 5c. HEAT AUDIT - PLANT MONTHS -> MEASURED HEAT PARAMETERS   [new in notebook v11.7]
# ================================================================
# Reads one workbook per plant month (template: audit_template_bytes), checks that the plant's own numbers are
# consistent (carbon balance, nitrogen balance for the top-gas volume, iron per 100 kg HM), then MEASURES what the
# heat model had as placeholders:
#   * tuyere carbon from the blast (2 C per O2 + 1 C per H2O), not from a formula;
#   * degree of direct reduction from the carbon that left with the top gas beyond the tuyere carbon (Rist);
#     a second, independent estimate comes from the carbon charged (their difference is the carbon-balance error);
#   * hot-zone heat losses from the cooling-water circuits tagged "hot zone" (flow x temperature rise);
#   * the hot-zone heat balance from measured supply and demand, with the RESIDUAL reported, never forced to zero;
#   * the plant thumb-rule fuel against the fuel actually charged, month by month;
#   * DRR against sinter share (needs >= 3 months spread over >= 5 points) and fuel against sinter share.
# audit_apply() then writes the measured values into HEAT. Nothing is applied unless the user asks.
# Dashboard: use audit_run_cfg / audit_apply_cfg, which load the user's Config and return the new heat settings.
AUDIT_KEYS = [   # key, label, unit, example value, required, note
    ("period", "Period label", "text", "Example month", True, "Any label, e.g. Sep-26"),
    ("hm_t", "Hot metal produced", "t", 30000.0, True, "Tapped hot metal in the period"),
    ("hours", "Operating hours (on blast)", "h", 720.0, True, "Hours the furnace was blowing"),
    ("hm_temp_C", "Hot metal temperature at the taphole", "C", 1470.0, True, "Average of tap measurements"),
    ("hm_fe_pct", "Fe in hot metal", "%", 94.0, True, "Average analysis"),
    ("hm_c_pct", "C in hot metal", "%", 4.3, True, "Average analysis"),
    ("hm_si_pct", "Si in hot metal", "%", 0.6, True, "Average analysis"),
    ("hm_mn_pct", "Mn in hot metal", "%", None, True, "Average analysis"),
    ("blast_Nm3_min", "Blast volume, average, DRY basis", "Nm3/min", None, True, "State on the sheet if your meter reads wet"),
    ("blast_temp_C", "Hot-blast temperature", "C", 1000.0, True, "Average at the bustle pipe"),
    ("blast_h2o_g_Nm3", "Moisture + steam in blast", "g/Nm3 dry blast", 15.0, True, "Humidity plus any steam injection"),
    ("blast_o2_pct", "O2 in dry blast (21 = no enrichment)", "%", 21.0, True, "Include oxygen enrichment"),
    ("tg_co_pct", "Top gas CO (dry)", "%", None, True, "Average analysis"),
    ("tg_co2_pct", "Top gas CO2 (dry)", "%", None, True, "Average analysis"),
    ("tg_h2_pct", "Top gas H2 (dry)", "%", None, True, "Average analysis"),
    ("tg_n2_pct", "Top gas N2 (dry)", "%", None, True, "Average analysis (not by difference if you can avoid it)"),
    ("tg_temp_C", "Top gas temperature", "C", 200.0, False, "For the later whole-furnace cross-check"),
    ("tg_Nm3_h", "Top gas volume, measured (optional)", "Nm3/h", None, False, "Compared with the nitrogen-balance volume"),
    ("slag_t", "Slag produced, measured (optional)", "t", None, False, "Compared with the chemistry-based slag"),
    ("slag_temp_C", "Slag temperature at tapping", "C", 1500.0, True, "Average"),
    ("dust_kg_thm", "Flue dust", "kg/tHM", 15.0, True, "Carried out of the furnace top"),
    ("dust_c_pct", "Carbon in flue dust", "%", 35.0, True, "Average analysis"),
]
AUDIT_MAT_COLS = ["Material", "Group", "Consumed_t", "Moisture %", "Fe %", "CaO %", "MgO %", "SiO2 %", "Al2O3 %", "Mn %", "S %", "FC %", "C_total %"]
AUDIT_COOL_COLS = ["Circuit", "Hot_zone (Y/N)", "Flow_m3_h", "T_in_C", "T_out_C"]
AUDIT_Q_QUESTIONS = [
    "Which cooling circuits serve the furnace below the thermal reserve zone (tuyeres, tuyere stocks, bosh, belly, lower stave/plate coolers)? Mark them Y in the Cooling sheet.",
    "Is the blast volume meter reading on a dry or wet basis, and at what reference conditions (Nm3 = 0 C, 1 atm)?",
    "Is steam or oxygen injected into the blast? If so, enter it in the moisture and O2 rows.",
    "Is coal injected through every tuyere, and does PCI go in as a Fuel_PCI row of the Materials sheet?",
    "How is the top gas sampled (how many points, how often) and is it analysed dry? Is CH4 present?",
    "Does 'Consumed_t' come from bin weighing (wet, as charged)? Enter the moisture of each material so it can be dried.",
    "What is C_total of coke, nut coke and PCI (fixed carbon + volatile carbon)? Blank means fixed carbon only.",
    "Were there long stoppages, blow-downs or abnormal running (hanging, slips) in the period? Note them: such months should not be used.",
    "Are the cooling-water temperature rises taken on each circuit or on a common header?",
]
_AUDIT_TITLE = {k: (lab, unit, ex, req, note) for k, lab, unit, ex, req, note in AUDIT_KEYS}


@contextlib.contextmanager
def _hm_override(M):
    """Temporarily use the month's hot-metal analysis in the model constants (restored on exit)."""
    g = globals()
    names = ("HM_SI_PCT", "HM_C_PCT", "HM_FE_PCT", "HM_FE_KGTHM")
    old = {k: g[k] for k in names}
    g["HM_SI_PCT"], g["HM_C_PCT"], g["HM_FE_PCT"] = float(M["hm_si_pct"]), float(M["hm_c_pct"]), float(M["hm_fe_pct"])
    g["HM_FE_KGTHM"] = HM_BASIS_KG * float(M["hm_fe_pct"]) / 100.0
    try:
        yield
    finally:
        g.update(old)


def _hm_enthalpy(T_C):
    return HEAT_DEFAULT["h_hm_kJ_kg"] + 0.9 * (float(T_C) - 1450.0)        # kJ/kg above 25 C, placeholder slope


def _slag_enthalpy(T_C):
    return HEAT_DEFAULT["h_slag_kJ_kg"] + 1.3 * (float(T_C) - 1500.0)      # kJ/kg above 25 C, placeholder slope


def audit_month(rec, blast_scale=1.0, gas_shift=0.0):
    """Audit ONE plant month. rec = {'month','df','cons_t','ctotal','cooling','name'}.
    blast_scale / gas_shift are only for the sensitivity table (blast volume x, top-gas CO+CO2 + points)."""
    M, df, cons, cool = rec["month"], rec["df"], rec["cons_t"], rec["cooling"]
    ct = rec.get("ctotal", {})
    hm_t, hrs = float(M["hm_t"]), float(M["hours"])
    flags, errors = [], []
    blend = {m: float(cons.get(m, 0.0)) * (1.0 - float(df.loc[m, "Moisture_Pct"]) / 100.0) * 1000.0 / hm_t for m in df.index}
    R = {"name": rec.get("name", str(M["period"])), "period": M["period"], "hm_t": hm_t, "flags": flags, "errors": errors}
    saved_heat = copy.deepcopy(HEAT)
    try:
        with _hm_override(M):
            ach = _calc_achieved_core(blend, df)
            # ---- carbon charged
            fuel_c = sum(blend[m] * float(ct.get(m, df.loc[m, "FC"]) or df.loc[m, "FC"]) / 100.0
                         for g in FUEL_GROUPS for m in mats(df, g))
            carb_c = sum(blend[m] * (float(df.loc[m, "CaO"]) / 56.077 + float(df.loc[m, "MgO"]) / 40.304) * _MW["C"] / 100.0
                         for m in df.index if is_raw_flux(df, m))
            # ---- blast (per tHM)
            V = float(M["blast_Nm3_min"]) * blast_scale * 60.0 * hrs / hm_t
            y = float(M["blast_o2_pct"]) / 100.0
            n_o2, n_n2 = V * y / 22.414, V * (1.0 - y) / 22.414
            n_h2o = V * float(M["blast_h2o_g_Nm3"]) / 1000.0 / 18.015
            c_tuy = (2.0 * n_o2 + n_h2o) * _MW["C"]
            # ---- top gas: nitrogen balance gives the volume
            co, co2 = float(M["tg_co_pct"]) + gas_shift, float(M["tg_co2_pct"])
            h2, n2 = float(M["tg_h2_pct"]), float(M["tg_n2_pct"])
            gsum = co + co2 + h2 + n2
            if abs(gsum - 100.0) > 2.5:
                flags.append(f"top-gas analysis adds up to {gsum:.1f}% (CH4 or a missing component?)")
            v_tg = n_n2 * 22.414 / (n2 / 100.0)
            tgm = M.get("tg_Nm3_h")
            if tgm:
                v_meas = float(tgm) * hrs / hm_t
                R["tg_ratio"] = v_meas / v_tg
                if abs(R["tg_ratio"] - 1.0) > 0.10:
                    flags.append(f"measured top-gas volume is {100 * (R['tg_ratio'] - 1):+.0f}% vs the nitrogen balance - check the blast or top-gas meter")
            c_gas = v_tg * (co + co2) / 100.0 / 22.414 * _MW["C"]
            c_hm = HM_BASIS_KG * float(M["hm_c_pct"]) / 100.0
            c_dust = float(M["dust_kg_thm"]) * float(M["dust_c_pct"]) / 100.0
            c_in = fuel_c + carb_c
            gap = c_in - (c_hm + c_gas + c_dust)
            R.update(fuel_C=fuel_c, carb_C=carb_c, blast_Nm3_tHM=V, C_tuy=c_tuy, tg_Nm3_tHM=v_tg, C_gas=c_gas, C_hm=c_hm,
                     C_dust=c_dust, C_gap=gap, C_gap_pct=100.0 * gap / c_in if c_in > 0 else np.nan)
            # ---- degree of direct reduction (Rist): carbon gasified other than by blast oxygen
            fe_hm = HM_BASIS_KG * float(M["hm_fe_pct"]) / 100.0
            si_kg = HM_BASIS_KG * float(M["hm_si_pct"]) / 100.0
            mn_kg = HM_BASIS_KG * float(M["hm_mn_pct"]) / 100.0
            c_si, c_mn = si_kg * C_PER_KG_SI, mn_kg * C_PER_KG_MN
            c_sol = HEAT["calc_hot_frac"] * HEAT["sol_loss_frac"] * carb_c
            den = fe_hm * C_PER_KG_FE_DIRECT
            drr_gas = (c_gas - c_tuy - carb_c - c_si - c_mn - c_sol) / den
            drr_fuel = (fuel_c - c_hm - c_dust - c_tuy - c_si - c_mn - c_sol) / den
            R.update(DRR_gas=drr_gas, DRR_fuel=drr_fuel, DRR=drr_gas)
            # ---- cooling water
            per_h = hm_t / hrs
            q_hot = q_all = 0.0
            for c in cool:
                q = float(c["flow"]) * (float(c["t_out"]) - float(c["t_in"])) * 4.186 / per_h
                q_all += q
                if c["hot"]:
                    q_hot += q
            R.update(Q_cool_hot=q_hot, Q_cool_all=q_all)
            if not any(c["hot"] for c in cool):
                errors.append("no cooling circuit is tagged as hot zone")
            # ---- hot-zone heat balance from measurements (loss = measured cooling of the hot-zone circuits)
            HEAT.update(blast_temp_C=float(M["blast_temp_C"]), blast_humidity_g_Nm3=float(M["blast_h2o_g_Nm3"]),
                        blast_O2_pct=float(M["blast_o2_pct"]), drr=min(max(drr_gas, 0.0), 1.0), drr_per_pt_sinter=0.0,
                        loss_MJ_tHM=q_hot, h_hm_kJ_kg=_hm_enthalpy(M["hm_temp_C"]), h_slag_kJ_kg=_slag_enthalpy(M["slag_temp_C"]),
                        mode="check", calibrated=True)
            a2 = dict(ach)
            slag_meas = M.get("slag_t")
            if slag_meas:
                R["slag_ratio"] = float(slag_meas) * 1000.0 / hm_t / ach["slag_kg"]
                a2["slag_kg"] = float(slag_meas) * 1000.0 / hm_t
                if abs(R["slag_ratio"] - 1.0) > 0.08:
                    flags.append(f"measured slag is {100 * (R['slag_ratio'] - 1):+.0f}% vs the chemistry-based slag - check assays or slag weighing")
            h = heat_check(blend, df, a2)
            q_c = h["supply"]["kJ_per_kgC"]
            supply, demand = c_tuy * q_c / 1000.0, sum(h["D"].values()) / 1000.0
            fc_eff = ach["FC_kg"] - c_dust                       # carbon lost in the dust gives no heat
            R.update(q_c=q_c, supply_MJ=supply, demand_MJ=demand, residual_MJ=supply - demand,
                     residual_pct=100.0 * (supply - demand) / supply if supply > 0 else np.nan,
                     C_need=h["C_need_kg"], C_surplus=fc_eff - h["C_need_kg"], fuel_min=ach["Fuel_supplied"] - (fc_eff - h["C_need_kg"]) * 100.0 / h["coke_FC_pct"],
                     D=h["D"], calib={"blast_temp_C": HEAT["blast_temp_C"], "blast_humidity_g_Nm3": HEAT["blast_humidity_g_Nm3"],
                                      "blast_O2_pct": HEAT["blast_O2_pct"], "h_hm_kJ_kg": HEAT["h_hm_kJ_kg"], "h_slag_kJ_kg": HEAT["h_slag_kJ_kg"]})
            # ---- plant thumb rules vs what was charged, and iron per 100 kg HM
            R.update(sinter_pct=ach["Sinter_share_pct"], fuel_actual=ach["Fuel_supplied"], coke_actual=ach["Coke_kg"], pci_actual=ach["PCI_kg"],
                     fuel_rule=ach["Fuel_rule"], slag_model=ach["slag_kg"], fe_per_100=ach["Fe_burden_kg"] / (HM_BASIS_KG / 100.0))
    finally:
        HEAT.clear(); HEAT.update(saved_heat)
    if abs(R["C_gap_pct"]) > 5.0:
        flags.append(f"carbon balance does not close: {R['C_gap_pct']:+.1f}% of the carbon charged (coke weighing, top-gas analysis or blast volume)")
    if abs(R["C_gap_pct"]) > 10.0:
        errors.append("carbon balance error above 10% - the DRR from this month is not trustworthy")
    if not (0.0 <= R["DRR"] <= 1.0):
        errors.append(f"direct reduction degree {R['DRR']:.2f} is outside 0-1")
    if abs(R["DRR_gas"] - R["DRR_fuel"]) > 0.08:
        flags.append(f"DRR from the gas ({R['DRR_gas']:.2f}) and from the carbon charged ({R['DRR_fuel']:.2f}) differ by more than 0.08")
    if abs(R["residual_pct"]) > 15.0:
        flags.append(f"hot-zone heat residual is {R['residual_pct']:+.0f}% of the tuyere heat - a large unaccounted term (TRZ temperature, shell losses, enthalpies)")
    if abs(R["fe_per_100"] - FE_REQUIRED_PER_100KG_HM) > 1.0:
        flags.append(f"iron charged is {R['fe_per_100']:.1f} kg per 100 kg HM (model uses {FE_REQUIRED_PER_100KG_HM:g})")
    R["usable"] = not errors
    return R


def audit_sensitivity(rec):
    """How far the audit result moves for plausible measurement errors (blast volume +/-3%, top-gas CO+CO2 +/-1 point)."""
    rows = []
    for lab, kw in (("as measured", {}), ("blast volume +3%", {"blast_scale": 1.03}), ("blast volume -3%", {"blast_scale": 0.97}),
                    ("top-gas CO +1 pt", {"gas_shift": 1.0}), ("top-gas CO -1 pt", {"gas_shift": -1.0})):
        r = audit_month(rec, **kw)
        rows.append({"Case": lab, "DRR (gas)": round(r["DRR_gas"], 3), "DRR (carbon charged)": round(r["DRR_fuel"], 3),
                     "Carbon gap %": round(r["C_gap_pct"], 2), "Heat residual MJ/tHM": round(r["residual_MJ"], 0)})
    return pd.DataFrame(rows)


def _ols(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    slope = float(((x - xm) * (y - ym)).sum() / sxx)
    icpt = ym - slope * xm
    res = y - (icpt + slope * x)
    se = float(np.sqrt((res ** 2).sum() / (n - 2) / sxx)) if n > 2 else np.nan
    ss_tot = float(((y - ym) ** 2).sum())
    return {"n": n, "slope": slope, "intercept": icpt, "se": se, "t": slope / se if se and se > 0 else np.nan,
            "r2": 1.0 - float((res ** 2).sum()) / ss_tot if ss_tot > 0 else np.nan, "x_mean": xm, "y_mean": ym}


def audit_run(recs):
    """Audit every month, then the across-month fits. Returns dict(results, months, fits, flags)."""
    results = [audit_month(r) for r in recs]
    use = [r for r in results if r["usable"]]
    rows = []
    for r in results:
        rows.append({"Month": r["period"], "Usable": "YES" if r["usable"] else "NO", "HM t": round(r["hm_t"], 0), "Sinter %": round(r["sinter_pct"], 2),
                     "Fuel charged kg/tHM": round(r["fuel_actual"], 1), "Coke kg/tHM": round(r["coke_actual"], 1),
                     "Thumb-rule fuel kg/tHM": round(r["fuel_rule"], 1), "Charged - rule kg": round(r["fuel_actual"] - r["fuel_rule"], 1),
                     "Blast Nm3/tHM": round(r["blast_Nm3_tHM"], 0), "Tuyere C kg/tHM": round(r["C_tuy"], 1),
                     "Carbon gap % charged": round(r["C_gap_pct"], 2), "DRR (gas)": round(r["DRR_gas"], 3), "DRR (carbon charged)": round(r["DRR_fuel"], 3),
                     "Cooling loss, hot zone MJ/tHM": round(r["Q_cool_hot"], 0), "Cooling loss, all MJ/tHM": round(r["Q_cool_all"], 0),
                     "Tuyere heat MJ/tHM": round(r["supply_MJ"], 0), "Hot-zone demand MJ/tHM": round(r["demand_MJ"], 0),
                     "Heat residual MJ/tHM": round(r["residual_MJ"], 0), "Residual % of supply": round(r["residual_pct"], 1),
                     "Heat-min fuel kg/tHM": round(r["fuel_min"], 1), "Fe per 100 kg HM": round(r["fe_per_100"], 2)})
    fits = {"n_used": len(use)}
    xs = [r["sinter_pct"] for r in use]
    if len(use) >= 3 and (max(xs) - min(xs)) >= 5.0:
        fd = _ols(xs, [r["DRR"] for r in use])
        ff = _ols(xs, [r["fuel_actual"] for r in use])
        fd["applied"] = bool(abs(fd["t"]) >= 2.0) if np.isfinite(fd["t"]) else False
        fits["drr"], fits["fuel"] = fd, ff
    if use:
        fits["drr_mean"] = float(np.mean([r["DRR"] for r in use]))
        fits["res_mean"], fits["res_sd"] = float(np.mean([r["residual_MJ"] for r in use])), float(np.std([r["residual_MJ"] for r in use]))
        fits["cool_mean"] = float(np.mean([r["Q_cool_hot"] for r in use]))
        fits["rule_diff_mean"] = float(np.mean([r["fuel_actual"] - r["fuel_rule"] for r in use]))
    flags = [f"{r['period']}: {f}" for r in results for f in r["flags"]] + [f"{r['period']}: NOT USABLE - {e}" for r in results for e in r["errors"]]
    if len(use) < 3:
        flags.append(f"{len(use)} usable month(s): the DRR-vs-sinter slope needs at least 3 months spread over 5 points of sinter share; "
                     "only an average DRR can be applied.")
    elif "drr" not in fits:
        flags.append("the usable months cover less than 5 points of sinter share: the DRR-vs-sinter slope cannot be estimated.")
    return {"results": results, "months": pd.DataFrame(rows), "fits": fits, "flags": flags}


def audit_fit_text(A):
    f, out = A["fits"], []
    if "drr" in f:
        d, u = f["drr"], f["fuel"]
        out.append(f"DRR vs sinter share ({d['n']} months): {d['slope']:+.4f} per point (t = {min(abs(d['t']), 99):.1f}, R2 = {d['r2']:.2f}); "
                   + ("significant - can be applied." if d["applied"] else "NOT significant (|t| < 2) - will not be applied; only the mean DRR is used."))
        out.append(f"Fuel charged vs sinter share: {u['slope']:+.2f} kg per +1 point (plant thumb rule: {-FUEL_PER_PCT_SINTER:+.1f}; "
                   f"t = {min(abs(u['t']), 99):.1f}, R2 = {u['r2']:.2f}). Other things also changed between months, so treat this as evidence, not proof.")
    if "res_mean" in f:
        out.append(f"Hot-zone heat residual: {f['res_mean']:+,.0f} MJ/tHM on average, month-to-month spread +/-{f['res_sd']:,.0f}. "
                   + ("A small spread means the unaccounted term is stable and can be absorbed as a constant; a large one means the balance is missing a variable."))
        out.append(f"Thumb-rule fuel vs fuel charged: charged is on average {f['rule_diff_mean']:+.1f} kg/tHM relative to the rule.")
    return out


def audit_apply(A, mode="measured"):
    """Write the measured parameters into HEAT. mode 'measured' = cooling-water losses only; 'anchored' = also absorb the
    average heat residual into the losses (clearly labelled: this is a fudge factor, not a measurement)."""
    f = A["fits"]
    use = [r for r in A["results"] if r["usable"]]
    if not use:
        raise ValueError("No usable month - nothing to apply.")
    keys = ("blast_temp_C", "blast_humidity_g_Nm3", "blast_O2_pct", "h_hm_kJ_kg", "h_slag_kJ_kg")
    for k in keys:
        HEAT[k] = float(np.mean([r["calib"][k] for r in use]))
    if "drr" in f and f["drr"]["applied"]:
        d = f["drr"]
        HEAT["drr_per_pt_sinter"] = d["slope"]
        HEAT["drr"] = float(min(max(d["y_mean"] + d["slope"] * (SINTER_REF_PCT - d["x_mean"]), 0.0), 1.0))
        drr_txt = f"DRR {HEAT['drr']:.3f} at {SINTER_REF_PCT:g}% sinter with slope {d['slope']:+.4f}/pt"
    else:
        HEAT["drr_per_pt_sinter"] = 0.0
        HEAT["drr"] = float(f["drr_mean"])
        drr_txt = f"DRR {HEAT['drr']:.3f} (average of {len(use)} month(s); no slope)"
    loss = float(f["cool_mean"])
    anchor = ""
    if mode == "anchored":
        loss += float(f["res_mean"])
        if loss < 0:
            raise ValueError("Anchoring would make the losses negative - the measured heat terms do not fit; do not anchor.")
        anchor = f"; residual {f['res_mean']:+,.0f} MJ/tHM ABSORBED into the losses (anchor, not a measurement)"
    HEAT["loss_MJ_tHM"] = loss
    HEAT["calibrated"] = True
    HEAT["calibration_note"] = (f"audited on {len(use)} plant month(s) ({', '.join(str(r['period']) for r in use)}): {drr_txt}; "
                                f"hot-zone cooling losses {f['cool_mean']:,.0f} MJ/tHM measured{anchor}; TRZ temperature, calcination share and "
                                f"enthalpy table are still placeholders")
    return HEAT["calibration_note"]


# ---- template writer / reader ------------------------------------------------------------------------------
def audit_write_template(path, rec=None):
    """Template workbook (path or a binary buffer). With rec (a synthetic example month) it is pre-filled so the audit
    can be tried end to end."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    hdr_fill, hdr_font = PatternFill("solid", start_color="1F3864", end_color="1F3864"), Font(bold=True, color="FFFFFF")

    def head(ws, cols):
        ws.append(cols)
        for c in ws[1]:
            c.fill, c.font, c.alignment = hdr_fill, hdr_font, Alignment(horizontal="center", wrap_text=True)

    ws = wb.active; ws.title = "READ ME"
    lines = ["MBF HEAT AUDIT - one workbook per plant month (or campaign)",
             "EXAMPLE DATA: this file is pre-filled with a SYNTHETIC month generated by the model so you can test the audit. Replace every value." if rec else
             "Fill the Month, Materials and Cooling sheets. Leave optional cells blank if unknown.",
             "", "Choose steady months: no long stoppage, blow-down or hanging. Use 3 or more months with different sinter shares if you can.",
             "Quantities are monthly totals or averages exactly as stated in the Unit column. Nm3 = 0 C, 1 atm.", "",
             "QUESTIONS THE PLANT SHOULD ANSWER (write the answers below the list or send them back):"]
    for i, q in enumerate(AUDIT_Q_QUESTIONS, 1):
        lines.append(f"  {i}. {q}")
    for ln in lines:
        ws.append([ln])
    ws.column_dimensions["A"].width = 150
    ws["A1"].font = Font(bold=True, size=14)

    wm = wb.create_sheet("Month")
    head(wm, ["Key", "Parameter", "Value", "Unit", "Required", "Note"])
    for k, lab, unit, ex, req, note in AUDIT_KEYS:
        v = rec["month"].get(k) if rec else None
        wm.append([k, lab, v, unit, "yes" if req else "optional", note])
    for col, w in zip("ABCDEF", (18, 44, 16, 18, 10, 60)):
        wm.column_dimensions[col].width = w

    wt = wb.create_sheet("Materials")
    head(wt, AUDIT_MAT_COLS)
    if rec:
        for m in rec["df"].index:
            d = rec["df"].loc[m]
            wt.append([m, d["Group"], round(rec["cons_t"][m], 3), d["Moisture_Pct"], d["Fe"], d["CaO"], d["MgO"], d["SiO2"], d["Al2O3"],
                       d["Mn"], d["S"], d["FC"], rec.get("ctotal", {}).get(m)])
    wt.append([])
    wt.append(["Group must be one of: " + ", ".join(GROUPS) + ". Consumed_t = tonnes charged in the period, AS CHARGED (wet). Assays are % dry basis."])
    for i, w in enumerate((16, 14, 13, 11, 8, 8, 8, 8, 9, 8, 8, 8, 10)):
        wt.column_dimensions[chr(65 + i)].width = w

    wc = wb.create_sheet("Cooling")
    head(wc, AUDIT_COOL_COLS)
    if rec:
        for c in rec["cooling"]:
            wc.append([c["name"], "Y" if c["hot"] else "N", round(c["flow"], 2), c["t_in"], round(c["t_out"], 3)])
    for col, w in zip("ABCDE", (34, 16, 14, 10, 10)):
        wc.column_dimensions[col].width = w
    wb.save(path)
    return path


def audit_read(src, name=None):
    """Read one plant-month workbook (path or bytes) into a rec for audit_month()."""
    xl = pd.ExcelFile(io.BytesIO(bytes(src)) if isinstance(src, (bytes, bytearray, memoryview)) else src)
    for sh in ("Month", "Materials", "Cooling"):
        if sh not in xl.sheet_names:
            raise ValueError(f"The workbook has no '{sh}' sheet - use the audit template.")
    mo = xl.parse("Month")
    kcol = next((c for c in mo.columns if norm(c) == "key"), None)
    vcol = next((c for c in mo.columns if norm(c) == "value"), None)
    if kcol is None or vcol is None:
        raise ValueError("Month sheet needs 'Key' and 'Value' columns.")
    raw = {str(k).strip(): v for k, v in zip(mo[kcol], mo[vcol])}
    M, miss = {}, []
    for k, lab, unit, ex, req, note in AUDIT_KEYS:
        v = raw.get(k)
        blank = v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == ""
        if blank:
            if req:
                miss.append(f"{lab} [{k}]")
            M[k] = None
        elif k == "period":
            M[k] = str(v).strip()
        else:
            try:
                M[k] = float(v)
            except Exception:
                raise ValueError(f"Month sheet: '{lab}' is not a number ({v!r})")
    if miss:
        raise ValueError("Month sheet is missing required values: " + "; ".join(miss))
    bad = [lab for k, lab, ok in (("hm_t", "Hot metal produced", M["hm_t"] > 0), ("hours", "Operating hours", M["hours"] > 0),
                                   ("blast_Nm3_min", "Blast volume", M["blast_Nm3_min"] > 0), ("tg_n2_pct", "Top gas N2", M["tg_n2_pct"] > 0),
                                   ("blast_o2_pct", "O2 in blast (15-50%)", 15 <= M["blast_o2_pct"] <= 50),
                                   ("hm_fe_pct", "Fe in hot metal (50-100%)", 50 <= M["hm_fe_pct"] <= 100)) if not ok]
    if bad:
        raise ValueError("Month sheet has impossible values: " + "; ".join(bad))
    mt = xl.parse("Materials")
    nc = {norm(c): c for c in mt.columns}
    need = {"material": "Material", "group": "Group", "consumedt": "Consumed_t"}
    for k, v in need.items():
        if k not in nc:
            raise ValueError(f"Materials sheet needs a '{v}' column.")
    col = lambda c: nc.get(norm(c))
    rows, cons, ctot = [], {}, {}
    for _, r in mt.iterrows():
        if pd.isna(r[nc["material"]]) or str(r[nc["material"]]).startswith("Group must"):
            continue
        t = r[nc["consumedt"]]
        if pd.isna(t) or float(t) <= 0:
            continue
        nm = sanitize_name(r[nc["material"]])
        g = GROUP_LOOKUP.get(norm(r[nc["group"]]), str(r[nc["group"]]).strip())
        if g not in GROUPS:
            raise ValueError(f"Materials: {nm} has invalid Group '{g}'.")
        rec_ = {"Material": nm, "Group": g}
        for key, hd in (("Fe", "Fe %"), ("CaO", "CaO %"), ("MgO", "MgO %"), ("SiO2", "SiO2 %"), ("Al2O3", "Al2O3 %"), ("Mn", "Mn %"),
                        ("S", "S %"), ("FC", "FC %"), ("Moisture_Pct", "Moisture %")):
            c_ = col(hd)
            rec_[key] = float(r[c_]) if c_ and pd.notna(r[c_]) else 0.0
        rec_["Price_Rs_t"] = 1.0
        rec_["RM_Stock"] = rec_["Fines_Pct"] = rec_["Fines_Credit_Rs_t"] = np.nan
        rec_["Available"] = True
        rows.append(rec_); cons[nm] = float(t)
        c_ = col("C_total %")
        if c_ and pd.notna(r[c_]):
            ctot[nm] = float(r[c_])
    if not rows:
        raise ValueError("Materials sheet has no material with Consumed_t > 0.")
    df = pd.DataFrame(rows).set_index("Material")[COLUMNS[1:]]
    cl = xl.parse("Cooling")
    cc = {norm(c): c for c in cl.columns}
    for k, v in {"circuit": "Circuit", "hotzoneyn": "Hot_zone (Y/N)", "flowm3h": "Flow_m3_h", "tinc": "T_in_C", "toutc": "T_out_C"}.items():
        if k not in cc:
            raise ValueError(f"Cooling sheet needs a '{v}' column.")
    cool = []
    for _, r in cl.iterrows():
        if pd.isna(r[cc["circuit"]]):
            continue
        try:
            cool.append({"name": str(r[cc["circuit"]]), "hot": str(r[cc["hotzoneyn"]]).strip().upper().startswith("Y"),
                         "flow": float(r[cc["flowm3h"]]), "t_in": float(r[cc["tinc"]]), "t_out": float(r[cc["toutc"]])})
        except Exception:
            raise ValueError(f"Cooling sheet: circuit '{r[cc['circuit']]}' has a missing or non-numeric flow or temperature.")
    if not cool:
        raise ValueError("Cooling sheet has no circuit.")
    return {"month": M, "df": df, "cons_t": cons, "ctotal": ctot, "cooling": cool, "name": name or M["period"]}


def audit_export(A, path):
    """Audit results to Excel (path or a binary buffer): months table, fits, flags and heat terms."""
    from openpyxl.styles import Font, PatternFill
    fits_rows = [{"Item": t} for t in audit_fit_text(A)]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        A["months"].T.reset_index().rename(columns={"index": "Quantity"}).to_excel(xw, sheet_name="Months", index=False)
        pd.DataFrame(fits_rows or [{"Item": "not enough usable months for fits"}]).to_excel(xw, sheet_name="Fits", index=False)
        pd.DataFrame({"Flag": A["flags"] or ["none"]}).to_excel(xw, sheet_name="Flags", index=False)
        pd.DataFrame(HEAT_TERM_ROWS(A)).to_excel(xw, sheet_name="Heat terms", index=False)
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.fill, c.font = PatternFill("solid", start_color="1F3864", end_color="1F3864"), Font(bold=True, color="FFFFFF")
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 90)
    return path


def HEAT_TERM_ROWS(A):
    rows = []
    for r in A["results"]:
        row = {"Month": r["period"]}
        for k, v in r["D"].items():
            row[HEAT_TERM_LABELS[k]] = round(v / 1000.0, 1)
        row["Tuyere heat (measured blast)"] = round(r["supply_MJ"], 1)
        row["Residual (supply - demand)"] = round(r["residual_MJ"], 1)
        rows.append(row)
    return rows


# ---- synthetic plant month (self-tests and the template example) -------------------------------------------
def synthetic_month(sinter_pct=70.0, drr=0.40, blast_T=1000.0, hum=15.0, o2=21.0, hm_t=30000.0, hours=720.0,
                    noise=0.0, seed=0, label=None, eta=0.42):
    """A physically consistent month: burden from the model at this sinter share, a blast and top gas that satisfy the
    carbon, nitrogen and hot-zone heat balances for the chosen true DRR, cooling water equal to the losses that close
    the balance. The audit must recover the inputs; with noise > 0 the measurements are perturbed."""
    rng = np.random.default_rng(seed)
    SM0, H0 = dict(SINTER_MANUAL), copy.deepcopy(HEAT)
    try:
        SINTER_MANUAL.update(on=True, pct=float(sinter_pct))
        st, blend, cost, ach, _ = solve_mbf(plant_df, explain=False)
        if st != "Optimal":
            raise ValueError(f"synthetic month: burden at {sinter_pct}% sinter is {st}")
        df = plant_df
        Mnh = 0.85 * ach["Mn_kg"] / 10.0                                   # HM Mn % consistent with the model's Mn reduction
        HEAT.update(blast_temp_C=blast_T, blast_humidity_g_Nm3=hum, blast_O2_pct=o2, drr=drr, drr_per_pt_sinter=0.0, mode="check",
                    h_hm_kJ_kg=_hm_enthalpy(1470.0), h_slag_kJ_kg=_slag_enthalpy(1500.0))
        dust_c = 15.0 * 0.35
        h = heat_check(blend, df, ach)
        q = h["supply"]["kJ_per_kgC"]
        L = HEAT["loss_MJ_tHM"] + ((ach["FC_kg"] - dust_c) - h["C_need_kg"]) * q / 1000.0    # losses that close the balance
        HEAT["loss_MJ_tHM"] = L
        D, C = heat_terms(blend, df, ach["slag_kg"], ach["Sinter_share_pct"])
        c_tuy = sum(D.values()) / q
        y = o2 / 100.0
        V = (c_tuy / _MW["C"]) / (2.0 * y / 22.414 + hum / 1000.0 / 18.015)
        n_n2, n_h2 = V * (1 - y) / 22.414, V * hum / 1000.0 / 18.015
        carb_c = sum(blend[m] * (float(df.loc[m, "CaO"]) / 56.077 + float(df.loc[m, "MgO"]) / 40.304) * _MW["C"] / 100.0
                     for m in df.index if is_raw_flux(df, m))
        c_hm = HM_BASIS_KG * HM_C_PCT / 100.0
        c_gas = ach["FC_kg"] + carb_c - c_hm - dust_c
        n_gas = n_n2 + n_h2 + c_gas / _MW["C"]
        pct = lambda n: 100.0 * n / n_gas
        n_co2 = eta * c_gas / _MW["C"]
        n_co = c_gas / _MW["C"] - n_co2
        gn = lambda s: 1.0 + (noise * rng.standard_normal() if noise else 0.0) * s
        ga = lambda: (0.3 * rng.standard_normal() if noise else 0.0)
        per_h = hm_t / hours
        circuits = []
        for nm, share in (("Tuyere coolers", 0.40), ("Bosh and belly staves", 0.60)):
            dT = 6.0
            flow = L * share * per_h / (dT * 4.186)
            circuits.append({"name": nm, "hot": True, "flow": flow * gn(1), "t_in": 32.0, "t_out": 32.0 + dT})
        circuits.append({"name": "Upper staves and shell", "hot": False, "flow": 600.0 * gn(1), "t_in": 32.0, "t_out": 34.5})
        M = {"period": label or f"Synthetic {sinter_pct:g}%", "hm_t": hm_t, "hours": hours, "hm_temp_C": 1470.0, "hm_fe_pct": HM_FE_PCT,
             "hm_c_pct": HM_C_PCT, "hm_si_pct": HM_SI_PCT, "hm_mn_pct": Mnh,
             "blast_Nm3_min": V * hm_t / (60.0 * hours) * gn(1), "blast_temp_C": blast_T, "blast_h2o_g_Nm3": hum, "blast_o2_pct": o2,
             "tg_co_pct": pct(n_co) + ga(), "tg_co2_pct": pct(n_co2) + ga(), "tg_h2_pct": pct(n_h2), "tg_n2_pct": pct(n_n2),
             "tg_temp_C": 200.0, "tg_Nm3_h": None, "slag_t": ach["slag_kg"] * hm_t / 1000.0 * gn(1), "slag_temp_C": 1500.0,
             "dust_kg_thm": 15.0, "dust_c_pct": 35.0}
        used = [m for m in df.index if blend[m] > 1e-6]
        rec = {"month": M, "df": df.loc[used].copy(), "cons_t": {m: blend[m] / (1 - df.loc[m, "Moisture_Pct"] / 100.0) * hm_t / 1000.0 for m in used},
               "ctotal": {}, "cooling": circuits, "name": M["period"],
               "truth": {"DRR": drr, "loss_MJ": L, "sinter_pct": ach["Sinter_share_pct"], "fuel_rule": ach["Fuel_rule"]}}
    finally:
        SINTER_MANUAL.clear(); SINTER_MANUAL.update(SM0)
        HEAT.clear(); HEAT.update(H0)
    return rec


# ================================================================
# 6. OPTIMIZER
# ================================================================
def _solve_slice(df, bounds, beta=0.0, bgroups=None, relax=frozenset(), share=None):
    """One exact LP at a fixed sinter share (share, or the manual pinned share).
    relax: constraint names to leave out (limit diagnostics only).
    Heat 'floor' mode needs fuel = max(plant rule, heat-balance need), which is not convex. It is solved exactly as the
    cheaper of two LPs: (a) fuel = rule and carbon >= heat need, (b) carbon = heat need and fuel >= rule.
    Fuel can therefore never exceed the larger of the two (no burning extra coke just for its ash chemistry)."""
    if HEAT["mode"] != "floor":
        return _solve_slice_one(df, bounds, beta, bgroups, relax, share, None)
    if "Heat_balance_carbon" in relax:
        return _solve_slice_one(df, bounds, beta, bgroups, relax, share, "none")
    best = ("Infeasible", None, float("inf"))
    for variant in ("rule", "heat"):
        r_ = _solve_slice_one(df, bounds, beta, bgroups, relax, share, variant)
        if r_[0] == "Optimal" and r_[2] < best[2] - 1e-9:
            best = r_
    return best


def _solve_slice_one(df, bounds, beta, bgroups, relax, share, variant):
    p = pulp.LpProblem("MBF_v117", pulp.LpMinimize)

    def req(c, name):
        if name not in relax:
            p.addConstraint(c, name)

    x = {m: pulp.LpVariable(f"x{i}", lowBound=0.0, upBound=bounds[m][1]) for i, m in enumerate(df.index)}
    grp = lambda g: pulp.lpSum(x[m] for m in mats(df, g))
    ore, sinter = grp("Iron_ore"), grp("Sinter")
    coke, nut, pci = grp("Fuel_Coke"), grp("Fuel_NutCoke"), grp("Fuel_PCI")
    pin = float(share) if share is not None else (float(SINTER_MANUAL["pct"]) if SINTER_MANUAL["on"] else None)

    if pin is None:
        raise ValueError("_solve_slice needs a sinter share")
    if pin >= 100.0:
        p.addConstraint(ore == 0, "Sinter_share_pinned")          # all-sinter burden
    elif pin <= 0.0:
        p.addConstraint(sinter == 0, "Sinter_share_pinned")       # all-ore burden
    else:
        p.addConstraint(sinter == pin / (100.0 - pin) * ore, "Sinter_share_pinned")
    req(grp("Minor") <= MINOR_MAX_KGTHM, "Minor_total_cap")
    req(pci == PCI_FIXED_KGTHM, "PCI_fixed")
    if any(bounds[m][1] > 0 for m in mats(df, "Fuel_NutCoke")):
        if NUT_COKE_MODE == "fixed":
            req(nut == NUT_COKE_KGTHM, "Nut_coke_fixed")
        else:
            req(nut <= NUT_COKE_KGTHM, "Nut_coke_cap")
    fe_groups = None if FE_CLOSURE_BASIS == "all" else ["Iron_ore", "Sinter", "Minor"]
    p.addConstraint(chem_expr(x, df, "Fe", fe_groups) == REQUIRED_FE_KGTHM, "Fe_closure")

    si_in_hm = HM_BASIS_KG * HM_SI_PCT / 100.0
    sio2_red = si_in_hm / SIO2_TO_SI_CONST
    CaO, MgO = chem_expr(x, df, "CaO"), chem_expr(x, df, "MgO")
    SiO2, Al2O3 = chem_expr(x, df, "SiO2"), chem_expr(x, df, "Al2O3")
    req(SiO2 >= sio2_red + 1.0, "Enough_SiO2_for_Si")
    slag_SiO2 = SiO2 - sio2_red
    slag = CaO + MgO + slag_SiO2 + Al2O3
    req(CaO >= BASICITY_MIN * slag_SiO2, "B2_min")
    req(CaO <= BASICITY_MAX * slag_SiO2, "B2_max")
    req(MgO >= (MGO_MIN_PCT / 100) * slag, "MgO_min")
    req(MgO <= (MGO_MAX_PCT / 100) * slag, "MgO_max")
    req(Al2O3 >= (AL2O3_MIN_PCT / 100) * slag, "Al2O3_min")
    req(Al2O3 <= (AL2O3_MAX_PCT / 100) * slag, "Al2O3_max")

    Mn_reduced = MN_REDUCTION_EFF * chem_expr(x, df, "Mn")
    fc_floor = (DIRECT_REDUCTION_DEGREE * HM_FE_KGTHM * C_PER_KG_FE_DIRECT
                + si_in_hm * C_PER_KG_SI + Mn_reduced * C_PER_KG_MN + HM_BASIS_KG * HM_C_PCT / 100.0)
    req(chem_expr(x, df, "FC") >= fc_floor, "FC_stoich_floor")

    t_ = fuel_terms(x, df, slag, pin)
    if variant == "heat":                           # heat balance binding: carbon = need, fuel at or above the rule
        p.addConstraint(coke + nut + pci >= sum(t_.values()), "Fuel_rate_rule")
        p.addConstraint(chem_expr(x, df, "FC") == heat_carbon_need(x, df, slag, pin)[0], "Heat_balance_carbon")
    else:                                           # plant rule exactly (check mode = v11.5 behaviour)
        p.addConstraint(coke + nut + pci == sum(t_.values()), "Fuel_rate_rule")
        if variant == "rule":                       # floor mode, rule binding: carbon must still cover the heat need
            p.addConstraint(chem_expr(x, df, "FC") >= heat_carbon_need(x, df, slag, pin)[0], "Heat_balance_carbon")

    if beta > 0 and bgroups:
        for g, shares in bgroups.items():
            tot = pulp.lpSum(x[m] for m in shares)
            for m, sh in shares.items():
                p.addConstraint(x[m] >= beta * sh * tot, f"StockBalance_{g}_{m}")
    if MIN_SHARE_MULTI_SOURCE > 0:
        for g in MIN_SHARE_GROUPS:
            av = [m for m in mats(df, g) if bounds[m][1] > 0]
            if len(av) >= 2:
                tot = pulp.lpSum(x[m] for m in av)
                for m in av:
                    p.addConstraint(x[m] >= MIN_SHARE_MULTI_SOURCE * tot, f"MinShare_{g}_{m}")

    cost = pulp.lpSum(x[m] * eff_price(df, m) / 1000 for m in df.index)
    p.setObjective(cost + (MINOR_PENALTY_RS_PER_KG * grp("Minor") if MINOR_DEMAND_ONLY else 0))
    status = pulp.LpStatus[p.solve(SOLVER)]
    if status != "Optimal":
        return status, None, float("inf")
    blend = {m: max(0.0, float(x[m].value() or 0.0)) for m in df.index}
    return status, blend, float(pulp.value(p.objective))


def _share_search(df, bounds, beta, bgroups, relax=frozenset()):
    """FREE sinter:ore choice. Exact LP at every sinter share on a coarse grid across the guard rails, then a fine
    grid around the best point. Returns the least-cost blend (None if no share is feasible)."""
    wide = "Sinter_min_share" in relax
    lo, hi = (1.0, 99.0) if wide else (SINTER_MIN * 100.0, SINTER_MAX * 100.0)
    step = 2.0 if wide else SHARE_GRID_STEP
    pts = [float(v) for v in np.arange(lo, hi + 1e-9, step)]
    if pts[-1] < hi - 1e-9:
        pts.append(hi)
    cache = {}

    def f(S):
        key = round(S, 6)
        if key not in cache:
            st, bl, c = _solve_slice(df, bounds, beta, bgroups, relax, share=S)
            cache[key] = (c, bl)
        return cache[key][0]

    vals = [f(S) for S in pts]
    if not np.isfinite(vals).any():
        return None
    i = int(np.argmin(vals))
    a_, b_ = max(lo, pts[i] - step), min(hi, pts[i] + step)
    for S in np.arange(a_, b_ + 1e-9, SHARE_REFINE_STEP):
        f(float(S))
    best = min(cache, key=lambda k: cache[k][0])
    return cache[best][1]


def _search(df, bounds, beta, bgroups, relax=frozenset()):
    """Least-cost solution for one stock-balance level (beta), whatever the sinter mode."""
    if SINTER_MANUAL["on"]:
        st, bl, c = _solve_slice(df, bounds, beta, bgroups, relax)
        return bl if st == "Optimal" else None
    return _share_search(df, bounds, beta, bgroups, relax)


def _find_blend(df, bounds, bgroups, b0, relax=frozenset()):
    betas = sorted({b0, 0.75 * b0, 0.5 * b0, 0.25 * b0, 0.0}, reverse=True) if (bgroups and b0 > 0) else [0.0]
    for beta in betas:
        bl = _search(df, bounds, beta, bgroups, relax)
        if bl is not None:
            return bl, beta
    return None, None


# ---- LIMIT DIAGNOSTICS -------------------------------------------------------------------------
RELAX_FAMILIES = {
    "B2 cap": ["B2_max"], "B2 floor": ["B2_min"],
    "MgO floor": ["MgO_min"], "MgO cap": ["MgO_max"],
    "Al2O3 floor": ["Al2O3_min"], "Al2O3 cap": ["Al2O3_max"],
    "nut coke rate": ["Nut_coke_fixed", "Nut_coke_cap"], "PCI rate": ["PCI_fixed"],
    "carbon floor": ["FC_stoich_floor"], "SiO2 needed for Si": ["Enough_SiO2_for_Si"],
    "Sinter:Ore guard rails": ["Sinter_min_share", "Sinter_max_share"],
    "heat balance": ["Heat_balance_carbon"],
}


def _family_live(name):
    return name != "heat balance" or HEAT["mode"] == "floor"


def _feasible_share(df, pct, bounds, bgroups, b0, relax=frozenset()):
    SINTER_MANUAL.update(on=True, pct=float(pct))
    return _find_blend(df, bounds, bgroups, b0, relax)[0] is not None


def sinter_range(df, step=0.5):
    """Lowest and highest feasible sinter share (%) inside the guard rails. {'lo','hi','gaps'} or None."""
    saved = dict(SINTER_MANUAL)
    try:
        bounds = get_bounds(df); bg = balance_groups(df, bounds); b0 = float(STOCK["balance"])
        a, b = SINTER_MIN * 100, SINTER_MAX * 100
        iters = 10
        pts = [float(v) for v in np.arange(a, b + 1e-9, step)]
        if pts[-1] < b - 1e-9:
            pts.append(b)
        ok = [_feasible_share(df, p_, bounds, bg, b0) for p_ in pts]
        if not any(ok):
            return None
        lo_i, hi_i = ok.index(True), len(ok) - 1 - ok[::-1].index(True)
        lo, hi = pts[lo_i], pts[hi_i]
        if hi_i < len(pts) - 1:
            l_, h_ = pts[hi_i], pts[hi_i + 1]
            for _ in range(iters):
                m_ = (l_ + h_) / 2
                l_, h_ = (m_, h_) if _feasible_share(df, m_, bounds, bg, b0) else (l_, m_)
            hi = l_
        if lo_i > 0:
            l_, h_ = pts[lo_i - 1], pts[lo_i]
            for _ in range(iters):
                m_ = (l_ + h_) / 2
                l_, h_ = (l_, m_) if _feasible_share(df, m_, bounds, bg, b0) else (m_, h_)
            lo = h_
        return {"lo": lo, "hi": hi, "gaps": not all(ok[lo_i:hi_i + 1])}
    finally:
        SINTER_MANUAL.clear(); SINTER_MANUAL.update(saved)


def sinter_limiters(df, pct):
    """Which limits, if relaxed on their own, would make this pinned sinter share feasible?"""
    saved = dict(SINTER_MANUAL)
    try:
        bounds = get_bounds(df)
        SINTER_MANUAL.update(on=True, pct=float(pct))
        return [name for name, cons in RELAX_FAMILIES.items() if name != "Sinter:Ore guard rails" and _family_live(name)
                and _search(df, bounds, 0.0, {}, frozenset(cons)) is not None]
    finally:
        SINTER_MANUAL.clear(); SINTER_MANUAL.update(saved)


def sinter_limit_note(df, rng=None):
    top, bot = SINTER_MAX * 100, SINTER_MIN * 100
    rng = sinter_range(df) if rng is None else rng
    if rng is None:
        return f"NOTE: no sinter share inside the {bot:g}-{top:g}% guard rails is feasible on these inputs."
    lo, hi = rng["lo"], rng["hi"]
    if lo <= bot + 0.05 and hi >= top - 0.05 and not rng["gaps"]:
        return None
    bits = [f"NOTE: on these inputs sinter can only run from {lo:.1f}% to {hi:.1f}% (guard rails {bot:g}-{top:g}%)."]
    if hi < top - 0.05:
        L = sinter_limiters(df, min(hi + 0.3, top))
        bits.append(f"Above {hi:.1f}% it is blocked by: {', '.join(L) if L else 'several limits at once'}.")
    if lo > bot + 0.05:
        L = sinter_limiters(df, max(lo - 0.3, bot))
        bits.append(f"Below {lo:.1f}% it is blocked by: {', '.join(L) if L else 'several limits at once'}.")
    if rng["gaps"]:
        bits.append("Feasibility is not continuous inside that range.")
    return " ".join(bits)


def _band_reach_note(df):
    saved = dict(SINTER_MANUAL)
    try:
        bounds = get_bounds(df); bg = balance_groups(df, bounds); b0 = float(STOCK["balance"])
        ends_ok = all(_feasible_share(df, p_, bounds, bg, b0) for p_ in (SINTER_MIN * 100, SINTER_MAX * 100))
    finally:
        SINTER_MANUAL.clear(); SINTER_MANUAL.update(saved)
    return None if ends_ok else sinter_limit_note(df)


def sinter_decision_note(df, s0, c0, step=0.5):
    """Plain-language answer to 'what decided the sinter share?' for a free-choice run: for one step up and one
    step down it says whether a guard rail stops it, a chemistry limit blocks it, or it simply costs more."""
    if SINTER_MANUAL["on"]:
        return None
    lo, hi = SINTER_MIN * 100, SINTER_MAX * 100
    saved = dict(SINTER_MANUAL)
    parts, blocked, dearer = [], [], []
    try:
        for d_, word in ((+1, "Higher"), (-1, "Lower")):
            if (d_ > 0 and s0 >= hi - 0.05) or (d_ < 0 and s0 <= lo + 0.05):
                parts.append(f"{word} sinter: stopped by the {hi if d_ > 0 else lo:g}% guard rail")
                blocked.append("guard rail")
                continue
            s1 = min(max(s0 + d_ * step, lo), hi)
            SINTER_MANUAL.update(on=True, pct=float(s1))
            st, _, c1, _, _ = solve_mbf(df, explain=False)
            SINTER_MANUAL.clear(); SINTER_MANUAL.update(saved)
            if st != "Optimal":
                L = sinter_limiters(df, s1)
                parts.append(f"{word} sinter ({s1:.1f}%) is blocked by: {', '.join(L) if L else 'several limits at once'}")
                blocked.append("chemistry")
            else:
                parts.append(f"{word} sinter ({s1:.1f}%) costs {c1 - c0:+,.2f} Rs/tHM")
                dearer.append(word)
    finally:
        SINTER_MANUAL.clear(); SINTER_MANUAL.update(saved)
    if len(dearer) == 2:
        why = "a true optimum - the cost balance between burden price and fuel decides it"
    elif "guard rail" in blocked:
        why = "a guard rail - the optimizer would go further if allowed"
    elif "chemistry" in blocked:
        why = "a chemistry limit on one side, cost on the other"
    else:
        why = "limits on both sides"
    return f"SINTER DECISION: {s0:.2f}% is set by {why}. " + "; ".join(parts) + "."


def input_notes(df, ach):
    """Plain-language checks on the INPUTS that most often explain a surprising result."""
    notes = []
    on = lambda g: [m for m in mats(df, g) if bool(df.loc[m, "Available"])]
    ores, sinters = on("Iron_ore"), on("Sinter")
    per_tfe = lambda m: eff_price(df, m) / (float(df.loc[m, "Fe"]) / 100.0) if float(df.loc[m, "Fe"]) > 0 else float("inf")
    if ores:
        cheapest = min(eff_price(df, m) for m in ores)
        for m in on("Flux"):
            if eff_price(df, m) > cheapest:
                notes.append(f"CHECK: {m} is priced at Rs {eff_price(df, m):,.0f}/t, above the cheapest iron ore (Rs {cheapest:,.0f}/t). "
                             f"Flux normally costs a fraction of ore - please confirm the price (a typo?).")
    if ores and sinters:
        s_t, o_t = min(per_tfe(m) for m in sinters), min(per_tfe(m) for m in ores)
        if s_t > 1.25 * o_t:
            coke_p = min([eff_price(df, m) for m in on("Fuel_Coke")] or [0.0]) / 1000.0
            notes.append(f"NOTE: sinter costs Rs {s_t:,.0f} per tonne of contained Fe against Rs {o_t:,.0f} for the cheapest ore ({s_t / o_t - 1:.0%} more). "
                         f"The plant rule saves {10 * FUEL_PER_PCT_SINTER:g} kg fuel per +10 pts of sinter (about Rs {10 * FUEL_PER_PCT_SINTER * coke_p:,.0f} "
                         f"at your coke price), so price pushes the optimizer towards less sinter.")
    ext = float(ach["Fuel_terms"].get("other_moisture", 0.0))
    if ext > 1.0:
        notes.append(f"NOTE: the sinter/flux moisture rule (an extension - there is no plant rule for it) adds {ext:.1f} kg fuel in this run. "
                     f"Check the sinter moisture entered, or switch the rule off under Inputs > Fuel-rate rules.")
    return notes


def explain_infeasible(df, bounds):
    fam = [name for name, cons in RELAX_FAMILIES.items()
           if not (name == "Sinter:Ore guard rails" and SINTER_MANUAL["on"]) and _family_live(name)
           and _search(df, bounds, 0.0, {}, frozenset(cons)) is not None]
    if fam:
        return ["Feasible if any ONE of these limits is relaxed (they are what blocks a solution): " + ", ".join(fam) + "."]
    return ["No single limit explains it - several limits conflict at once (check the assays; prices are irrelevant here)."]


def solve_mbf(df, explain=True):
    errs = validate_df(df)
    if errs:
        return "INPUT_ERROR", None, None, None, errs
    if SINTER_MANUAL["on"] and not (0.0 <= float(SINTER_MANUAL["pct"]) <= 100.0):
        return "INPUT_ERROR", None, None, None, ["Manual sinter share must be between 0 and 100."]

    bounds = get_bounds(df)
    on = lambda g: [m for m in mats(df, g) if bounds[m][1] > 0]
    problems = []
    if not on("Iron_ore") or not on("Sinter"):
        problems.append("The sinter:ore choice needs at least one Iron_ore AND one Sinter material available "
                        "(toggled ON and with stock > 0).")
    if not on("Fuel_Coke"):
        problems.append("No regular coke is available (toggled ON and with stock > 0); regular coke is the residual fuel.")
    if not on("Fuel_PCI"):
        problems.append("No PCI material is available to supply the fixed PCI requirement.")
    if problems:
        return "NO_PRODUCTION", None, None, None, problems

    bgroups = balance_groups(df, bounds)
    b0 = float(STOCK["balance"])
    blend, beta_used = _find_blend(df, bounds, bgroups, b0)

    if blend is None:
        msg = ["No feasible blend under the current chemistry, Fe closure, fuel-rate rule, sinter:ore guard rails, "
               "nut coke rate, availability and stock.",
               "Typical causes: B2/MgO/Al2O3 cannot be met by the available materials together, "
               "the fixed PCI + nut coke exceed the fuel the rule allows, or the SiO2 supply is too low."]
        if float(STOCK["plan_hm_tonnes"]) > 0:
            free = _search(df, get_bounds(df, use_stock_caps=False), 0.0, {})
            if free is not None:
                msg.insert(0, "STOCK is the limiting factor: a blend exists without stock caps, but the stock on hand "
                              "cannot cover the planned hot metal tonnage. Increase RM_Stock or lower the plan.")
        if explain:
            if SINTER_MANUAL["on"]:
                rng = sinter_range(df)
                pm = float(SINTER_MANUAL["pct"])
                if rng is None or not (rng["lo"] - 1e-9 <= pm <= rng["hi"] + 1e-9):
                    msg.append(f"The pinned sinter share {pm:g}% is not feasible on these inputs.")
                note = sinter_limit_note(df, rng)
                if note:
                    msg.append(note)
            msg.extend(explain_infeasible(df, bounds))
        return "Infeasible", None, None, None, msg

    ach = calc_achieved(blend, df)
    diag = ["OPTIMAL BLEND FOUND.", (f"Minimum raw-material cost: Rs {ach['Cost_Rs_tHM']:,.2f}/tHM" if OM_RS_THM <= 0 else
            f"Minimum cost: Rs {ach['Cost_Rs_tHM']:,.2f}/tHM (raw materials Rs {ach['Raw_cost_Rs_tHM']:,.2f} + O&M Rs {OM_RS_THM:,.2f})")]
    off = rules_off()
    if off:
        diag.append("THUMB RULES SWITCHED OFF: " + ", ".join(off) + " - the fuel rate and cost exclude them (not the plant rule set).")
    if SINTER_MANUAL["on"]:
        pm = float(SINTER_MANUAL["pct"])
        diag.append(f"MANUAL sinter share: {pm:g}% (pinned; the optimizer did not choose it).")
        if not (SINTER_MIN * 100 - 1e-9 <= pm <= SINTER_MAX * 100 + 1e-9):
            diag.append(f"NOTE: {pm:g}% is outside the {SINTER_MIN*100:.0f}-{SINTER_MAX*100:.0f}% guard rails - sensitivity use only.")
    if bgroups and b0 > 0:
        if beta_used < b0 - 1e-9:
            diag.append(f"NOTE: stock balancing relaxed from {b0*100:.0f}% to {beta_used*100:.0f}% - the chemistry / stock "
                        f"caps could not be met at the full setting.")
        else:
            diag.append(f"Stock balance: {beta_used*100:.0f}% (usage split by stock within {', '.join(bgroups)}).")
    skipped = [g for g in BALANCE_GROUPS if len(on(g)) >= 2 and g not in bgroups]
    if skipped:
        diag.append(f"NOTE: RM_Stock is missing for some ON materials in {', '.join(skipped)} - stock balancing skipped there.")
    zero = [m for m in df.index if bool(df.loc[m, "Available"]) and pd.notna(df.loc[m, "RM_Stock"]) and float(df.loc[m, "RM_Stock"]) <= 0]
    if zero:
        diag.append(f"NOTE: zero stock, treated as unavailable: {', '.join(zero)}.")
    gap = ach["Fuel_supplied"] - ach["Fuel_rule"]
    if HEAT["mode"] == "floor" and gap > 0.05:
        diag.append(f"HEAT BALANCE FLOOR: the hot-zone heat balance needs {gap:.2f} kg/tHM more fuel than the plant rule - "
                    "coke raised to cover it.")
    elif abs(gap) > 0.05:
        diag.append(f"WARNING: fuel supplied differs from the plant rule by {gap:+.2f} kg/tHM - please report this.")
    diag.append(heat_note(ach["Heat"]))
    if not (FE_C_RATIO_TARGET - FE_C_RATIO_TOL - 1e-9 <= ach["Fe_C_ratio"] <= FE_C_RATIO_TARGET + FE_C_RATIO_TOL + 1e-9):
        diag.append(f"NOTE: Fe/C = {ach['Fe_C_ratio']:.2f} is outside the {FE_C_RATIO_TARGET}+/-{FE_C_RATIO_TOL} guide band (diagnostic only).")
    ma = ach["MgO_Al2O3"]
    if not (MGO_AL2O3_GUIDE[0] - 1e-9 <= ma <= MGO_AL2O3_GUIDE[1] + 1e-9):
        diag.append(f"NOTE: MgO/Al2O3 = {ma:.2f} is outside the {MGO_AL2O3_GUIDE[0]:g}-{MGO_AL2O3_GUIDE[1]:g} guide for slag drainage (diagnostic only).")
    if ach["Raw_flux_kg"] > RAW_FLUX_NOTE_KGTHM:
        diag.append(f"NOTE: {ach['Raw_flux_kg']:.1f} kg/tHM of raw limestone/dolomite is charged directly to the furnace "
                    f"(its fuel cost is in the rule at {FUEL_PER_KG_RAW_FLUX:g} kg per kg).")
    if ach["slag_kg"] > SLAG_REF_KGTHM + SLAG_NOTE_MARGIN_KG:
        diag.append(f"NOTE: slag {ach['slag_kg']:.0f} kg/tHM vs {SLAG_REF_KGTHM:g} reference. The burden brings {ach['Al2O3_kg']:.1f} kg "
                    f"Al2O3/tHM, which alone needs at least {ach['Slag_min_for_Al2O3_cap']:.0f} kg slag at the {AL2O3_MAX_PCT:g}% Al2O3 cap.")
    minors_on = on("Minor")
    if ach["Minor_kg"] > 1e-6:
        why = "required to meet the limits" if MINOR_DEMAND_ONLY else "chosen by cost"
        diag.append(f"NOTE: Minor materials in use ({ach['Minor_kg']:.1f} kg/tHM) - {why}.")
    elif minors_on:
        diag.append(f"Minor materials available but not needed: {', '.join(minors_on)}.")
    if explain and not SINTER_MANUAL["on"]:
        note = sinter_decision_note(df, ach["Sinter_share_pct"], ach["Cost_Rs_tHM"])
        if note:
            diag.insert(2, note)
        reach = _band_reach_note(df)
        if reach:
            diag.append(reach)
    if explain:
        diag.extend(input_notes(df, ach))
    return "Optimal", blend, ach["Cost_Rs_tHM"], ach, diag


# ================================================================
# 7. RESULT TABLES + SENSITIVITY STUDIO
# ================================================================

def _fdf(df):
    """Working copy with every numeric input as a decimal (sensitivity steps write decimals into it)."""
    d = df.copy()
    d[NUM_COLS] = d[NUM_COLS].astype(float)
    for c in OPT_COLS:
        if c in d.columns:
            d[c] = d[c].astype(float)
    return d


def _row_summary(a, c):
    """Common result columns for every sensitivity table."""
    fg = fuel_term_groups(a["Fuel_terms"])
    return {"Cost Rs/tHM": round(c, 1), "Sinter %": round(a["Sinter_share_pct"], 2),
            "Ore kg": round(a["Ore_kg"], 1), "Sinter kg": round(a["Sinter_kg"], 1), "Coke kg": round(a["Coke_kg"], 1),
            "Fuel kg": round(a["Fuel_supplied"], 2), "Raw flux kg": round(a["Raw_flux_kg"], 1), "Flux kg": round(a["Flux_kg"], 1),
            "Slag kg": round(a["slag_kg"], 1), "Burden Fe %": round(a["Burden_Fe_pct"], 2),
            "Slag term": round(fg["slag"], 2), "Fe terms": round(fg["fe"], 2), "Sinter term": round(fg["sinter"], 2),
            "Raw flux term": round(fg["raw_flux"], 2), "Moisture terms": round(fg["moisture"], 2),
            "Sinter/flux moisture": round(fg["moisture_ext"], 2), "B2": round(a["B2"], 3), "MgO %": round(a["MgO_pct"], 2),
            "Al2O3 %": round(a["Al2O3_pct"], 2), "MgO/Al2O3": round(a["MgO_Al2O3"], 3), "Fe/C": round(a["Fe_C_ratio"], 3),
            "S % (pred)": round(a["S_HM_pct"], 4),
            "Heat-min fuel kg": round(a["Heat"]["fuel_min_kg"], 1), "Heat C surplus kg": round(a["Heat"]["C_surplus_kg"], 1)}


def sinter_sweep(df, start=None, end=None, step=1.0, with_oxides=False, progress=None):
    """Model re-solved at fixed sinter shares from start..end (%), plus the optimizer's free choice.
    with_oxides=True also returns a long table of CaO / SiO2 / Al2O3 / MgO kg by material at every feasible point."""
    start = SINTER_MIN * 100 if start is None else float(start)
    end = SINTER_MAX * 100 if end is None else float(end)
    step = float(step)
    if step <= 0 or end < start:
        raise ValueError("Sweep needs step > 0 and end >= start.")
    pts = list(np.arange(start, end + 1e-9, step))
    if len(pts) > 80:
        raise ValueError("Sweep limited to 80 points - use a larger step.")
    saved = dict(SINTER_MANUAL)
    rows, free_cost, ox_rows = [], None, []

    def add(label, res):
        nonlocal free_cost
        st, b, c, a, dg = res
        lab = label if isinstance(label, str) else round(label, 2)
        if st == "Optimal":
            for m, v in b.items():
                if v > 1e-7:
                    r_ = {"Point": lab, "Sinter %": round(a["Sinter_share_pct"], 2), "Material": m, "Group": df.loc[m, "Group"], "kg/tHM": round(v, 2)}
                    for ox in OXIDES:
                        r_[f"{ox} kg"] = round(v * float(df.loc[m, ox]) / 100.0, 3)
                    ox_rows.append(r_)
            bal_used = np.nan
            for line_ in dg:
                m_ = re.search(r"Stock balance: (\d+)%", line_) or re.search(r"relaxed from \d+% to (\d+)%", line_)
                if m_:
                    bal_used = float(m_.group(1))
            if label == "optimizer's choice":
                free_cost = c
            row = {"Sinter share %": lab, "Status": st, "Stock balance %": bal_used}
            row.update(_row_summary(a, c))
            row["vs free choice Rs"] = round(c - free_cost, 1) if free_cost is not None else np.nan
            rows.append(row)
        else:
            rows.append({"Sinter share %": lab, "Status": st})

    try:
        SINTER_MANUAL["on"] = False
        add("optimizer's choice", solve_mbf(df, explain=False))
        for i_, p_ in enumerate(pts, start=1):
            SINTER_MANUAL.update(on=True, pct=float(p_))
            add(float(p_), solve_mbf(df, explain=False))
            if progress:
                progress(i_, len(pts))
    finally:
        SINTER_MANUAL.clear(); SINTER_MANUAL.update(saved)
    out = pd.DataFrame(rows)
    bad = out["Status"] != "Optimal"
    if bad.any():
        rng = sinter_range(df)
        out["Note"] = ""
        for i in out.index[bad]:
            lab = rows[i]["Sinter share %"]
            if rng is None:
                out.loc[i, "Note"] = "no feasible sinter share on these inputs"
            elif isinstance(lab, (int, float)) and lab > rng["hi"]:
                out.loc[i, "Note"] = f"above the feasible limit ({rng['hi']:.1f}%)"
            elif isinstance(lab, (int, float)) and lab < rng["lo"]:
                out.loc[i, "Note"] = f"below the feasible range ({rng['lo']:.1f}%)"
            else:
                out.loc[i, "Note"] = "infeasible"
        out.attrs["note"] = sinter_limit_note(df, rng) or ""
    if with_oxides:
        return out, pd.DataFrame(ox_rows)
    return out


def oxides_by_sinter_pivot(ox_long, oxide):
    """Rows = sinter point, columns = material, values = kg of one oxide charged per tHM (plus TOTAL)."""
    if ox_long is None or len(ox_long) == 0:
        return None
    pv = ox_long.pivot_table(index=["Point", "Sinter %"], columns="Material", values=f"{oxide} kg", aggfunc="sum", fill_value=0.0, sort=False)
    pv = pv.loc[:, (pv.abs() > 1e-6).any(axis=0)]
    order = {g_: i for i, g_ in enumerate(GROUPS)}
    grp = ox_long.drop_duplicates("Material").set_index("Material")["Group"]
    pv = pv[sorted(pv.columns, key=lambda m: (order.get(str(grp.get(m, "")), 99), m))]
    pv["TOTAL"] = pv.sum(axis=1)
    out = pv.reset_index()
    out.columns.name = None
    return out.round(2)


CHART_OXIDES = ["CaO", "SiO2", "Al2O3"]





CURVE_STEP = 2.5          # points of sinter share between curve points (0 to 100%)


def sinter_curve(df, step=None, progress=None):
    """The model re-solved with the sinter share pinned at every step from 0% to 100% sinter.
    Infeasible points are kept (blank results) and the feasible edges are located to 0.1 pt with the limit that stops them."""
    step = float(step or CURVE_STEP)
    pts = [round(float(v), 4) for v in np.arange(0.0, 100.0 + 1e-9, step)]
    if pts[-1] < 100.0:
        pts.append(100.0)
    saved = dict(SINTER_MANUAL)
    rows = []

    def one(s):
        SINTER_MANUAL.update(on=True, pct=float(s))
        return solve_mbf(df, explain=False)

    try:
        for i_, s in enumerate(pts, start=1):
            st, b, c, a, _ = one(s)
            if progress:
                progress(i_, len(pts))
            if st == "Optimal":
                acid = sum(float(b[m]) for m in mats(df, "Flux") if not is_raw_flux(df, m))
                rows.append({"Sinter %": s, "Status": st, "Cost Rs/tHM": round(c, 1), "Coke kg": round(a["Coke_kg"], 2),
                             "Total fuel kg": round(a["Fuel_supplied"], 2), "Ore kg": round(a["Ore_kg"], 1), "Sinter kg": round(a["Sinter_kg"], 1),
                             "Raw flux kg": round(a["Raw_flux_kg"], 1), "Acid flux kg": round(acid, 1), "Slag kg": round(a["slag_kg"], 1),
                             "B2": round(a["B2"], 3), "Al2O3 %": round(a["Al2O3_pct"], 2), "MgO %": round(a["MgO_pct"], 2),
                             "Heat-min coke kg": round(a["Heat"]["coke_min_kg"], 2), "Heat-min fuel kg": round(a["Heat"]["fuel_min_kg"], 2),
                             "Heat C surplus kg": round(a["Heat"]["C_surplus_kg"], 2)})
            else:
                rows.append({"Sinter %": s, "Status": st})
        out = pd.DataFrame(rows)
        feas = out["Status"] == "Optimal"
        edges = []
        if feas.any():
            fi = list(out.index[feas])
            lo_i, hi_i = fi[0], fi[-1]

            def edge(a_ok, b_bad):
                for _ in range(10):
                    m_ = (a_ok + b_bad) / 2
                    if one(m_)[0] == "Optimal":
                        a_ok = m_
                    else:
                        b_bad = m_
                return a_ok, b_bad
            if hi_i < len(out) - 1:
                ok_, bad_ = edge(pts[hi_i], pts[hi_i + 1])
                L = sinter_limiters(df, bad_)
                edges.append(("hi", ok_, L))
            if lo_i > 0:
                ok_, bad_ = edge(pts[lo_i], pts[lo_i - 1])
                L = sinter_limiters(df, bad_)
                edges.append(("lo", ok_, L))
            gaps = not feas.iloc[lo_i:hi_i + 1].all()
            lo_v = next((e[1] for e in edges if e[0] == "lo"), pts[lo_i])
            hi_v = next((e[1] for e in edges if e[0] == "hi"), pts[hi_i])
        else:
            gaps, lo_v, hi_v = False, None, None
    finally:
        SINTER_MANUAL.clear(); SINTER_MANUAL.update(saved)
    out.attrs["lo"], out.attrs["hi"], out.attrs["edges"], out.attrs["gaps"] = lo_v, hi_v, edges, gaps
    if lo_v is None:
        out.attrs["note"] = "No sinter share from 0% to 100% is feasible on these inputs."
    else:
        bits = [f"Feasible from {lo_v:.1f}% to {hi_v:.1f}% sinter."]
        for side, v, L in edges:
            bits.append(f"{'Above' if side == 'hi' else 'Below'} {v:.1f}% it is blocked by: {', '.join(L) if L else 'several limits at once'}.")
        if gaps:
            bits.append("Feasibility is not continuous inside that range.")
        out.attrs["note"] = " ".join(bits)
    out.attrs["heat_note"] = heat_sinter_slope(out)
    return out



def compact_oxide_table(ox_table, oxides=CHART_OXIDES):
    """Material rows with kg and % of total for CaO, SiO2 and Al2O3 only, plus the total."""
    body = ox_table[~ox_table["Material"].isin(["Less: SiO2 reduced to Si in hot metal", "SLAG"])]
    cols = ["Material", "Group"]
    for o in oxides:
        cols += [f"{o} kg", f"{o} % of total"]
    t = body[cols].copy()
    return t[(t[[f"{o} kg" for o in oxides]].abs().sum(axis=1) > 1e-9) | (t["Material"] == "TOTAL CHARGED")].reset_index(drop=True)



ASSAY_COLUMNS = ("Fe", "SiO2", "Al2O3", "CaO", "MgO", "Moisture_Pct")


def assay_sensitivity(df, group="Iron_ore", column="Fe", deltas=(-2, -1, 0, 1, 2)):
    """Shift `column` of every ON material in `group` by each delta (points) and re-solve."""
    if group not in GROUPS:
        raise ValueError(f"group must be one of {GROUPS}.")
    if column not in ASSAY_COLUMNS:
        raise ValueError(f"column must be one of {ASSAY_COLUMNS}.")
    if not any(bool(df.loc[m, "Available"]) for m in mats(df, group)):
        raise ValueError(f"No {group} material is toggled ON.")
    deltas = sorted(dict.fromkeys([0.0] + [float(x) for x in deltas]))
    rows, base = [], None
    for dlt in deltas:
        d = _fdf(df)
        for m in mats(d, group):
            if bool(d.loc[m, "Available"]):
                d.loc[m, column] = max(0.0, float(d.loc[m, column]) + dlt)
        st, b, c, a, _ = solve_mbf(d, explain=False)
        row = {"Change (pts)": dlt, "Status": st}
        if st == "Optimal":
            row.update(_row_summary(a, c))
            if dlt == 0.0:
                base = (c, a["Fuel_supplied"])
        rows.append(row)
    out = pd.DataFrame(rows)
    if base is not None and "Cost Rs/tHM" in out.columns:
        out.insert(out.columns.get_loc("Cost Rs/tHM") + 1, "Cost vs base Rs", (out["Cost Rs/tHM"] - base[0]).round(1))
        out.insert(out.columns.get_loc("Fuel kg") + 1, "Fuel vs base kg", (out["Fuel kg"] - base[1]).round(2))
    return out


def fe_sensitivity(df, group="Iron_ore", deltas=(-2, -1, 0, 1, 2)):
    return assay_sensitivity(df, group, "Fe", deltas)


def moisture_sensitivity(df, group="Iron_ore", deltas=(-2, -1, 0, 1, 2)):
    return assay_sensitivity(df, group, "Moisture_Pct", deltas)


def price_targets(df):
    """Choices for the price sensitivity: every ON material, then every group with an ON material."""
    ms = [m for m in df.index if bool(df.loc[m, "Available"])]
    gs = [f"Group: {g}" for g in GROUPS if any(bool(df.loc[m, "Available"]) for m in mats(df, g))]
    return ms + gs


def price_sensitivity(df, target, pct_changes=(-20, -10, 0, 10, 20)):
    """Change the price of one material (or every ON material of a group: 'Group: Sinter') by each % and re-solve."""
    if str(target).startswith("Group: "):
        g = str(target)[7:]
        ms = [m for m in mats(df, g) if bool(df.loc[m, "Available"])]
    else:
        ms = [target] if target in df.index else []
    if not ms:
        raise ValueError(f"Nothing to change for '{target}' (unknown or switched OFF).")
    pcts = sorted(dict.fromkeys([0.0] + [float(x) for x in pct_changes]))
    rows, base = [], None
    for pc in pcts:
        d = _fdf(df)
        for m in ms:
            d.loc[m, "Price_Rs_t"] = max(0.01, float(df.loc[m, "Price_Rs_t"]) * (1 + pc / 100.0))
        st, b, c, a, _ = solve_mbf(d, explain=False)
        row = {"Price change %": pc, "Price used Rs/t": round(float(d.loc[ms[0], "Price_Rs_t"]), 1) if len(ms) == 1 else "group", "Status": st}
        if st == "Optimal":
            row.update(_row_summary(a, c))
            if pc == 0.0:
                base = c
        rows.append(row)
    out = pd.DataFrame(rows)
    if base is not None and "Cost Rs/tHM" in out.columns:
        out.insert(out.columns.get_loc("Cost Rs/tHM") + 1, "Cost vs base Rs", (out["Cost Rs/tHM"] - base).round(1))
    out.attrs["note"] = f"Changed: {', '.join(ms)}. Every other input as applied."
    return out


# ---- TORNADO: every driver moved one at a time ----------------------------------------------------
def _driver_list(df, swing_pct):
    D = []
    for m in df.index:
        if bool(df.loc[m, "Available"]) and float(df.loc[m, "Price_Rs_t"]) > 0:
            D.append((f"Price: {m}", "Price", ("price", m), "pct", swing_pct))
    D.append((f"Base fuel ({FURNACE['name']})", "Fuel rule", ("base",), "pct", swing_pct))
    for name, lab, term in (("FUEL_PER_PCT_SINTER", "Sinter-share rule (kg/pt)", "sinter_share"),
                            ("FUEL_PER_KG_RAW_FLUX", "Raw flux rule (kg/kg)", "raw_flux"),
                            ("FUEL_PER_KG_SLAG", "Slag rule (kg/kg)", "slag")):
        if term_active(term):
            D.append((lab, "Fuel rule", ("global", name), "pct", swing_pct))
    for r in MATERIAL_RULES:
        if term_active(r):
            D.append((f"{TERM_LABELS.get(r, r)} rule (coef)", "Fuel rule", ("rule", r), "pct", swing_pct))
    D += [("Al2O3 cap (% of slag)", "Limit", ("global", "AL2O3_MAX_PCT"), "abs", 0.5),
          ("MgO cap (% of slag)", "Limit", ("global", "MGO_MAX_PCT"), "abs", 0.5),
          ("B2 floor", "Limit", ("global", "BASICITY_MIN"), "abs", 0.01),
          ("B2 cap", "Limit", ("global", "BASICITY_MAX"), "abs", 0.01),
          ("Fe required per 100 kg HM", "Limit", ("fe",), "abs", 0.5)]
    return D


def _get_driver(key, df):
    k = key[0]
    if k == "price":
        return float(df.loc[key[1], "Price_Rs_t"])
    if k == "global":
        return float(globals()[key[1]])
    if k == "rule":
        return float(MATERIAL_RULES[key[1]]["coef"])
    if k == "base":
        return float(FURNACE_PROFILES[FURNACE["name"]]["base"])
    if k == "fe":
        return float(FE_REQUIRED_PER_100KG_HM)
    raise ValueError(key)


def _set_driver(key, v, df):
    """Apply a driver value; returns the (possibly copied) df to solve."""
    k = key[0]
    if k == "price":
        d = _fdf(df); d.loc[key[1], "Price_Rs_t"] = max(0.01, v); return d
    if k == "global":
        globals()[key[1]] = v
    elif k == "rule":
        MATERIAL_RULES[key[1]]["coef"] = v
    elif k == "base":
        FURNACE_PROFILES[FURNACE["name"]]["base"] = v
    elif k == "fe":
        globals()["FE_REQUIRED_PER_100KG_HM"] = v
        globals()["REQUIRED_FE_KGTHM"] = HM_BASIS_KG * v / 100.0
    return df


def tornado(df, swing_pct=10.0):
    """Move every driver (prices, fuel-rule coefficients, key limits) down and up ONE AT A TIME and re-solve.
    Prices and rule coefficients move by +/- swing_pct %; limits by a fixed step (Al2O3/MgO caps 0.5 pt,
    B2 floor and cap 0.01, Fe requirement 0.5). Ranked by the cost swing."""
    st0, b0, c0, a0, _ = solve_mbf(df, explain=False)
    if st0 != "Optimal":
        raise ValueError(f"The base case is {st0} - fix it before running the tornado.")
    rows = []
    for lab, cat, key, mode, amt in _driver_list(df, float(swing_pct)):
        v0 = _get_driver(key, df)
        vals = (v0 * (1 - amt / 100.0), v0 * (1 + amt / 100.0)) if mode == "pct" else (v0 - amt, v0 + amt)
        res = []
        for v in vals:
            try:
                d = _set_driver(key, v, df)
                st, b, c, a, _ = solve_mbf(d, explain=False)
                res.append((st, c, a))
            finally:
                _set_driver(key, v0, df)
        (sl, cl, al), (sh, ch, ah) = res
        ok = sl == "Optimal" and sh == "Optimal"
        rows.append({"Driver": lab, "Type": cat, "Base value": round(v0, 4), "Low value": round(vals[0], 4), "High value": round(vals[1], 4),
                     "Cost at low": round(cl, 1) if sl == "Optimal" else np.nan, "Cost at high": round(ch, 1) if sh == "Optimal" else np.nan,
                     "Low vs base Rs": round(cl - c0, 1) if sl == "Optimal" else np.nan, "High vs base Rs": round(ch - c0, 1) if sh == "Optimal" else np.nan,
                     "Swing Rs": round(abs(ch - cl), 1) if ok else np.nan,
                     "Sinter % at low": round(al["Sinter_share_pct"], 2) if sl == "Optimal" else np.nan,
                     "Sinter % at high": round(ah["Sinter_share_pct"], 2) if sh == "Optimal" else np.nan,
                     "Fuel kg at low": round(al["Fuel_supplied"], 1) if sl == "Optimal" else np.nan,
                     "Fuel kg at high": round(ah["Fuel_supplied"], 1) if sh == "Optimal" else np.nan,
                     "Status": "Optimal" if ok else f"low: {sl}, high: {sh}"})
    out = pd.DataFrame(rows).sort_values("Swing Rs", ascending=False, na_position="last").reset_index(drop=True)
    out.attrs["note"] = (f"Base case: Rs {c0:,.2f}/tHM at {a0['Sinter_share_pct']:.2f}% sinter. Prices and rule coefficients moved "
                         f"+/-{swing_pct:g}%; limits by fixed steps. One driver at a time, everything else as applied.")
    return out


def sinter_breakeven(df, iters=16):
    """Sinter prices at which the optimizer's free choice drops to the lower guard rail and reaches the upper one
    (every ON sinter price moved together by the same Rs/t)."""
    if SINTER_MANUAL["on"]:
        raise ValueError("Untick 'Fix sinter share manually' - the break-even needs the optimizer's free choice.")
    sin_on = [m for m in mats(df, "Sinter") if bool(df.loc[m, "Available"])]
    if not sin_on:
        raise ValueError("No sinter material is switched ON.")
    base_p = {m: float(df.loc[m, "Price_Rs_t"]) for m in sin_on}
    lo, hi = SINTER_MIN * 100, SINTER_MAX * 100

    def share_at(delta):
        d = _fdf(df)
        for m in sin_on:
            d.loc[m, "Price_Rs_t"] = max(1.0, base_p[m] + delta)
        st, _, c, a, _ = solve_mbf(d, explain=False)
        return (a["Sinter_share_pct"], c) if st == "Optimal" else (np.nan, np.nan)

    s0, c0 = share_at(0.0)
    if np.isnan(s0):
        raise ValueError("The base case is not feasible.")
    pmin = min(base_p.values())
    rows = [{"Case": "Entered sinter price", "Sinter price Rs/t": round(pmin, 0), "Change Rs/t": 0.0, "Sinter share %": round(s0, 2),
             "Cost Rs/tHM": round(c0, 1), "Meaning": "the optimizer's free choice today"}]

    def search(cond, a_, b_):
        """smallest |delta| in [a_, b_] (monotone) where cond(share) holds; None if never."""
        if not cond(share_at(b_)[0]):
            return None
        if cond(share_at(a_)[0]):
            return a_
        for _ in range(iters):
            m_ = (a_ + b_) / 2
            a_, b_ = (a_, m_) if cond(share_at(m_)[0]) else (m_, b_)
        return b_

    at_floor = lambda s: (not np.isnan(s)) and s <= lo + 0.05
    at_top = lambda s: (not np.isnan(s)) and s >= hi - 0.05
    up = search(at_floor, 0.0, 2.0 * pmin)
    dn = search(at_top, 0.0, -(pmin - 1.0))
    for delta, lab, meaning in ((up, f"Sinter drops to the {lo:g}% floor", "at or above this price the optimizer runs minimum sinter"),
                                (dn, f"Sinter reaches the {hi:g}% top", "at or below this price the optimizer runs maximum sinter")):
        if delta is None:
            rows.append({"Case": lab, "Sinter price Rs/t": np.nan, "Change Rs/t": np.nan, "Sinter share %": np.nan, "Cost Rs/tHM": np.nan,
                         "Meaning": "never within the searched price range (chemistry decides, not price)"})
        else:
            s_, c_ = share_at(delta)
            rows.append({"Case": lab, "Sinter price Rs/t": round(pmin + delta, 0), "Change Rs/t": round(delta, 0),
                         "Sinter share %": round(s_, 2), "Cost Rs/tHM": round(c_, 1), "Meaning": meaning})
    out = pd.DataFrame(rows)
    out.attrs["note"] = ("Compare these prices with the MARGINAL cost of sinter (what the plant saves by making one tonne less), "
                         "not the full absorbed cost.")
    return out


# ---- REPORT TABLES -------------------------------------------------------------------------------
def moisture_table(blend, df):
    rows = []
    for m, v in blend.items():
        if v > 1e-7:
            mo = float(df.loc[m, "Moisture_Pct"])
            wet = v / (1.0 - mo / 100.0)
            rows.append({"Material": m, "Group": df.loc[m, "Group"], "Moisture %": round(mo, 2), "Dry kg/tHM": round(v, 2),
                         "Wet kg/tHM": round(wet, 2), "Water kg/tHM": round(wet - v, 2)})
    if not rows:
        return None
    t = pd.DataFrame(rows)
    tot = {"Material": "TOTAL", "Group": "", "Moisture %": round(100 * t["Water kg/tHM"].sum() / t["Wet kg/tHM"].sum(), 2),
           "Dry kg/tHM": round(t["Dry kg/tHM"].sum(), 2), "Wet kg/tHM": round(t["Wet kg/tHM"].sum(), 2),
           "Water kg/tHM": round(t["Water kg/tHM"].sum(), 2)}
    ck = t[t["Group"].isin(["Fuel_Coke", "Fuel_NutCoke"])]
    extra = [{"Material": "Coke rate (regular + nut) - dry & net vs wet & net", "Group": "", "Moisture %": np.nan,
              "Dry kg/tHM": round(ck["Dry kg/tHM"].sum(), 2), "Wet kg/tHM": round(ck["Wet kg/tHM"].sum(), 2),
              "Water kg/tHM": round(ck["Water kg/tHM"].sum(), 2)}]
    return pd.concat([t, pd.DataFrame([tot]), pd.DataFrame(extra)], ignore_index=True)


def fe_impact_table(blend, df, ach):
    ft = ach["Fuel_terms"]
    rows = []
    for name, grp in (("ore_fe", "Iron_ore"), ("sinter_fe", "Sinter")):
        r = MATERIAL_RULES[name]
        w = rule_weights(df, name)
        for m in mats(df, grp):
            v = float(blend.get(m, 0.0))
            if v <= 1e-7 or m not in w:
                continue
            fe = float(df.loc[m, "Fe"])
            rows.append({"Material": m, "Group": grp, "Fe %": round(fe, 3), "Ref Fe %": r["ref"], "Pts vs ref": round(fe - r["ref"], 3),
                         "kg/tHM used": round(v, 2), "Fuel effect kg/tHM": round(w[m] * v if term_active(name) else 0.0, 3),
                         "Rule": f"{r['coef']:g} kg per +1 pt Fe at {REF_RATES[r['rate']]:g} kg/tHM"})
    total = sum(float(x["Fuel effect kg/tHM"]) for x in rows)
    rows.append({"Material": "TOTAL Fe effect on fuel", "Group": "", "Fe %": np.nan, "Ref Fe %": np.nan, "Pts vs ref": np.nan,
                 "kg/tHM used": np.nan, "Fuel effect kg/tHM": round(total, 3), "Rule": "negative = saves fuel, positive = adds fuel"})
    return pd.DataFrame(rows)


def oxide_sources_table(blend, df, ach):
    """Where the slag oxides come from: kg and % of the charged total of CaO, SiO2, Al2O3 and MgO per material,
    then the SiO2 reduced into the metal, ending on the slag totals (these match the slag chemistry table)."""
    order = {g: i for i, g in enumerate(GROUPS)}
    used = sorted([m for m in df.index if float(blend.get(m, 0.0)) > 1e-7],
                  key=lambda m: (order.get(str(df.loc[m, "Group"]), 99), list(df.index).index(m)))
    kg = {m: {ox: float(blend[m]) * float(df.loc[m, ox]) / 100.0 for ox in OXIDES} for m in used}
    tot = {ox: sum(kg[m][ox] for m in used) for ox in OXIDES}
    rows = []
    for m in used:
        r = {"Material": m, "Group": df.loc[m, "Group"], "kg/tHM charged": round(float(blend[m]), 2)}
        for ox in OXIDES:
            r[f"{ox} kg"] = round(kg[m][ox], 3)
            r[f"{ox} % of total"] = round(100 * kg[m][ox] / tot[ox], 2) if tot[ox] > 1e-12 else 0.0
        rows.append(r)
    r = {"Material": "TOTAL CHARGED", "Group": "", "kg/tHM charged": round(sum(float(blend[m]) for m in used), 2)}
    for ox in OXIDES:
        r[f"{ox} kg"] = round(tot[ox], 3); r[f"{ox} % of total"] = 100.0
    rows.append(r)
    red = ach["SiO2_reduced_kg"]
    r = {"Material": "Less: SiO2 reduced to Si in hot metal", "Group": "", "kg/tHM charged": np.nan}
    for ox in OXIDES:
        r[f"{ox} kg"] = round(-red, 3) if ox == "SiO2" else 0.0; r[f"{ox} % of total"] = np.nan
    rows.append(r)
    slag = {"CaO": ach["CaO_kg"], "SiO2": ach["slag_SiO2_kg"], "Al2O3": ach["Al2O3_kg"], "MgO": ach["MgO_kg"]}
    r = {"Material": "SLAG", "Group": "", "kg/tHM charged": round(ach["slag_kg"], 2)}
    for ox in OXIDES:
        r[f"{ox} kg"] = round(slag[ox], 3); r[f"{ox} % of total"] = round(100 * slag[ox] / ach["slag_kg"], 2)
    rows.append(r)
    out = pd.DataFrame(rows)
    top = {ox: max(used, key=lambda m: kg[m][ox]) for ox in OXIDES} if used else {}
    out.attrs["note"] = ("Largest source - " + ", ".join(f"{ox}: {top[ox]} ({100 * kg[top[ox]][ox] / tot[ox]:.0f}%)" for ox in OXIDES if tot[ox] > 1e-12)
                         + ". On the SLAG row the % columns are the slag composition.") if used else None
    return out


def stock_table(blend, df):
    plan = float(STOCK["plan_hm_tonnes"])
    rows = []
    for g in GROUPS:
        ms = [m for m in mats(df, g) if bool(df.loc[m, "Available"]) and pd.notna(df.loc[m, "RM_Stock"])]
        if not ms:
            continue
        used_g = sum(blend[m] for m in mats(df, g))
        stock_g = sum(float(df.loc[m, "RM_Stock"]) for m in ms)
        for m in ms:
            st = float(df.loc[m, "RM_Stock"])
            bal = g in BALANCE_GROUPS and len(ms) >= 2
            row = {"Material": m, "Group": g, "RM stock (t)": round(st, 1), "kg/tHM used": round(blend[m], 2),
                   "Share of group used %": round(100 * blend[m] / used_g, 1) if (bal and used_g > 1e-9) else np.nan,
                   "Share of group stock %": round(100 * st / stock_g, 1) if (bal and stock_g > 0) else np.nan}
            if plan > 0:
                need = blend[m] * plan / 1000.0
                left = st - need
                row["Needed for plan (t)"] = round(max(need, 0.0), 1)
                row["Stock left (t)"] = 0.0 if abs(left) < 0.05 else round(left, 1)
            rows.append(row)
    return pd.DataFrame(rows) if rows else None



# ================================================================
# 8. EXCEL LOADER
# ================================================================
ALIASES = {
    "Material": ["Material"], "Group": ["Group", "Material_Group", "Category"],
    "Fe": ["Fe %", "Fe"], "CaO": ["CaO %", "CaO"], "MgO": ["MgO %", "MgO"],
    "SiO2": ["SiO2 %", "SiO2"], "Al2O3": ["Al2O3 %", "Al2O3"], "Mn": ["Mn %", "Mn"],
    "S": ["S %", "S"], "FC": ["FC %", "Fixed Carbon %", "Fixed Carbon"],
    "Moisture_Pct": ["Moisture %", "Moisture", "Moisture_Pct"],
    "Price_Rs_t": ["Price Rs/t", "Price_Rs_t", "Price", "Cost (Rs/t)", "Cost"],
    "RM_Stock": ["RM Stock", "RM_Stock", "RM Stock (t)", "Stock", "Stock (t)", "Stock_t", "Inventory", "Inventory (t)"],
    "Fines_Pct": ["Fines %", "Fines_Pct", "Fines Pct", "Fines Generated %", "Minus 100 mesh %"],
    "Fines_Credit_Rs_t": ["Fines Credit Rs/t", "Fines_Credit_Rs_t", "Fines Credit", "Fines Value Rs/t", "Fines Price Rs/t"],
    "Available": ["Availability", "Available", "Available (ON/OFF)"],
}


GROUP_LOOKUP = {norm(g): g for g in GROUPS}       # 'Iron ore', 'iron_ore', 'IRON-ORE' all mean Iron_ore


def load_excel_bytes(content, notes=None):
    """Read a master materials workbook (first sheet). Returns the material table.
    If `notes` is a list, plain-language notes about how the file was read are appended to it."""
    notes = [] if notes is None else notes
    raw = pd.read_excel(io.BytesIO(bytes(content)))
    ncols = {norm(c): c for c in raw.columns}
    mp = {}
    for k, als in ALIASES.items():
        for a in als:
            if norm(a) in ncols:
                mp[k] = ncols[norm(a)]
                break
    missing = [k for k in ["Material", "Group", "Price_Rs_t", "Available"] if k not in mp]
    if missing:
        raise ValueError("Missing required Excel columns: " + ", ".join(missing))
    ignored = [c for c in raw.columns if norm(c) in ("ash", "ashpct", "cokeash", "cokeashpct", "csr", "csrpct", "cokecsr")]
    if ignored:
        notes.append(f"Columns {ignored} are ignored - coke ash and CSR are not part of the v11.7 rule set.")
    out, blank_av = [], []
    for _, row in raw.iterrows():
        if pd.isna(row[mp["Material"]]):
            continue
        name = sanitize_name(row[mp["Material"]])
        g_raw = str(row[mp["Group"]]).strip()
        g = GROUP_LOOKUP.get(norm(g_raw), g_raw)
        if g != g_raw:
            notes.append(f"{name}: Group '{g_raw}' read as '{g}'.")
        if g == "Iron_ore" and any(norm(name).startswith(h) for h in MINOR_NAME_HINTS):
            notes.append(f"{name}: moved from Iron_ore to Minor (used only when the limits need it).")
            g = "Minor"
        rec = {"Material": name, "Group": g}
        for k in NUM_COLS:
            if k in mp and pd.notna(row[mp[k]]):
                try:
                    rec[k] = float(row[mp[k]])
                except Exception:
                    raise ValueError(f"{name}: '{k}' is not a number ({row[mp[k]]!r})")
            else:
                rec[k] = 0.0
        for oc in OPT_COLS:
            if oc in mp and pd.notna(row[mp[oc]]):
                try:
                    rec[oc] = float(row[mp[oc]])
                except Exception:
                    raise ValueError(f"{name}: '{oc}' is not a number ({row[mp[oc]]!r})")
            else:
                rec[oc] = np.nan
        av = row[mp["Available"]]
        if pd.isna(av):
            blank_av.append(name)
        rec["Available"] = (str(av).strip().upper() not in ["OFF", "NO", "0", "FALSE", "UNAVAILABLE", "N"]) if pd.notna(av) else True
        out.append(rec)
    if not out:
        raise ValueError("The workbook has no material rows.")
    df = pd.DataFrame(out)
    if df["Material"].duplicated().any():
        raise ValueError("Duplicate material names after cleaning: " + str(sorted(set(df["Material"][df["Material"].duplicated()]))))
    df = df.set_index("Material")[COLUMNS[1:]]
    errs = validate_df(df)
    if errs:
        raise ValueError("\n - " + "\n - ".join(errs))
    if blank_av:
        notes.append(f"Availability is blank for {', '.join(blank_av)} - treated as ON.")
    return df


def load_master(content):
    """Dashboard entry point: returns (material table, list of notes about how the file was read)."""
    notes = []
    return load_excel_bytes(content, notes), notes


_TEMPLATE_HEADERS = {"Fe": "Fe %", "CaO": "CaO %", "MgO": "MgO %", "SiO2": "SiO2 %", "Al2O3": "Al2O3 %", "Mn": "Mn %", "S": "S %",
                     "FC": "FC %", "Moisture_Pct": "Moisture %", "Price_Rs_t": "Price Rs/t", "RM_Stock": "RM Stock (t)",
                     "Fines_Pct": "Fines %", "Fines_Credit_Rs_t": "Fines credit Rs/t"}


def template_bytes():
    """The input template as .xlsx bytes: one sheet, one row per material (values are DEMO placeholders), plus a guide sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter as L
    from openpyxl.worksheet.datavalidation import DataValidation
    d = plant_df.reset_index().copy()
    d["Availability"] = np.where(d["Available"], "ON", "OFF")
    d = d.drop(columns=["Available"]).rename(columns=_TEMPLATE_HEADERS)
    wb = Workbook()
    ws = wb.active
    ws.title = "Materials"
    head = PatternFill("solid", start_color="2B2E38", end_color="2B2E38")
    for j, c in enumerate(d.columns, start=1):
        x = ws.cell(1, j, c)
        x.font = Font(bold=True, color="FFFFFF"); x.fill = head; x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[L(j)].width = max(11, len(c) + 3)
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 15
    for i, row in enumerate(d.itertuples(index=False), start=2):
        for j, v in enumerate(row, start=1):
            if isinstance(v, float) and v != v:
                v = None
            elif isinstance(v, (np.floating,)):
                v = float(v)
            ws.cell(i, j, v)
    n = len(d) + 60
    dv_g = DataValidation(type="list", formula1='"' + ",".join(GROUPS) + '"', allow_blank=False)
    dv_a = DataValidation(type="list", formula1='"ON,OFF"', allow_blank=False)
    ws.add_data_validation(dv_g); ws.add_data_validation(dv_a)
    dv_g.add(f"B2:B{n}")
    dv_a.add(f"{L(len(d.columns))}2:{L(len(d.columns))}{n}")
    ws.freeze_panes = "C2"
    ws.row_dimensions[1].height = 30
    g = wb.create_sheet("Guide")
    guide = [("Materials sheet", "One row per material. Only this first sheet is read."),
             ("Group", "One of: " + ", ".join(GROUPS) + "."),
             ("Availability", "ON or OFF. A blank cell is treated as ON."),
             ("Assays", "Fe, CaO, MgO, SiO2, Al2O3, Mn, S, FC and Moisture are percentages on a dry basis."),
             ("Price Rs/t", "Per DRY tonne (change to wet under Inputs > Stock and pricing)."),
             ("RM Stock (t)", "Optional. Blank = unlimited. 0 = treated as unavailable."),
             ("Fines % and credit", "Optional, ore only. Price used = (price - fines x credit) / (1 - fines)."),
             ("Moisture", "Measured moisture of each material. Sinter moisture drives the sinter/flux moisture rule (an extension)."),
             ("Values in this file", "DEMO placeholders. Replace every price and assay with plant values before relying on a result.")]
    g.column_dimensions["A"].width = 22
    g.column_dimensions["B"].width = 110
    for i, (a, b) in enumerate(guide, start=1):
        g.cell(i, 1, a).font = Font(bold=True)
        g.cell(i, 2, b).alignment = Alignment(wrap_text=True)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ================================================================
# 9. RESULT TABLES + EXCEL EXPORT  (only when you press EXPORT OPTIMISED RESULTS)
# ================================================================
import datetime as _dt
import os
import tempfile

LAST_ANALYSIS = {}
ANALYSIS_SHEETS = [   # key in LAST_ANALYSIS, sheet name, chart columns (title, column)
    ("sweep", "Sinter Sweep", [("Coke kg/tHM vs sinter share", "Coke kg"), ("Cost Rs/tHM vs sinter share", "Cost Rs/tHM")]),
    ("sweep_oxides", "Oxides by Sinter %", None),
    ("sensitivity", "Assay Sensitivity", [("Cost Rs/tHM vs assay change", "Cost Rs/tHM"), ("Fuel kg/tHM vs assay change", "Fuel kg")]),
    ("price", "Price Sensitivity", [("Cost Rs/tHM vs price change", "Cost Rs/tHM"), ("Sinter % vs price change", "Sinter %")]),
    ("tornado", "Tornado", None),
    ("breakeven", "Sinter Break-even", None),
]

TERM_LABELS = {"slag": "Slag", "sinter_share": "Sinter share", "raw_flux": "Raw flux",
               "ore_fe": "Ore Fe", "sinter_fe": "Sinter Fe", "ore_moisture": "Ore moisture",
               "coke_moisture": "Coke moisture (coke + nut coke)", "other_moisture": "Moisture: sinter / flux / minor"}


def _fingerprint(df):
    try:
        return int(pd.util.hash_pandas_object(df, index=True).sum() % (2 ** 62))
    except Exception:
        return -1


def _band_status(v, lo, hi, rel=0.01):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    span = max(hi - lo, 1e-9)
    tol = 1e-3 * span
    if v < lo - tol or v > hi + tol:
        return "OUT OF LIMIT"
    if v - lo <= rel * span or hi - v <= rel * span:
        return "AT LIMIT"
    return "OK"


def compliance_table(ach, df):
    rows = []

    def add(req, target, achieved, status, note=""):
        rows.append({"Requirement": req, "Target": target, "Achieved": achieved, "Status": status, "Note": note})

    fe_a = ach["Fe_burden_kg"] if FE_CLOSURE_BASIS != "all" else ach["Fe_kg"]
    add("Fe closure (kg Fe per tHM)", f"{REQUIRED_FE_KGTHM:.1f} exactly ({FE_REQUIRED_PER_100KG_HM:g} per 100 kg HM)", round(fe_a, 3),
        "OK" if abs(fe_a - REQUIRED_FE_KGTHM) < 0.01 else "OUT OF LIMIT", f"basis: {FE_CLOSURE_BASIS}")
    sp = ach["Sinter_share_pct"]
    if SINTER_MANUAL["on"]:
        pm = float(SINTER_MANUAL["pct"])
        add("Sinter share of sinter + ore (%)", f"{pm:g} (MANUAL, pinned)", round(sp, 3), "OK" if abs(sp - pm) < 0.01 else "OUT OF LIMIT",
            "outside the guard rails - sensitivity use only" if not (SINTER_MIN * 100 <= pm <= SINTER_MAX * 100) else "")
    else:
        add("Sinter share of sinter + ore (%)", f"free choice, guard rails {SINTER_MIN*100:g} - {SINTER_MAX*100:g}", round(sp, 3),
            _band_status(sp, SINTER_MIN * 100, SINTER_MAX * 100), "AT LIMIT = the optimizer stopped at a guard rail")
    add("Slag basicity B2 = CaO/SiO2", f"{BASICITY_MIN} - {BASICITY_MAX}", round(ach["B2"], 4), _band_status(ach["B2"], BASICITY_MIN, BASICITY_MAX))
    add("MgO in slag (%)", f"{MGO_MIN_PCT:g} - {MGO_MAX_PCT:g}", round(ach["MgO_pct"], 3), _band_status(ach["MgO_pct"], MGO_MIN_PCT, MGO_MAX_PCT))
    add("Al2O3 in slag (%)", f"{AL2O3_MIN_PCT:g} - {AL2O3_MAX_PCT:g}", round(ach["Al2O3_pct"], 3), _band_status(ach["Al2O3_pct"], AL2O3_MIN_PCT, AL2O3_MAX_PCT))
    ma = ach["MgO_Al2O3"]
    add("MgO / Al2O3 (diagnostic only)", f"{MGO_AL2O3_GUIDE[0]:g} - {MGO_AL2O3_GUIDE[1]:g} guide", round(ma, 3),
        "OK" if MGO_AL2O3_GUIDE[0] - 1e-9 <= ma <= MGO_AL2O3_GUIDE[1] + 1e-9 else "NOTE", "slag drainage guide; never constrains")
    add("PCI (kg/tHM)", f"{PCI_FIXED_KGTHM:g} fixed", round(ach["PCI_kg"], 3), "OK" if abs(ach["PCI_kg"] - PCI_FIXED_KGTHM) < 1e-3 else "OUT OF LIMIT")
    if any(bool(df.loc[m, "Available"]) for m in mats(df, "Fuel_NutCoke")):
        ok = abs(ach["NutCoke_kg"] - NUT_COKE_KGTHM) < 1e-3 if NUT_COKE_MODE == "fixed" else ach["NutCoke_kg"] <= NUT_COKE_KGTHM + 1e-3
        add("Nut coke (kg/tHM)", f"{NUT_COKE_KGTHM:g} ({NUT_COKE_MODE})", round(ach["NutCoke_kg"], 3), "OK" if ok else "OUT OF LIMIT")
    add("Minor materials (kg/tHM)", f"<= {MINOR_MAX_KGTHM:g}", round(ach["Minor_kg"], 3), "OK" if ach["Minor_kg"] <= MINOR_MAX_KGTHM + 1e-3 else "OUT OF LIMIT",
        "used only if the limits cannot be met without them" if MINOR_DEMAND_ONLY else "cost decides")
    gap = ach["Fuel_supplied"] - ach["Fuel_rule"]
    add("Fuel supplied = plant fuel rule (kg/tHM)", f"{ach['Fuel_rule']:.2f}", round(ach["Fuel_supplied"], 3), "OK" if abs(gap) < 0.05 else "OUT OF LIMIT", f"gap {gap:+.4f}")
    h_ = ach["Heat"]
    add("Heat balance: carbon charged >= hot-zone need (kg C/tHM)", f"{h_['C_need_kg']:.2f}", round(h_["C_supplied_kg"], 3),
        "OK" if h_["C_surplus_kg"] >= -0.05 else ("OUT OF LIMIT" if HEAT["mode"] == "floor" else "NOTE"),
        ("enforced (floor mode)" if HEAT["mode"] == "floor" else "report only (check mode)")
        + ("" if HEAT["calibrated"] else "; placeholders, not calibrated"))
    fc = ach["Fe_C_ratio"]
    add("Fe/C (diagnostic only)", f"{FE_C_RATIO_TARGET - FE_C_RATIO_TOL:g} - {FE_C_RATIO_TARGET + FE_C_RATIO_TOL:g} guide", round(fc, 3),
        "OK" if FE_C_RATIO_TARGET - FE_C_RATIO_TOL - 1e-9 <= fc <= FE_C_RATIO_TARGET + FE_C_RATIO_TOL + 1e-9 else "NOTE", "never constrains the optimizer")
    return pd.DataFrame(rows)


def burden_comparison(blend, df, ach):
    order = {g: i for i, g in enumerate(GROUPS)}
    idx = list(df.index)
    ms = sorted(idx, key=lambda m: (order.get(str(df.loc[m, "Group"]), 99), idx.index(m)))
    tot = ach["Total_burden_kg"]
    rows = []
    for m in ms:
        v = float(blend.get(m, 0.0))
        g = str(df.loc[m, "Group"])
        on = bool(df.loc[m, "Available"])
        mo = float(df.loc[m, "Moisture_Pct"])
        gtot = sum(float(blend.get(k, 0.0)) for k in mats(df, g))
        used = v > 1e-7
        wet = v / (1 - mo / 100.0)
        st = df.loc[m, "RM_Stock"]
        rows.append({"Material": m, "Group": g, "Availability": "ON" if on else "OFF",
                     "Result": "USED" if used else ("not used" if on else "OFF"),
                     "Price Rs/t (dry)": eff_price(df, m), "RM stock (t)": np.nan if pd.isna(st) else float(st),
                     "Moisture %": mo, "Dry kg/tHM": v, "% of burden": 100 * v / tot if tot > 0 else 0.0,
                     "Wet kg/tHM": wet, "Water kg/tHM": wet - v, "Cost Rs/tHM": v * eff_price(df, m) / 1000.0,
                     "Share of group %": (100 * v / gtot) if (used and gtot > 0) else np.nan})
    t = pd.DataFrame(rows)
    tr = {"Material": "TOTAL", "Group": "", "Availability": "", "Result": "", "Price Rs/t (dry)": np.nan, "RM stock (t)": np.nan,
          "Moisture %": np.nan, "Dry kg/tHM": t["Dry kg/tHM"].sum(), "% of burden": 100.0, "Wet kg/tHM": t["Wet kg/tHM"].sum(),
          "Water kg/tHM": t["Water kg/tHM"].sum(), "Cost Rs/tHM": t["Cost Rs/tHM"].sum(), "Share of group %": np.nan}
    return pd.concat([t, pd.DataFrame([tr])], ignore_index=True)


def settings_snapshot():
    prof = FURNACE_PROFILES[FURNACE["name"]]
    R = []

    def add(sec, name, val, note=""):
        R.append((sec, name, val, note))

    S = "Basis"
    add(S, "Model", "v11.7 - ideal plant conditions, plant-reviewed thumb rules", "furnace at reference (hot blast, no offset), coke at reference quality, steady state")
    add(S, "Basis", "1 tonne hot metal; dry & net kg")
    add(S, "Solver", type(SOLVER).__name__)
    S = "Hot metal & Fe closure"
    add(S, "Fe charged per 100 kg hot metal", FE_REQUIRED_PER_100KG_HM, f"equals {REQUIRED_FE_KGTHM:.1f} kg/tHM, matched exactly")
    add(S, "Fe closure basis", FE_CLOSURE_BASIS, "burden = ore + sinter + minor | all = every charged material")
    add(S, "HM Si (%)", HM_SI_PCT); add(S, "HM C (%) - stoichiometric floor", HM_C_PCT); add(S, "HM Fe (%) - stoichiometric floor", HM_FE_PCT)
    S = "Operating policy"
    add(S, "Sinter : Ore", (f"MANUAL {SINTER_MANUAL['pct']:g}% (pinned)" if SINTER_MANUAL["on"] else
                            f"free choice inside guard rails {SINTER_MIN*100:g}% - {SINTER_MAX*100:g}% sinter"))
    add(S, "PCI (kg/tHM)", PCI_FIXED_KGTHM, "fixed")
    add(S, "Nut coke (kg/tHM)", NUT_COKE_KGTHM, NUT_COKE_MODE + " when a nut coke is ON")
    add(S, "B2 band", f"{BASICITY_MIN} - {BASICITY_MAX}", "1.00 +/- 0.01"); add(S, "MgO band (% of slag)", f"{MGO_MIN_PCT:g} - {MGO_MAX_PCT:g}")
    add(S, "Al2O3 band (% of slag)", f"{AL2O3_MIN_PCT:g} - {AL2O3_MAX_PCT:g}")
    add(S, "MgO/Al2O3 guide", f"{MGO_AL2O3_GUIDE[0]:g} - {MGO_AL2O3_GUIDE[1]:g}", "diagnostic only")
    add(S, "Minor materials", f"cap {MINOR_MAX_KGTHM:g} kg/tHM", "demand-driven (only if needed)" if MINOR_DEMAND_ONLY else "cost decides within the cap")
    S = "Fuel-rate rules (fixed rule set)"
    add(S, "Furnace profile", FURNACE["name"], f"base fuel {prof['base']:g} kg/tHM at the plant reference point")
    add(S, "Slag", f"{FUEL_PER_KG_SLAG:g} kg per kg over {SLAG_REF_KGTHM:g}")
    add(S, "Sinter share", f"{FUEL_PER_PCT_SINTER:g} kg per point of sinter in (sinter + ore), ref {SINTER_REF_PCT:g}%",
        f"+10 pts sinter = {-10 * FUEL_PER_PCT_SINTER:g} kg fuel, and vice versa (plant thumb rule)")
    add(S, "Raw flux (limestone + dolomite)", f"{FUEL_PER_KG_RAW_FLUX:g} kg per kg over {RAW_FLUX_REF_KGTHM:g}", "literature: 20-35 kg coke per 100 kg limestone")
    for nm, r in MATERIAL_RULES.items():
        add(S, TERM_LABELS.get(nm, nm), f"{r['coef']:g} kg per point {'over' if r['sign'] > 0 else 'under'} {r['ref']:g}",
            f"{r['attr']} of {', '.join(r['pools'])}; per-kg form at {REF_RATES[r['rate']]:g} kg/tHM"
            + ("; EXTENSION - not a plant rule" if nm in EXTENSION_TERMS else ""))
    add(S, "Not in the model (by design)", "PCI rate, PCI FC, PCI moisture, coke ash, coke CSR, DRI credit, hot blast, furnace offset",
        "zero effect under ideal plant conditions")
    add(S, "Removed in the plant review", "Mar-26 burden-Fe fuel form; ore-grade cost model", "Sep-26 thumb-rule checklist")
    S = "Sulphur (diagnostic only)"
    add(S, "Ks (S partition slag/metal)", KS_FIXED, "fixed; S in HM = all charged S / (1 + Ks x slag / HM)")
    off = rules_off()
    add(S, "Thumb rules switched OFF", ", ".join(off) if off else "none - full plant rule set",
        "switched off with the rule buttons; base fuel is always on")
    S = "Heat balance (hot zone below the TRZ)"
    add(S, "Mode", HEAT["mode"], "check = report only | floor = LP needs fixed carbon >= heat need")
    add(S, "Calibration", "calibrated" if HEAT["calibrated"] else "NOT calibrated", HEAT["calibration_note"])
    for k_, lab_ in (("blast_temp_C", "Hot-blast temperature (C)"), ("blast_humidity_g_Nm3", "Blast moisture (g/Nm3)"),
                     ("blast_O2_pct", "O2 in dry blast (%)"), ("trz_temp_C", "Thermal reserve zone temperature (C)"),
                     ("drr", "Degree of direct reduction at ref. sinter"), ("drr_per_pt_sinter", "DRR change per +1 pt sinter"),
                     ("calc_hot_frac", "Share of raw-flux carbonate calcined in the hot zone"), ("sol_loss_frac", "Share of that CO2 reacting with coke"),
                     ("h_hm_kJ_kg", "Hot metal enthalpy at tapping (kJ/kg)"), ("h_fe_trz_kJ_kg", "Iron enthalpy at the TRZ (kJ/kg)"),
                     ("h_slag_kJ_kg", "Slag enthalpy at tapping (kJ/kg)"), ("h_gangue_trz_kJ_kg", "Gangue enthalpy at the TRZ (kJ/kg)"),
                     ("loss_MJ_tHM", "Lower-furnace heat losses (MJ/tHM)")):
        add(S, lab_, round(float(HEAT[k_]), 4), "literature placeholder" if (HEAT[k_] == HEAT_DEFAULT[k_] and k_ != "loss_MJ_tHM")
            else ("calibrated" if k_ == "loss_MJ_tHM" and HEAT["calibrated"] else ("placeholder - calibrate" if k_ == "loss_MJ_tHM" else "user value")))
    S = "Model constants and guides"
    add(S, "Reference charge rates for the per-point rules (kg/tHM)", ", ".join(f"{k} {v:g}" for k, v in REF_RATES.items()),
        "per-kg rule weight = kg per point / reference rate")
    add(S, "Raw flux threshold (CaO + MgO %)", RAW_FLUX_MIN_CAO_MGO, "a Flux row at or above this counts as limestone/dolomite")
    add(S, "DRR for the stoichiometric carbon floor", DIRECT_REDUCTION_DEGREE, "sanity floor only; the heat balance has its own DRR")
    add(S, "Mn reduction efficiency", MN_REDUCTION_EFF)
    add(S, "MgO/Al2O3 guide", f"{MGO_AL2O3_GUIDE[0]:g} - {MGO_AL2O3_GUIDE[1]:g}", "diagnostic only")
    add(S, "Fe/C guide", f"{FE_C_RATIO_TARGET:g} +/- {FE_C_RATIO_TOL:g}", "diagnostic only")
    S = "Cost, moisture & stock"
    add(S, "Price basis", f"per {PRICE_BASIS} tonne", "quantities are dry & net; wet = dry / (1 - moisture)")
    add(S, "O&M cost (Rs/tHM)", OM_RS_THM, "operations & maintenance, added to the raw-material cost; does not change the burden")
    add(S, "Ore fines credit", "per material", "price used = (price - fines x credit) / (1 - fines); blank Fines % = none")
    add(S, "Stock balance", STOCK["balance"], "1 = usage split by stock in multi-source groups; 0 = cost decides")
    add(S, "Planned hot metal for stock caps (t)", STOCK["plan_hm_tonnes"] if STOCK["plan_hm_tonnes"] > 0 else "off")
    return R


def result_tables(status, blend, cost, ach, df):
    """(key, title, DataFrame) for every result table - shared by the screen report and the Excel export."""
    T = []
    rows = [{"Material": m, "Group": df.loc[m, "Group"], "kg/tHM": round(v, 3),
             "% of burden": round(100 * v / ach["Total_burden_kg"], 2), "Cost Rs/tHM": round(v * eff_price(df, m) / 1000, 2)}
            for m, v in blend.items() if v > 1e-7]
    if float(ach.get("OM_Rs_tHM", 0.0)) > 0:
        rows.append({"Material": "O&M (operations & maintenance)", "Group": "O&M", "kg/tHM": np.nan, "% of burden": np.nan, "Cost Rs/tHM": round(float(ach["OM_Rs_tHM"]), 2)})
    rows.append({"Material": "TOTAL", "Group": "TOTAL", "kg/tHM": round(ach["Total_burden_kg"], 3), "% of burden": 100.0, "Cost Rs/tHM": round(cost, 2)})
    T.append(("burden", "OPTIMIZED BURDEN (per tonne of hot metal)", pd.DataFrame(rows)))
    mode = (f"MANUAL {SINTER_MANUAL['pct']:g}%" if SINTER_MANUAL["on"] else f"free choice ({SINTER_MIN*100:.0f}-{SINTER_MAX*100:.0f}% guard rails)")
    T.append(("sinter_ore", "SINTER : ORE", pd.DataFrame([{"Mode": mode, "Achieved sinter share %": round(ach["Sinter_share_pct"], 2),
                                                          "Ore kg/tHM": round(ach["Ore_kg"], 1), "Sinter kg/tHM": round(ach["Sinter_kg"], 1)}])))
    st_ = _band_status
    slag_df = pd.DataFrame([
        {"Component": "CaO", "kg/tHM": round(ach["CaO_kg"], 2), "% of slag": round(ach["CaO_pct"], 2), "Target": "", "Status": ""},
        {"Component": "SiO2 (slag basis)", "kg/tHM": round(ach["slag_SiO2_kg"], 2), "% of slag": round(ach["SiO2_slag_pct"], 2), "Target": "", "Status": ""},
        {"Component": "MgO", "kg/tHM": round(ach["MgO_kg"], 2), "% of slag": round(ach["MgO_pct"], 2), "Target": f"{MGO_MIN_PCT}-{MGO_MAX_PCT}%",
         "Status": st_(ach["MgO_pct"], MGO_MIN_PCT, MGO_MAX_PCT)},
        {"Component": "Al2O3", "kg/tHM": round(ach["Al2O3_kg"], 2), "% of slag": round(ach["Al2O3_pct"], 2), "Target": f"{AL2O3_MIN_PCT}-{AL2O3_MAX_PCT}%",
         "Status": st_(ach["Al2O3_pct"], AL2O3_MIN_PCT, AL2O3_MAX_PCT)},
        {"Component": "Total slag mass", "kg/tHM": round(ach["slag_kg"], 2), "% of slag": 100.0, "Target": f"ref {SLAG_REF_KGTHM:.0f}", "Status": ""},
        {"Component": "B2 = CaO/SiO2", "kg/tHM": np.nan, "% of slag": round(ach["B2"], 4), "Target": f"{BASICITY_MIN}-{BASICITY_MAX}",
         "Status": st_(ach["B2"], BASICITY_MIN, BASICITY_MAX)},
        {"Component": "MgO/Al2O3", "kg/tHM": np.nan, "% of slag": round(ach["MgO_Al2O3"], 3), "Target": f"{MGO_AL2O3_GUIDE[0]:g}-{MGO_AL2O3_GUIDE[1]:g} guide",
         "Status": "OK" if MGO_AL2O3_GUIDE[0] - 1e-9 <= ach["MgO_Al2O3"] <= MGO_AL2O3_GUIDE[1] + 1e-9 else "NOTE"},
        {"Component": "B4 = (CaO+MgO)/(SiO2+Al2O3)", "kg/tHM": np.nan, "% of slag": round(ach["B4"], 3), "Target": "diagnostic only", "Status": ""},
    ])
    T.append(("slag", "SLAG CHEMISTRY (SiO2 net of Si reduced into hot metal)", slag_df))
    T.append(("oxides", "SLAG OXIDE SOURCES - where CaO, SiO2, Al2O3 and MgO come from (kg per tHM)", oxide_sources_table(blend, df, ach)))
    fuel_df = pd.DataFrame([
        {"Item": "PCI", "kg/tHM": round(ach["PCI_kg"], 2), "Note": f"fixed at {PCI_FIXED_KGTHM:g}"},
        {"Item": "Nut Coke", "kg/tHM": round(ach["NutCoke_kg"], 2),
         "Note": (f"fixed at {NUT_COKE_KGTHM:g} when ON" if NUT_COKE_MODE == "fixed" else f"cap {NUT_COKE_KGTHM:g}")},
        {"Item": "Regular Coke (residual)", "kg/tHM": round(ach["Coke_kg"], 2), "Note": ""},
        {"Item": "Total fuel supplied", "kg/tHM": round(ach["Fuel_supplied"], 2), "Note": "coke + nut coke + PCI"},
        {"Item": "Total fuel required by plant rule", "kg/tHM": round(ach["Fuel_rule"], 2),
         "Note": f"sinter {ach['Sinter_share_pct']:.1f}%, slag {ach['slag_kg']:.0f}, raw flux {ach['Raw_flux_kg']:.1f}"},
        {"Item": "Total fixed carbon", "kg/tHM": round(ach["FC_kg"], 2), "Note": f"stoich. floor {ach['FC_floor']:.1f} (sanity only)"},
    ])
    T.append(("fuel", "FUEL BALANCE", fuel_df))
    ft = ach["Fuel_terms"]
    drv = {"slag": f"{FUEL_PER_KG_SLAG:g} kg per kg slag over {SLAG_REF_KGTHM:g}; slag {ach['slag_kg']:.1f}",
           "sinter_share": f"{FUEL_PER_PCT_SINTER:g} kg per point under {SINTER_REF_PCT:g}%; sinter {ach['Sinter_share_pct']:.1f}%",
           "raw_flux": f"{FUEL_PER_KG_RAW_FLUX:g} kg per kg limestone/dolomite over {RAW_FLUX_REF_KGTHM:g}; raw flux {ach['Raw_flux_kg']:.1f}"}
    for nm, r in MATERIAL_RULES.items():
        drv[nm] = (f"{r['coef']:g} kg per point {'over' if r['sign'] > 0 else 'under'} {r['ref']:g} ({r['attr']}, at {REF_RATES[r['rate']]:g} kg/tHM)"
                   + ("  [extension - no plant rule]" if nm in EXTENSION_TERMS else ""))
    rows_bd = [{"Term": f"Base ({FURNACE['name']} at reference point)", "kg/tHM": round(ft["base"], 2), "Driver": ""}]
    for k in FUEL_TERM_NAMES:
        rows_bd.append({"Term": TERM_LABELS[k], "kg/tHM": round(float(ft[k]), 2), "Driver": drv[k] + ("" if term_active(k) else "  [switched off]")})
    rows_bd.append({"Term": "TOTAL fuel (coke + nut coke + PCI)", "kg/tHM": round(ft["total"], 2), "Driver": ""})
    T.append(("breakdown", "FUEL-RATE BREAKDOWN (why the fuel rate is what it is)", pd.DataFrame(rows_bd)))
    T.append(("fe_impact", "FE IMPACT ON FUEL - iron ore & sinter (negative = saves fuel)", fe_impact_table(blend, df, ach)))
    mt = moisture_table(blend, df)
    if mt is not None:
        T.append(("moisture", f"MOISTURE - WET & NET vs DRY & NET (prices are per {PRICE_BASIS} tonne)", mt))
    stock_df = stock_table(blend, df)
    if stock_df is not None:
        T.append(("stock", "RM STOCK USE", stock_df))
    diag_df = pd.DataFrame([
        {"Diagnostic": "Fe/C ratio", "Value": round(ach["Fe_C_ratio"], 3), "Note": f"guide {FE_C_RATIO_TARGET}+/-{FE_C_RATIO_TOL}; never constrains"},
        {"Diagnostic": "Al2O3 load (kg/tHM)", "Value": round(ach["Al2O3_kg"], 2), "Note": f"needs >= {ach['Slag_min_for_Al2O3_cap']:.0f} kg slag at the {AL2O3_MAX_PCT:g}% cap"},
        {"Diagnostic": "Ks (fixed)", "Value": round(ach["Ks"], 2), "Note": "S partition slag/metal, plant value"},
        {"Diagnostic": "Predicted %S in hot metal", "Value": round(ach["S_HM_pct"], 4), "Note": "all charged S / (1 + Ks x slag/HM), in %"},
    ])
    T.append(("diagnostics", "DIAGNOSTICS (reported only)", diag_df))
    T.append(("heat", "HEAT BALANCE - hot zone below the thermal reserve zone (per tHM)", heat_table(ach)))
    T.append(("heat_rules", "HEAT BALANCE vs PLANT THUMB RULES - marginal fuel effects", heat_vs_rules(df, ach)))
    return T


def build_export_bundle(df, res, curve=None):
    status, blend, cost, ach, diag = res
    b = {"created": _dt.datetime.now(), "status": status, "diagnostics": list(diag), "inputs": df.copy(),
         "settings": settings_snapshot(), "fingerprint": _fingerprint(df), "furnace": FURNACE["name"], "cost": cost}
    if curve is None and status in ("Optimal", "Infeasible"):
        curve = sinter_curve(df)
    b["curve"] = curve
    b["choice"] = ((ach["Sinter_share_pct"], cost, ach["Coke_kg"], "Manual share" if SINTER_MANUAL["on"] else "Optimizer's choice")
                   if status == "Optimal" else None)
    if status == "Optimal":
        b.update(ach=ach, blend=dict(blend), tables=result_tables(status, blend, cost, ach, df),
                 compliance=compliance_table(ach, df), burden_full=burden_comparison(blend, df, ach))
    return b


def write_export(bundle, path, analysis=None, extra=None):
    """Real .xlsx, coloured, formatted, with charts.
    extra: optional callable(workbook) that adds dashboard sheets before the file is saved (V6 hook; no effect on the model)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.chart import BarChart, PieChart, LineChart, ScatterChart, Series, Reference
    from openpyxl.chart.marker import Marker
    from openpyxl.chart.label import DataLabelList
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.worksheet.properties import PageSetupProperties

    NAVY, BLUE, TEAL, LIGHT, PALE, GRID = "1F3864", "2F5597", "1F7A8C", "DDEBF7", "F3F8FD", "BFBFBF"
    FONT = "Calibri"
    fill = lambda h: PatternFill("solid", start_color=h, end_color=h)
    thin = Side(style="thin", color=GRID)
    BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
    TOTB = Border(left=thin, right=thin, bottom=thin, top=Side(style="medium", color=NAVY))
    GROUP_FILL = {"Iron_ore": "FCE4D6", "Sinter": "DDEBF7", "Minor": "EDEDED", "Flux": "E2EFDA",
                  "Fuel_Coke": "D9D9D9", "Fuel_NutCoke": "E7E6E6", "Fuel_PCI": "FFF2CC"}
    STATUS_STYLE = {"OK": ("C6EFCE", "006100"), "USED": ("C6EFCE", "006100"), "ON": ("C6EFCE", "006100"), "Optimal": ("C6EFCE", "006100"),
                    "Infeasible": ("FFC7CE", "9C0006"), "INPUT_ERROR": ("FFC7CE", "9C0006"), "NO_PRODUCTION": ("FFC7CE", "9C0006"),
                    "AT LIMIT": ("FFEB9C", "7F6000"), "NOTE": ("FFEB9C", "7F6000"),
                    "OUT OF LIMIT": ("FFC7CE", "9C0006"), "OFF": ("FFC7CE", "9C0006"), "not used": ("EDEDED", "595959")}
    TOTAL_WORDS = ("TOTAL", "SLAG")

    def cv(x):
        if x is None:
            return None
        if isinstance(x, (bool, np.bool_)):
            return "Yes" if x else "No"
        if isinstance(x, (float, np.floating)):
            return None if (x != x or x in (float("inf"), float("-inf"))) else float(x)
        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, str) and x[:1] in ("=", "+", "@"):
            return " " + x
        return x

    def note_w(ws, col, n):
        w = getattr(ws, "_w", {})
        w[col] = max(w.get(col, 0), int(n))
        ws._w = w

    def fit(ws, lo=9, hi=46, over=None):
        for col, n in getattr(ws, "_w", {}).items():
            cap = (over or {}).get(col, hi)
            ws.column_dimensions[L(col)].width = max(lo, min(cap, n + 2.5))

    def banner(ws, title, sub, ncols):
        ncols = max(ncols, 2)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        c = ws.cell(1, 1, title); c.font = Font(name=FONT, size=16, bold=True, color="FFFFFF")
        c.fill = fill(NAVY); c.alignment = Alignment(vertical="center", indent=1); ws.row_dimensions[1].height = 30
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
        c = ws.cell(2, 1, sub); c.font = Font(name=FONT, size=10, italic=True, color="44546A")
        c.fill = fill(LIGHT); c.alignment = Alignment(vertical="center", indent=1); ws.row_dimensions[2].height = 18
        for j in range(2, ncols + 1):
            ws.cell(1, j).fill = fill(NAVY); ws.cell(2, j).fill = fill(LIGHT)

    def section(ws, r, text, ncols):
        for j in range(1, max(ncols, 2) + 1):
            ws.cell(r, j).fill = fill(BLUE)
        c = ws.cell(r, 1, text); c.font = Font(name=FONT, size=11, bold=True, color="FFFFFF")
        c.alignment = Alignment(vertical="center", indent=1); ws.row_dimensions[r].height = 20

    def fmt_for(col):
        cl = str(col)
        if cl in ("B2", "Fe/C", "MgO/Al2O3"):
            return "0.000"
        if cl.startswith("S %"):
            return "0.0000"
        if "(t)" in cl:
            return "#,##0.0"
        return "#,##0.00"

    def table(ws, df, r0, c0=1, group_col=None, status_cols=(), sign_cols=(), row_fmt=None):
        cols = list(df.columns)
        for j, c in enumerate(cols):
            x = ws.cell(r0, c0 + j, c)
            x.font = Font(name=FONT, size=10, bold=True, color="FFFFFF"); x.fill = fill(NAVY); x.border = BOX
            x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            note_w(ws, c0 + j, min(len(str(c)), 22))
        ws.row_dimensions[r0].height = 32
        for i, row in enumerate(df.itertuples(index=False), start=1):
            r = r0 + i
            first = str(row[0])
            is_tot = first.startswith(TOTAL_WORDS)
            for j, v in enumerate(row):
                v = cv(v)
                x = ws.cell(r, c0 + j, v); x.border = TOTB if is_tot else BOX
                x.font = Font(name=FONT, size=10, bold=is_tot)
                name = cols[j]
                if isinstance(v, float):
                    x.number_format = (row_fmt or {}).get(first, fmt_for(name)); x.alignment = Alignment(horizontal="right", indent=1)
                elif isinstance(v, int):
                    x.alignment = Alignment(horizontal="right", indent=1)
                else:
                    x.alignment = Alignment(horizontal="left", vertical="center", indent=1)
                if is_tot:
                    x.fill = fill(LIGHT)
                elif i % 2 == 0:
                    x.fill = fill(PALE)
                if group_col and name == group_col and v in GROUP_FILL and not is_tot:
                    x.fill = fill(GROUP_FILL[v])
                if name in status_cols and v in STATUS_STYLE:
                    bg, fg = STATUS_STYLE[v]
                    x.fill = fill(bg); x.font = Font(name=FONT, size=10, bold=True, color=fg); x.alignment = Alignment(horizontal="center")
                if name in sign_cols and isinstance(v, float) and abs(v) > 0.005 and not first.startswith("Base"):
                    x.font = Font(name=FONT, size=10, bold=True, color=("9C0006" if v > 0 else "006100"))
                if v is not None:
                    note_w(ws, c0 + j, len(f"{v:,.2f}") if isinstance(v, float) else len(str(v)))
        return r0, r0 + 1, r0 + len(df)

    def finish(ws, tab, freeze=None):
        ws.sheet_properties.tabColor = tab
        ws.sheet_view.showGridLines = False
        ws.sheet_view.zoomScale = 90
        if freeze:
            ws.freeze_panes = freeze
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    def style_axes(ch):
        try:
            ch.x_axis.delete = False
            ch.y_axis.delete = False
        except Exception:
            pass

    def kv_note(ws, r, text, ncols, color="44546A", italic=True, height=None):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=max(ncols, 2))
        c = ws.cell(r, 1, text); c.font = Font(name=FONT, size=10, italic=italic, color=color)
        c.alignment = Alignment(wrap_text=True, vertical="top", indent=1)
        if height:
            ws.row_dimensions[r].height = height

    wb = Workbook()
    created = bundle["created"]
    optimal = bundle["status"] == "Optimal"
    sub = f"Furnace {bundle['furnace']}  |  run {created:%d %b %Y %H:%M}  |  basis: 1 tonne hot metal, dry & net  |  v11.7 ideal plant conditions"
    T = {k: (title, t) for k, title, t in bundle.get("tables", [])}
    analysis = analysis or {}

    # ------------------------------------------------ Summary
    ws = wb.active; ws.title = "Summary"
    NC = 7
    banner(ws, "MBF BURDEN OPTIMISER  -  OPTIMISED RESULTS", sub, NC)
    ws.merge_cells("A4:G4")
    c = ws["A4"]
    if optimal:
        c.value = "STATUS: OPTIMAL  -  least-cost burden found within every limit"; bg, fg = "C6EFCE", "006100"
    else:
        c.value = f"STATUS: {str(bundle['status']).upper()}  -  no optimised burden was produced (see notes below)"; bg, fg = "FFC7CE", "9C0006"
    c.font = Font(name=FONT, size=12, bold=True, color=fg); c.fill = fill(bg); c.alignment = Alignment(vertical="center", indent=1)
    for j in range(1, NC + 1):
        ws.cell(4, j).fill = fill(bg)
    ws.row_dimensions[4].height = 24
    r = 6
    if optimal:
        a_ = bundle["ach"]
        k1 = [(("Total raw-material cost (Rs/tHM)" if float(a_.get("OM_Rs_tHM", 0.0)) <= 0 else "Total cost incl. O&M (Rs/tHM)"), bundle["cost"], "#,##0.00"), ("Sinter share (%)", a_["Sinter_share_pct"], "0.00"),
              ("Total fuel (kg/tHM)", a_["Fuel_supplied"], "0.00"), ("Regular coke (kg/tHM)", a_["Coke_kg"], "0.00"), ("Slag volume (kg/tHM)", a_["slag_kg"], "0.0")]
        k2 = [("B2 = CaO/SiO2", a_["B2"], "0.000"), ("MgO in slag (%)", a_["MgO_pct"], "0.00"), ("Al2O3 in slag (%)", a_["Al2O3_pct"], "0.00"),
              ("MgO / Al2O3", a_["MgO_Al2O3"], "0.000"), ("Raw flux (kg/tHM)", a_["Raw_flux_kg"], "0.0")]
        for caption, k in (("Cost & burden", k1), ("Slag & flux", k2)):
            ws.cell(r, 1, caption).font = Font(name=FONT, size=11, bold=True, color=NAVY)
            ws.cell(r + 1, 1, "").fill = fill(PALE)
            for j, (lab, val, nf) in enumerate(k):
                x = ws.cell(r, 2 + j, lab); x.font = Font(name=FONT, size=9, bold=True, color="FFFFFF"); x.fill = fill(TEAL)
                x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); x.border = BOX
                y = ws.cell(r + 1, 2 + j, cv(val)); y.font = Font(name=FONT, size=18, bold=True, color=NAVY); y.fill = fill(PALE)
                y.alignment = Alignment(horizontal="center", vertical="center"); y.number_format = nf; y.border = BOX
            ws.row_dimensions[r].height = 30; ws.row_dimensions[r + 1].height = 38
            r += 3
        section(ws, r, "CONSTRAINT COMPLIANCE  (AT LIMIT = the constraint is binding: the optimizer is using that bound)", NC); r += 1
        hdr = [("Requirement", 1, 1), ("Target", 2, 3), ("Achieved", 4, 4), ("Status", 5, 5), ("Note", 6, 7)]
        for name, c1, c2 in hdr:
            if c2 > c1:
                ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
            x = ws.cell(r, c1, name); x.font = Font(name=FONT, size=10, bold=True, color="FFFFFF"); x.alignment = Alignment(horizontal="center")
            for j in range(c1, c2 + 1):
                ws.cell(r, j).fill = fill(NAVY); ws.cell(r, j).border = BOX
        r += 1
        for i, row in bundle["compliance"].reset_index(drop=True).iterrows():
            for (name, c1, c2), key in zip(hdr, ["Requirement", "Target", "Achieved", "Status", "Note"]):
                if c2 > c1:
                    ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
                v = cv(row[key]); x = ws.cell(r, c1, v); x.font = Font(name=FONT, size=10)
                x.alignment = Alignment(horizontal="right" if isinstance(v, float) else "left", vertical="center", indent=0 if isinstance(v, float) else 1)
                if isinstance(v, float):
                    x.number_format = "0.000"
                for j in range(c1, c2 + 1):
                    ws.cell(r, j).border = BOX
                    if i % 2 == 1:
                        ws.cell(r, j).fill = fill(PALE)
                if key == "Status" and v in STATUS_STYLE:
                    bg, fg = STATUS_STYLE[v]; x.fill = fill(bg); x.font = Font(name=FONT, size=10, bold=True, color=fg); x.alignment = Alignment(horizontal="center")
            r += 1
        r += 1
    section(ws, r, "RUN NOTES & DIAGNOSTICS", NC); r += 1
    for line in bundle["diagnostics"]:
        kv_note(ws, r, "-  " + str(line), NC, color="1F1F1F", italic=False, height=32 if len(str(line)) > 120 else 18); r += 1
    r += 1
    section(ws, r, "SHEET GUIDE  (click a name to jump)", NC); r += 1
    guide = [("Sinter Curve 0-100%", "Coke and cost at every sinter share from 0% to 100% - charts, this run starred"),
             ("Inputs", "Every input exactly as used in this run (ON/OFF, assays, moisture, price, stock)"),
             ("Optimised Burden", "Each input material next to its optimised result: kg, %, wet, water, cost, share of group; charts"),
             ("Slag & Chemistry", "Slag composition vs targets, OXIDE SOURCES table and chart (which material brings which oxide)"),
             ("Fuel Rate", "Fuel balance and the fuel-rate breakdown by rule; chart"),
             ("Fe Impact", "What the Fe content of each iron ore and sinter does to the fuel rate"),
             ("Heat Balance", "Hot-zone heat balance: every heat term, carbon needed vs charged, heat-balance minimum fuel vs plant rules"),
             ("Moisture", "Wet & net vs dry & net, water charged with the burden"),
             ("RM Stock", "Stock use per material (if stock was entered)"),
             ("Sinter Sweep", "Model re-solved at each sinter share - coke and cost charts"),
             ("Oxides by Sinter %", "CaO, SiO2, Al2O3 charged by each material at each sinter share - stacked charts"),
             ("Assay Sensitivity", "Fe / SiO2 / Al2O3 / CaO / MgO / moisture of a group shifted"),
             ("Price Sensitivity", "Price of one material or group shifted by +/- %"),
             ("Tornado", "Every driver moved one at a time, ranked by cost swing"),
             ("Sinter Break-even", "Sinter prices at which the optimizer drops to the floor / reaches the top"),
             ("Run Settings", "Every model setting in force - use it to compare two exports like for like")]
    have = {"Inputs", "Run Settings"} | ({"Sinter Curve 0-100%"} if bundle.get("curve") is not None else set())
    if optimal:
        have |= {"Optimised Burden", "Slag & Chemistry", "Fuel Rate", "Fe Impact", "Moisture", "Heat Balance"}
        if "stock" in T:
            have.add("RM Stock")
    for key, sheet, _ch in ANALYSIS_SHEETS:
        if analysis.get(key):
            have.add(sheet)
    for nm, desc in guide:
        if nm not in have:
            continue
        x = ws.cell(r, 1, nm); x.hyperlink = f"#'{nm}'!A1"; x.font = Font(name=FONT, size=10, bold=True, color="0563C1", underline="single")
        x.border = BOX
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=NC)
        y = ws.cell(r, 2, desc); y.font = Font(name=FONT, size=10); y.alignment = Alignment(indent=1)
        r += 1
    ws.column_dimensions["A"].width = 36
    for j in range(2, 8):
        ws.column_dimensions[L(j)].width = 20
    finish(ws, NAVY)

    # ------------------------------------------------ Inputs
    ws = wb.create_sheet("Inputs")
    d = bundle["inputs"]
    inp = pd.DataFrame({"Material": d.index, "Group": d["Group"].values,
                        "Availability": ["ON" if bool(v) else "OFF" for v in d["Available"]],
                        "Fe %": d["Fe"].values, "CaO %": d["CaO"].values, "MgO %": d["MgO"].values, "SiO2 %": d["SiO2"].values,
                        "Al2O3 %": d["Al2O3"].values, "Mn %": d["Mn"].values, "S %": d["S"].values, "FC %": d["FC"].values,
                        "Moisture %": d["Moisture_Pct"].values, "Price Rs/t (dry)": d["Price_Rs_t"].values, "Fines %": d["Fines_Pct"].values,
                        "Fines credit Rs/t": d["Fines_Credit_Rs_t"].values, "RM stock (t)": d["RM_Stock"].values})
    banner(ws, "INPUTS  -  exactly as used in this run", "Assays are % on a dry basis. Prices per dry tonne (unless the run settings say wet). Blank RM stock = unlimited. Fines % / credit (optional): price used = (price - fines x credit) / (1 - fines).", len(inp.columns))
    r0, rf, rl = table(ws, inp, 4, group_col="Group", status_cols=("Availability",))
    ws.auto_filter.ref = f"A{r0}:{L(len(inp.columns))}{rl}"
    fit(ws, over={1: 30}); finish(ws, "7F7F7F", freeze=f"B{rf}")

    # ------------------------------------------------ Run Settings
    ws = wb.create_sheet("Run Settings")
    banner(ws, "RUN SETTINGS  -  everything in force for this run", "Compare two exports by comparing these values; each export carries its own snapshot.", 4)
    r = 4
    cur = None
    for sec, name, val, nt in bundle["settings"]:
        if sec != cur:
            r += 1 if cur is not None else 0
            section(ws, r, sec.upper(), 4); r += 1
            for j, h in enumerate(["", "Setting", "Value", "Note"], start=1):
                x = ws.cell(r, j, h if j > 1 else None)
                x.font = Font(name=FONT, size=10, bold=True, color="FFFFFF"); x.fill = fill(NAVY); x.border = BOX; x.alignment = Alignment(horizontal="center")
            r += 1; cur = sec
        vals = [None, name, cv(val), nt]
        for j, v in enumerate(vals, start=1):
            x = ws.cell(r, j, v); x.border = BOX; x.font = Font(name=FONT, size=10, bold=(j == 2))
            x.alignment = Alignment(horizontal="left", vertical="center", wrap_text=(j == 4), indent=1 if j > 1 else 0)
            if (r % 2) == 0:
                x.fill = fill(PALE)
            if v is not None:
                note_w(ws, j, len(str(v)) if j != 4 else min(len(str(v)), 70))
        r += 1
    fit(ws, over={2: 42, 3: 46, 4: 78}); ws.column_dimensions["A"].width = 3
    finish(ws, "7F7F7F", freeze="A4")

    if optimal:
        ach = bundle["ach"]
        # -------------------------------------------- Optimised Burden
        ws = wb.create_sheet("Optimised Burden", 2)
        bf = bundle["burden_full"]
        banner(ws, "OPTIMISED BURDEN  -  inputs next to results (per tonne of hot metal)",
               "USED = the optimizer charged it; not used = ON but not needed at least cost; OFF = switched off. Dry kg is the model basis; wet = dry / (1 - moisture).", len(bf.columns))
        r0, rf, rl = table(ws, bf, 4, group_col="Group", status_cols=("Availability", "Result"))
        ws.auto_filter.ref = f"A{r0}:{L(len(bf.columns))}{rl - 1}"
        used = bf[(bf["Result"] == "USED")].sort_values("Dry kg/tHM", ascending=False)[["Material", "Group", "Dry kg/tHM", "Cost Rs/tHM"]].reset_index(drop=True)
        rr = rl + 3
        section(ws, rr - 1, "USED MATERIALS - RANKED (drives the charts)", 4)
        u0, uf, ul = table(ws, used, rr, group_col="Group")
        if len(used):
            ch = BarChart(); ch.type = "col"; ch.style = 10; ch.title = "Optimised burden - dry kg per tHM"
            ch.y_axis.title = "kg/tHM"; ch.legend = None; ch.height = 9; ch.width = 22
            ch.add_data(Reference(ws, min_col=3, min_row=u0, max_row=ul), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=1, min_row=uf, max_row=ul)); style_axes(ch)
            ws.add_chart(ch, f"F{rr - 1}")
            pie = PieChart(); pie.title = "Where the cost goes (Rs/tHM)"; pie.height = 9; pie.width = 15
            pie.add_data(Reference(ws, min_col=4, min_row=u0, max_row=ul), titles_from_data=True)
            pie.set_categories(Reference(ws, min_col=1, min_row=uf, max_row=ul))
            pie.dataLabels = DataLabelList(); pie.dataLabels.showPercent = True; pie.dataLabels.showVal = False
            pie.dataLabels.showCatName = False; pie.dataLabels.showSerName = False; pie.dataLabels.showLeaderLines = False
            ws.add_chart(pie, f"F{rr + 19}")
        fit(ws, over={1: 30}); finish(ws, "2F5597", freeze=f"C{rf}")

        # -------------------------------------------- Slag & Chemistry (+ oxide sources)
        ws = wb.create_sheet("Slag & Chemistry", 3)
        ox = T["oxides"][1]
        NCS = max(5, len(ox.columns))
        banner(ws, "SLAG & CHEMISTRY", "Slag basis: charged SiO2 less the SiO2 of Si reduced into the metal. AT LIMIT = binding. Oxide sources show which material brings which oxide.", NCS)
        section(ws, 4, T["slag"][0], NCS)
        s0, sf, sl = table(ws, T["slag"][1], 5, status_cols=("Status",),
                           row_fmt={"B2 = CaO/SiO2": "0.0000", "B4 = (CaO+MgO)/(SiO2+Al2O3)": "0.000", "MgO/Al2O3": "0.000"})
        rr = sl + 2
        section(ws, rr, T["oxides"][0], NCS)
        o0, of_, ol = table(ws, ox, rr + 1, group_col="Group")
        pct_cols = [j for j, c_ in enumerate(ox.columns, start=1) if c_.endswith("% of total")]
        for j in pct_cols:                                         # highlight the largest source of each oxide
            vals_ = [(ws.cell(rw, j).value, rw) for rw in range(of_, ol - 2) if isinstance(ws.cell(rw, j).value, (int, float))]
            if vals_:
                vmax, rmax = max(vals_)
                ws.cell(rmax, j).fill = fill("FFEB9C"); ws.cell(rmax, j).font = Font(name=FONT, size=10, bold=True, color="7F6000")
        if ox.attrs.get("note"):
            kv_note(ws, ol + 1, ox.attrs["note"], NCS, height=30)
        body_ = ox[~ox["Material"].isin(["TOTAL CHARGED", "Less: SiO2 reduced to Si in hot metal", "SLAG"])]
        share = pd.DataFrame({"Oxide": CHART_OXIDES})
        for _, r_ in body_.iterrows():
            share[r_["Material"]] = [float(r_[f"{o} % of total"]) for o in CHART_OXIDES]
        share = share.loc[:, [c_ for c_ in share.columns if c_ == "Oxide" or share[c_].abs().max() > 1e-6]]
        sr = ol + 3
        ws.cell(sr, 1, "Share of each oxide by material (% of total charged) - drives the chart").font = Font(name=FONT, size=10, bold=True, color=NAVY)
        s0_, sf_, sl_ = table(ws, share, sr + 1)
        ch = BarChart(); ch.type = "bar"; ch.grouping = "percentStacked"; ch.overlap = 100; ch.style = 10
        ch.title = "Where the CaO, SiO2 and Al2O3 come from (% of total charged)"; ch.height = 7.5; ch.width = 24
        ch.add_data(Reference(ws, min_col=2, max_col=len(share.columns), min_row=s0_, max_row=sl_), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=sf_, max_row=sl_)); style_axes(ch)
        try:
            ch.x_axis.scaling.orientation = "maxMin"
        except Exception:
            pass
        ws.add_chart(ch, f"A{sl_ + 2}")
        ol = sl_ + 17
        chem = pd.DataFrame([{"Item": "Fe charged (all materials)", "kg/tHM": ach["Fe_kg"]}, {"Item": "Fe charged (ore + sinter + minor)", "kg/tHM": ach["Fe_burden_kg"]},
                             {"Item": "CaO", "kg/tHM": ach["CaO_kg"]}, {"Item": "MgO", "kg/tHM": ach["MgO_kg"]},
                             {"Item": "SiO2 charged (before Si reduction)", "kg/tHM": ach["SiO2_charged_kg"]}, {"Item": "Al2O3", "kg/tHM": ach["Al2O3_kg"]},
                             {"Item": "Mn", "kg/tHM": ach["Mn_kg"]}, {"Item": "S (all materials)", "kg/tHM": ach["S_kg"]}, {"Item": "Fixed carbon", "kg/tHM": ach["FC_kg"]}])
        rr = ol + 4
        section(ws, rr, "CHARGED CHEMISTRY (kg per tHM)", NCS); table(ws, chem, rr + 1)
        rr = rr + len(chem) + 3
        section(ws, rr, T["diagnostics"][0], NCS)
        table(ws, T["diagnostics"][1], rr + 1, row_fmt={"Fe/C ratio": "0.000", "Ks (fixed)": "0.00",
                                                        "Predicted %S in hot metal": "0.0000"})
        fit(ws, over={1: 38}); finish(ws, "548235")

        # -------------------------------------------- Fuel Rate
        ws = wb.create_sheet("Fuel Rate", 4)
        banner(ws, "FUEL RATE  -  balance and rule breakdown (kg per tHM)",
               "Red = the rule ADDS fuel, green = the rule SAVES fuel, relative to the plant reference point.", 3)
        section(ws, 4, T["fuel"][0], 3)
        f0, ff, fl = table(ws, T["fuel"][1], 5)
        rr = fl + 2
        section(ws, rr, T["breakdown"][0], 3)
        b0, bfst, bl = table(ws, T["breakdown"][1], rr + 1, sign_cols=("kg/tHM",))
        if bl - bfst >= 2:
            ch = BarChart(); ch.type = "bar"; ch.style = 10; ch.title = "Fuel-rate terms (kg per tHM; + adds fuel)"; ch.legend = None
            ch.height = 11; ch.width = 20
            ch.add_data(Reference(ws, min_col=2, min_row=b0, max_row=bl - 1), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=1, min_row=bfst + 1, max_row=bl - 1)); style_axes(ch)
            ws.add_chart(ch, f"A{bl + 3}")
        fit(ws, over={1: 42, 2: 14, 3: 78}); finish(ws, "C55A11")

        # -------------------------------------------- Fe Impact
        ws = wb.create_sheet("Fe Impact", 5)
        fi = T["fe_impact"][1]
        banner(ws, "FE IMPACT ON FUEL  -  iron ore and sinter",
               "Green = the Fe content of that material SAVES fuel vs the reference Fe; red = it ADDS fuel.", len(fi.columns))
        table(ws, fi, 4, group_col="Group", sign_cols=("Fuel effect kg/tHM",))
        fit(ws, over={1: 34, 8: 56}); finish(ws, "BF8F00")

        # -------------------------------------------- Heat Balance
        if "heat" in T:
            ws = wb.create_sheet("Heat Balance", 6)
            ht = T["heat"][1]
            banner(ws, "HEAT BALANCE  -  hot zone below the thermal reserve zone (per tonne of hot metal)",
                   ("Mode: " + HEAT["mode"] + ("  |  CALIBRATED" if HEAT["calibrated"] else
                    "  |  PLACEHOLDER VALUES - NOT CALIBRATED: use differences between runs, not the absolute surplus")), 5)
            section(ws, 4, T["heat"][0], 5)
            h0, hf, hl = table(ws, ht, 5, sign_cols=())
            for rw in range(hf, hl + 1):
                if str(ws.cell(rw, 1).value).startswith("SURPLUS"):
                    v_ = ws.cell(rw, 4).value
                    good = isinstance(v_, (int, float)) and v_ >= 0
                    for j in range(1, 5):
                        ws.cell(rw, j).fill = fill("C6EFCE" if good else "FFC7CE")
                        ws.cell(rw, j).font = Font(name=FONT, size=10, bold=True, color="006100" if good else "9C0006")
            kv_note(ws, hl + 1, ht.attrs.get("note", ""), 5, height=44)
            rr = hl + 3
            section(ws, rr, T["heat_rules"][0], 5)
            q0, qf, ql = table(ws, T["heat_rules"][1], rr + 1)
            rr = ql + 2
            hn_ = (bundle.get("curve").attrs.get("heat_note") if bundle.get("curve") is not None else None)
            if hn_:
                kv_note(ws, rr, "0-100% SINTER CURVE: " + hn_, 5, color="1F3864", italic=False, height=30); rr += 2
            if "D" in ach["Heat"]:
                dd = pd.DataFrame({"Term": [HEAT_TERM_LABELS[k] for k in ach["Heat"]["D"]],
                                   "MJ/tHM": [v / 1000.0 for v in ach["Heat"]["D"].values()]})
                section(ws, rr, "HEAT DEMAND BY TERM (drives the chart)", 5)
                d0, df_, dl = table(ws, dd, rr + 1)
                ch = BarChart(); ch.type = "bar"; ch.style = 10; ch.title = "Hot-zone heat demand (MJ per tHM)"; ch.legend = None
                ch.height = 9; ch.width = 20
                ch.add_data(Reference(ws, min_col=2, min_row=d0, max_row=dl), titles_from_data=True)
                ch.set_categories(Reference(ws, min_col=1, min_row=df_, max_row=dl)); style_axes(ch)
                try:
                    ch.x_axis.scaling.orientation = "maxMin"
                except Exception:
                    pass
                ws.add_chart(ch, f"F{rr}")
            fit(ws, over={1: 60, 5: 70}); finish(ws, "C00000")

        # -------------------------------------------- Moisture / RM stock
        if "moisture" in T:
            ws = wb.create_sheet("Moisture", 6)
            banner(ws, "MOISTURE  -  wet & net vs dry & net", f"Model quantities are dry & net. Prices are per {PRICE_BASIS} tonne. Water = wet - dry.", len(T["moisture"][1].columns))
            table(ws, T["moisture"][1], 4, group_col="Group")
            fit(ws, over={1: 46}); finish(ws, "1F7A8C")
        if "stock" in T:
            ws = wb.create_sheet("RM Stock")
            banner(ws, "RM STOCK USE", "Share of group used vs share of group stock is shown for stock-balanced groups.", len(T["stock"][1].columns))
            table(ws, T["stock"][1], 4, group_col="Group")
            fit(ws); finish(ws, "7030A0")

    # ------------------------------------------------ analysis sheets
    for key, name, chart_cols in ANALYSIS_SHEETS:
        item = analysis.get(key)
        if not item:
            continue
        if key == "sweep_oxides":
            ws2 = wb.create_sheet(name)
            same = item.get("fingerprint") == bundle["fingerprint"]
            banner(ws2, "OXIDES BY SINTER SHARE - kg charged per tHM, by material",
                   f"Generated {item['time']:%d %b %Y %H:%M}  |  inputs identical to this run: {'YES' if same else 'NO'}  |  charged basis (before SiO2 reduction to Si)", 12)
            rr = 4
            for ox in CHART_OXIDES:
                pv = oxides_by_sinter_pivot(item["df"], ox)
                if pv is None:
                    continue
                section(ws2, rr, f"{ox} - kg per tHM charged, by material", max(len(pv.columns), 4))
                p0, pf, pl = table(ws2, pv, rr + 1)
                mat_cols = [j for j, c_ in enumerate(pv.columns, start=1) if c_ not in ("Point", "Sinter %", "TOTAL")]
                first_pt = pf + 1 if str(ws2.cell(pf, 1).value) == "optimizer's choice" else pf
                if mat_cols and pl >= first_pt:
                    ch = BarChart(); ch.type = "col"; ch.grouping = "stacked"; ch.overlap = 100; ch.style = 10
                    ch.title = f"{ox} charged by material vs sinter share (kg/tHM)"; ch.height = 8; ch.width = 20
                    for j in mat_cols:
                        ch.add_data(Reference(ws2, min_col=j, min_row=first_pt, max_row=pl), titles_from_data=False)
                        ch.series[-1].tx = None
                    from openpyxl.chart.series import SeriesLabel
                    for s_, j in zip(ch.series, mat_cols):
                        s_.tx = SeriesLabel(v=str(ws2.cell(p0, j).value))
                    ch.set_categories(Reference(ws2, min_col=2, min_row=first_pt, max_row=pl)); style_axes(ch)
                    ws2.add_chart(ch, f"{L(len(pv.columns) + 2)}{rr}")
                rr = max(pl + 3, rr + 18)
            fit(ws2, over={1: 20}); finish(ws2, "548235")
            continue
        ws2 = wb.create_sheet(name)
        dfa = item["df"]
        same = item.get("fingerprint") == bundle["fingerprint"]
        banner(ws2, item["title"].upper(), f"Generated {item['time']:%d %b %Y %H:%M}  |  inputs identical to this run: {'YES' if same else 'NO - inputs changed after this table was made'}",
               min(len(dfa.columns), 14))
        r0, rf, rl = table(ws2, dfa, 4, status_cols=("Status",), sign_cols=("Cost vs base Rs", "Low vs base Rs", "High vs base Rs"))
        note_ = dfa.attrs.get("note")
        if note_:
            kv_note(ws2, rl + 1, note_, min(len(dfa.columns), 12), color="9C0006", italic=False, height=40)
        for colname in ("Cost Rs/tHM", "Fuel kg", "Swing Rs"):
            if colname in dfa.columns and rl > rf:
                cc = list(dfa.columns).index(colname) + 1
                ws2.conditional_formatting.add(f"{L(cc)}{rf}:{L(cc)}{rl}",
                                               ColorScaleRule(start_type="min", start_color="C6EFCE", mid_type="percentile", mid_value=50,
                                                              mid_color="FFFFFF", end_type="max", end_color="F8CBAD"))
        anchor = rl + 3
        if key == "tornado" and rl >= rf and "Swing Rs" in dfa.columns:
            cc = list(dfa.columns).index("Swing Rs") + 1
            ch = BarChart(); ch.type = "bar"; ch.style = 10; ch.title = "Tornado - cost swing Rs/tHM (largest first)"; ch.legend = None
            ch.height = max(8, 0.55 * len(dfa)); ch.width = 22
            ch.add_data(Reference(ws2, min_col=cc, min_row=r0, max_row=rl), titles_from_data=True)
            ch.set_categories(Reference(ws2, min_col=1, min_row=rf, max_row=rl)); style_axes(ch)
            try:
                ch.y_axis.scaling.orientation = "minMax"; ch.x_axis.scaling.orientation = "maxMin"
            except Exception:
                pass
            ws2.add_chart(ch, f"A{anchor}")
        if chart_cols and rl - rf >= 2:
            first = rf + 1 if key == "sweep" else rf
            k_ = 0
            for ttl, colname in chart_cols:
                if colname not in dfa.columns:
                    continue
                cc = list(dfa.columns).index(colname) + 1
                lc = LineChart(); lc.title = ttl; lc.style = 12; lc.height = 8; lc.width = 15; lc.legend = None
                if key == "sweep":                       # skip the 'optimizer's choice' row so data and categories line up
                    lc.add_data(Reference(ws2, min_col=cc, min_row=first, max_row=rl), titles_from_data=False)
                else:
                    lc.add_data(Reference(ws2, min_col=cc, min_row=r0, max_row=rl), titles_from_data=True)
                lc.set_categories(Reference(ws2, min_col=1, min_row=first, max_row=rl)); style_axes(lc)
                ws2.add_chart(lc, f"A{anchor + 18 * k_}")
                k_ += 1
        fit(ws2, over={1: 30}); finish(ws2, "BF8F00", freeze=f"B{rf}")

    # ------------------------------------------------ Sinter Curve 0-100% (every export)
    curve_df = bundle.get("curve")
    if curve_df is not None:
        ws3 = wb.create_sheet("Sinter Curve 0-100%", 1)
        cols3 = [c_ for c_ in ["Sinter %", "Status", "Cost Rs/tHM", "Coke kg", "Total fuel kg", "Ore kg", "Sinter kg", "Raw flux kg",
                               "Acid flux kg", "Slag kg", "B2", "Al2O3 %", "MgO %", "Heat-min coke kg", "Heat C surplus kg"] if c_ in curve_df.columns]
        tv = curve_df[cols3]
        banner(ws3, "SINTER CURVE 0-100%  -  coke and cost at every sinter share",
               "Model re-solved with the sinter share pinned at each value; every other limit and rule as in this run. Blank = infeasible.", len(cols3))
        kv_note(ws3, 3, curve_df.attrs.get("note", ""), len(cols3), color="9C0006", italic=False, height=30)
        r0, rf, rl = table(ws3, tv, 5, status_cols=("Status",))
        ch_row = rl + 3
        ch_ = bundle.get("choice")
        if ch_ is not None:
            ws3.cell(ch_row, 1, f"{ch_[3]} (this run)").font = Font(name=FONT, size=10, bold=True, color="548235")
            for j, (lab, v) in enumerate((("Sinter %", ch_[0]), ("Cost Rs/tHM", ch_[1]), ("Coke kg", ch_[2])), start=2):
                ws3.cell(ch_row - 1, j, lab).font = Font(name=FONT, size=9, bold=True)
                x = ws3.cell(ch_row, j, float(v)); x.number_format = "#,##0.00"; x.border = BOX
        for k, (col, ttl, ylab, colr) in enumerate((("Coke kg", "Regular coke vs sinter share (kg/tHM)", "kg per tHM", "2F5597"),
                                                   ("Cost Rs/tHM", "Burden cost vs sinter share (Rs/tHM)", "Rs per tHM", "C55A11"))):
            if col not in tv.columns:
                continue
            cc = list(tv.columns).index(col) + 1
            sc = ScatterChart(); sc.title = ttl; sc.style = 13; sc.height = 9; sc.width = 17
            sc.x_axis.title = "Sinter share of (sinter + ore), %"; sc.y_axis.title = ylab
            sc.x_axis.scaling.min = 0; sc.x_axis.scaling.max = 100; sc.x_axis.majorUnit = 10
            sc.display_blanks = "gap"
            sc.x_axis.number_format = "0"; sc.y_axis.number_format = "#,##0"
            yv = pd.to_numeric(tv[col], errors="coerce").dropna()
            if len(yv):
                span_ = max(float(yv.max() - yv.min()), 1.0)
                u_ = 500.0 if span_ > 2000 else (100.0 if span_ > 400 else 10.0)
                sc.y_axis.scaling.min = float(np.floor((yv.min() - 0.08 * span_) / u_) * u_)
                sc.y_axis.scaling.max = float(np.ceil((yv.max() + 0.08 * span_) / u_) * u_)
            s1 = Series(Reference(ws3, min_col=cc, min_row=rf, max_row=rl), Reference(ws3, min_col=1, min_row=rf, max_row=rl), title=col.replace(" Rs/tHM", ""))
            s1.marker = Marker(symbol="circle", size=4); s1.graphicalProperties.line.solidFill = colr; s1.graphicalProperties.line.width = 22000
            s1.marker.graphicalProperties.solidFill = colr; s1.marker.graphicalProperties.line.solidFill = colr
            sc.series.append(s1)
            if ch_ is not None:
                jv = 3 if col == "Cost Rs/tHM" else 4
                s2 = Series(Reference(ws3, min_col=jv, min_row=ch_row, max_row=ch_row), Reference(ws3, min_col=2, min_row=ch_row, max_row=ch_row),
                            title=ch_[3])
                s2.marker = Marker(symbol="diamond", size=12); s2.marker.graphicalProperties.solidFill = "548235"
                s2.marker.graphicalProperties.line.solidFill = "548235"; s2.graphicalProperties.line.noFill = True
                sc.series.append(s2)
            style_axes(sc)
            ws3.add_chart(sc, f"{'A' if k == 0 else 'J'}{ch_row + 2}")
        fit(ws3, over={2: 14}); ws3.column_dimensions["A"].width = 26; finish(ws3, "C55A11", freeze=f"A{rf}")

    if extra is not None:
        extra(wb)
    wb.move_sheet("Run Settings", offset=len(wb.sheetnames))
    wb.properties.title = "MBF Burden Optimiser - Optimised Results"
    wb.properties.creator = "MBF Burden Optimiser v11.7"
    wb.save(path)
    return path



# ================================================================
# 11. SELF-TEST  (proves the core still works after any edit)
#     run_self_tests()           quick set, runs every time the cell loads
#     run_self_tests(full=True)  adds the slow studio checks (tornado, break-even)
# ================================================================
def run_self_tests(verbose=True, full=False):
    """Always tests the full plant rule set; the user's rule switches are restored afterwards."""
    user_terms = dict(FUEL_TERMS)
    user_heat = copy.deepcopy(HEAT)
    FUEL_TERMS.update(FUEL_TERMS_DEFAULT)
    HEAT.clear(); HEAT.update(copy.deepcopy(HEAT_DEFAULT))
    try:
        return _run_self_tests(verbose, full)
    finally:
        FUEL_TERMS.clear(); FUEL_TERMS.update(user_terms)
        HEAT.clear(); HEAT.update(user_heat)


def _run_self_tests(verbose=True, full=False):
    results = []
    saved = dict(STOCK)

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    try:
        STOCK.update(plan_hm_tonnes=0.0, balance=1.0)
        base = plant_df.copy()
        st, blend, cost, ach, diag = solve_mbf(base)
        check("demo data solves to Optimal", st == "Optimal", st)
        check("no sinter-limit note when the whole guard-rail range is usable", not any("can only run" in x for x in diag))
        check("every free-choice run says what decided the sinter share", any(x.startswith("SINTER DECISION") for x in diag))
        if st == "Optimal":
            tol = 1e-4
            check("Fe closure = 96.5 per 100 kg HM, exact", abs(ach["Fe_burden_kg"] - 965.0) < 1e-3, f"{ach['Fe_burden_kg']:.4f}")
            check("PCI fixed", abs(ach["PCI_kg"] - PCI_FIXED_KGTHM) < 1e-6)
            check("B2 within band", BASICITY_MIN - tol <= ach["B2"] <= BASICITY_MAX + tol, f"{ach['B2']:.4f}")
            check("MgO within band", MGO_MIN_PCT - tol <= ach["MgO_pct"] <= MGO_MAX_PCT + tol, f"{ach['MgO_pct']:.3f}")
            check("Al2O3 within 17-18.5", AL2O3_MIN_PCT - tol <= ach["Al2O3_pct"] <= AL2O3_MAX_PCT + tol, f"{ach['Al2O3_pct']:.3f}")
            check("sinter share inside the 50-80% guard rails", 50 - 1e-3 <= ach["Sinter_share_pct"] <= 80 + 1e-3, f"{ach['Sinter_share_pct']:.2f}")
            check("fuel supplied == fuel rule (exact)", abs(ach["Fuel_supplied"] - ach["Fuel_rule"]) < 0.05, f"{ach['Fuel_supplied']:.2f} vs {ach['Fuel_rule']:.2f}")
            check("toggled-OFF materials unused", all(blend[m] < 1e-9 for m in base.index if not base.loc[m, "Available"]))
            check("carbon above stoichiometric floor", ach["FC_kg"] >= ach["FC_floor"] - 1e-6)
            check("sulphur diagnostic in a plausible % range", 0.0 < ach["S_HM_pct"] < 0.3, f"{ach['S_HM_pct']:.4f}%")
            check("MgO/Al2O3 reported and inside the 0.40-0.55 guide on the demo", MGO_AL2O3_GUIDE[0] <= ach["MgO_Al2O3"] <= MGO_AL2O3_GUIDE[1], f"{ach['MgO_Al2O3']:.3f}")
            SMg = dict(SINTER_MANUAL)
            try:
                grid_best = float("inf")
                for S_ in np.arange(50.0, 80.01, 0.5):
                    SINTER_MANUAL.update(on=True, pct=float(S_))
                    r_ = solve_mbf(base, explain=False)
                    if r_[0] == "Optimal":
                        grid_best = min(grid_best, r_[2])
            finally:
                SINTER_MANUAL.clear(); SINTER_MANUAL.update(SMg)
            check("free sinter choice is never dearer than the best of a 0.5-pt grid of pinned shares", cost <= grid_best + 0.01, f"{cost:.2f} vs {grid_best:.2f}")

            ox = oxide_sources_table(blend, base, ach)
            slag_row = ox[ox["Material"] == "SLAG"].iloc[0]
            body = ox[~ox["Material"].isin(["TOTAL CHARGED", "Less: SiO2 reduced to Si in hot metal", "SLAG"])]
            ok_ = (abs(slag_row["CaO kg"] - ach["CaO_kg"]) < 1e-2 and abs(slag_row["SiO2 kg"] - ach["slag_SiO2_kg"]) < 1e-2
                   and abs(slag_row["Al2O3 kg"] - ach["Al2O3_kg"]) < 1e-2 and abs(slag_row["MgO kg"] - ach["MgO_kg"]) < 1e-2)
            check("oxide sources: SLAG row equals the slag chemistry table", ok_)
            check("oxide sources: material shares of each oxide add to 100%",
                  all(abs(body[f"{o} % of total"].sum() - 100.0) < 0.1 for o in OXIDES), str([round(body[f"{o} % of total"].sum(), 2) for o in OXIDES]))
            check("oxide sources: charged SiO2 less SiO2 reduced = slag SiO2",
                  abs(body["SiO2 kg"].sum() - ach["SiO2_reduced_kg"] - ach["slag_SiO2_kg"]) < 1e-2)

        zero = {m: 0.0 for m in base.index}
        check("thumb rule: +10 pts sinter in (sinter + ore) = -10 kg fuel, and vice versa",
              abs(fuel_terms(zero, base, 330, 65)["sinter_share"] - fuel_terms(zero, base, 330, 75)["sinter_share"] - 10.0) < 1e-9
              and abs(fuel_terms(zero, base, 330, 55)["sinter_share"] - fuel_terms(zero, base, 330, 65)["sinter_share"] - 10.0) < 1e-9)
        check("plant review: base fuel 545 for both furnaces, B2 band 0.99-1.01, Ks fixed at 25",
              all(v["base"] == 545.0 for v in FURNACE_PROFILES.values()) and (BASICITY_MIN, BASICITY_MAX) == (0.99, 1.01) and KS_FIXED == 25.0)
        if st == "Optimal":
            check("predicted S = all charged S / (1 + Ks x slag / HM)",
                  abs(ach["S_HM_pct"] - ach["S_kg"] / (1 + KS_FIXED * ach["slag_kg"] / HM_BASIS_KG) / 10.0) < 1e-12, f"{ach['S_HM_pct']:.4f}%")
        check("slag rule: +100 kg slag over the reference = +18 kg fuel", abs(fuel_terms(zero, base, 430, 65)["slag"] - 18.0) < 1e-9)
        Xl = dict(zero); Xl["Limestone"] = RAW_FLUX_REF_KGTHM + 100.0
        check("raw flux rule: 100 kg limestone over the reference = +30 kg fuel", abs(fuel_terms(Xl, base, 330, 65)["raw_flux"] - 30.0) < 1e-9)
        check("slag term is ON by default and raw flux penalty is 0.30", FUEL_TERMS_DEFAULT["slag"] is True and abs(FUEL_PER_KG_RAW_FLUX - 0.30) < 1e-12)
        check("removed rules are not in the model (PCI rate/FC/moisture, coke ash/CSR, DRI, hot blast, offset, burden-Fe form, cost model)",
              not ({"pci_rate", "pci_fc", "pci_moisture", "coke_ash", "coke_csr", "dri", "hbt", "offset", "burden_fe"} & set(FUEL_TERM_NAMES))
              and "cost_model_compare" not in globals() and "FE_FORM" not in globals()
              and "Ash_Pct" not in plant_df.columns and "CSR" not in plant_df.columns)
        _grp = sorted(n for ns in FUEL_TERM_GROUPS.values() for n in ns)
        FT0 = dict(FUEL_TERMS)
        try:
            FUEL_TERMS["raw_flux"] = False; FUEL_TERMS["other_moisture"] = False
            so_, _, _, ao_, do_ = solve_mbf(base, explain=False)
            check("rule switches: an OFF rule contributes exactly 0 and the result names it",
                  so_ == "Optimal" and ao_["Fuel_terms"]["raw_flux"] == 0 and ao_["Fuel_terms"]["other_moisture"] == 0
                  and any("SWITCHED OFF" in x and "Raw flux" in x for x in do_) and abs(ao_["Fuel_supplied"] - ao_["Fuel_rule"]) < 0.05)
        finally:
            FUEL_TERMS.clear(); FUEL_TERMS.update(FT0)
        check("every fuel term belongs to exactly one group", _grp == sorted(FUEL_TERM_NAMES), f"{len(_grp)} vs {len(FUEL_TERM_NAMES)}")

        d = base.copy(); d.loc["Nut_Coke", ["Fe", "CaO", "MgO", "SiO2", "Al2O3", "FC", "Available"]] = [2.2, 0.4, 0.2, 9.3, 5.0, 75.0, True]
        stn, bn, _, an, _ = solve_mbf(d, explain=False)
        check("nut coke ON -> exactly 40 kg/tHM", stn == "Optimal" and abs(an["NutCoke_kg"] - NUT_COKE_KGTHM) < 1e-6,
              f"{an['NutCoke_kg']:.3f}" if stn == "Optimal" else stn)

        d = base.copy(); d.loc["Coke3", "Available"] = False
        check("no coke ON -> NO_PRODUCTION", solve_mbf(d)[0] == "NO_PRODUCTION")
        d = base.copy(); d.loc["Sinter1", "Available"] = False
        check("no sinter ON -> NO_PRODUCTION", solve_mbf(d)[0] == "NO_PRODUCTION")
        d = base.copy(); d.loc["Sponge_Iron", "Available"] = True
        check("ON without assay/price -> INPUT_ERROR", solve_mbf(d)[0] == "INPUT_ERROR")
        d = base.copy(); d.loc["Mn_Ore", "Available"] = True
        st3, _, _, a3, _ = solve_mbf(d, explain=False)
        check("cheap Minor ON but not needed -> not used", st3 == "Optimal" and a3["Minor_kg"] < 1e-6, f"minor {a3['Minor_kg']:.2f}" if st3 == "Optimal" else st3)
        MINOR_SAVED = MINOR_DEMAND_ONLY
        try:
            globals()["MINOR_DEMAND_ONLY"] = False
            st4, _, _, a4, _ = solve_mbf(d, explain=False)
            check("MINOR_DEMAND_ONLY=False: cost decides, cap respected, burden not replaced",
                  st4 == "Optimal" and 0 < a4["Minor_kg"] <= MINOR_MAX_KGTHM + 1e-6 and a4["Ore_kg"] + a4["Sinter_kg"] > 500,
                  f"minor {a4['Minor_kg']:.1f}" if st4 == "Optimal" else st4)
        finally:
            globals()["MINOR_DEMAND_ONLY"] = MINOR_SAVED
        need = base.copy()
        need.loc["Quartzite", "Available"] = False; need.loc["Sinter1", "SiO2"] = 5.0
        need.loc["BHQ", ["Fe", "SiO2", "Al2O3", "CaO", "MgO", "Available"]] = [45.0, 45.0, 0.8, 0.5, 0.2, True]
        without = need.copy(); without.loc["BHQ", "Available"] = False
        s_wo = solve_mbf(without, explain=False)[0]
        s_w, _, _, a_w, _ = solve_mbf(need, explain=False)
        check("Minor used, and only as much as needed, when the limits cannot be met without it",
              s_wo == "Infeasible" and s_w == "Optimal" and 1.0 < a_w["Minor_kg"] < MINOR_MAX_KGTHM - 1.0,
              f"without: {s_wo}; with: {s_w}, minor {a_w['Minor_kg']:.1f} kg" if s_w == "Optimal" else f"without: {s_wo}; with: {s_w}")

        SM_SAVED = dict(SINTER_MANUAL)
        try:
            SINTER_MANUAL.update(on=True, pct=70.0)
            sm, bm, _, am, dm = solve_mbf(base)
            check("manual sinter share pins the share at 70.00%", sm == "Optimal" and abs(am["Sinter_share_pct"] - 70.0) < 1e-3,
                  f"{am['Sinter_share_pct']:.3f}" if sm == "Optimal" else sm)
            if sm == "Optimal":
                check("manual mode keeps every other limit", abs(am["Fe_burden_kg"] - 965.0) < 1e-3
                      and BASICITY_MIN - 1e-3 <= am["B2"] <= BASICITY_MAX + 1e-3 and abs(am["Fuel_supplied"] - am["Fuel_rule"]) < 0.05)
            SINTER_MANUAL.update(on=True, pct=45.0)
            so, _, _, ao, do = solve_mbf(base, explain=False)
            check("manual share outside the guard rails solves and is flagged", so == "Optimal" and any("outside" in x for x in do), so)
            SINTER_MANUAL.update(on=True, pct=101.0)
            check("manual share above 100% -> INPUT_ERROR", solve_mbf(base)[0] == "INPUT_ERROR")
            SINTER_MANUAL.update(on=True, pct=0.0)
            s0_, b0_, _, a0_, _ = solve_mbf(base, explain=False)
            check("0% sinter (all ore) can be pinned", s0_ == "Optimal" and a0_["Sinter_kg"] < 1e-6 and a0_["Ore_kg"] > 100, s0_)
        finally:
            SINTER_MANUAL.clear(); SINTER_MANUAL.update(SM_SAVED)
        sw = sinter_sweep(base, 65, 75, 5)
        check("sweep returns free choice + 3 points and restores state",
              len(sw) == 4 and (sw["Status"] == "Optimal").all() and abs(sw.iloc[1]["Sinter %"] - 65) < 1e-2
              and abs(sw.iloc[3]["Sinter %"] - 75) < 1e-2 and SINTER_MANUAL["on"] == SM_SAVED["on"], f"{len(sw)} rows")
        swo_, oxl_ = sinter_sweep(base, 65, 75, 5, with_oxides=True)
        pv_ = oxides_by_sinter_pivot(oxl_, "SiO2")
        chk_ = sw.set_index("Sinter share %").loc[70.0]
        tot70 = float(pv_.loc[pv_["Point"] == 70.0, "TOTAL"].iloc[0])
        st70 = oxl_[oxl_["Point"] == 70.0]
        check("oxides by sinter share: one row per point, totals equal the charged SiO2",
              len(pv_) == 4 and abs(tot70 - st70["SiO2 kg"].sum()) < 0.05 and "Quartzite" in pv_.columns and chk_["Status"] == "Optimal")
        cv_ = sinter_curve(base)
        okc = cv_[cv_["Status"] == "Optimal"]
        check("sinter curve covers 0-100%, finds the feasible edge and names the limit",
              cv_["Sinter %"].iloc[0] == 0.0 and cv_["Sinter %"].iloc[-1] == 100.0 and len(cv_) == 41
              and cv_.attrs["hi"] is not None and 50 < cv_.attrs["hi"] < 100 and any(e[0] == "hi" and e[2] for e in cv_.attrs["edges"])
              and okc["Coke kg"].iloc[0] > okc["Coke kg"].iloc[-1], cv_.attrs.get("note", "")[:90])
        check("compact oxide table: CaO, SiO2, Al2O3 kg and % with a total row",
              st == "Optimal" and list(compact_oxide_table(oxide_sources_table(blend, base, ach)).columns[2:]) ==
              ["CaO kg", "CaO % of total", "SiO2 kg", "SiO2 % of total", "Al2O3 kg", "Al2O3 % of total"])
        check("sweep shows the rule terms, raw flux, MgO/Al2O3 and stock-balance level",
              {"Sinter/flux moisture", "Raw flux term", "Slag term", "MgO/Al2O3", "Stock balance %", "Raw flux kg"} <= set(sw.columns))

        FT_SAVED, SM2 = dict(FUEL_TERMS), dict(SINTER_MANUAL)
        PB_SAVED = PRICE_BASIS
        MR_SAVED = {k: dict(v) for k, v in MATERIAL_RULES.items()}
        try:
            SINTER_MANUAL.update(on=True, pct=65.0)
            s65, b65, _, a65, _ = solve_mbf(base)
            if s65 == "Optimal":
                ft = a65["Fuel_terms"]
                check("fuel breakdown adds up to the fuel supplied",
                      abs(sum(v for k, v in ft.items() if k != "total") - a65["Fuel_supplied"]) < 0.05, f"{ft['total']:.2f} vs {a65['Fuel_supplied']:.2f}")
            for k in FUEL_TERMS:
                FUEL_TERMS[k] = (k == "sinter_share")
            f65 = solve_mbf(base)[3]["Fuel_supplied"]
            SINTER_MANUAL.update(pct=75.0)
            f75 = solve_mbf(base)[3]["Fuel_supplied"]
            check("sinter-only rule: +10 pts sinter share -> fuel falls exactly 10 kg", abs((f65 - f75) - 10.0) < 1e-3, f"{f65 - f75:.4f}")
            FUEL_TERMS.update(FT_SAVED)
            SINTER_MANUAL.update(pct=65.0)
            fu = fe_sensitivity(base, "Iron_ore", (-1, 1))["Fuel kg"].tolist()
            check("higher ore Fe -> lower fuel (Fe sensitivity)", len(fu) == 3 and fu[0] > fu[1] > fu[2], str([round(v, 2) for v in fu]))
            FUEL_TERMS.update(FUEL_TERMS_DEFAULT)
            fs = []
            for p_ in (65.0, 70.0, 75.0):
                SINTER_MANUAL.update(on=True, pct=p_)
                fs.append(solve_mbf(base)[3]["Fuel_supplied"])
            check("default rule set: fuel falls as sinter share rises 65 -> 70 -> 75", fs[0] > fs[1] > fs[2], str([round(v, 2) for v in fs]))
            FUEL_TERMS.update(FT_SAVED)

            SINTER_MANUAL.update(on=True, pct=65.0)
            r65 = solve_mbf(base)
            if r65[0] == "Optimal":
                fit_ = fe_impact_table(r65[1], base, r65[3])
                tot_ = float(fit_.loc[fit_["Material"] == "TOTAL Fe effect on fuel", "Fuel effect kg/tHM"].iloc[0])
                ft65 = r65[3]["Fuel_terms"]
                check("Fe impact table total = ore Fe term + sinter Fe term", abs(tot_ - (ft65["ore_fe"] + ft65["sinter_fe"])) < 0.01)
                check("Fe impact table lists every charged ore and sinter", set(fit_["Material"]) >= {"Ore1", "Sinter1"})
            Xb = {m: 0.0 for m in base.index}; Xb["Ore1"] = 600.0
            c0_ = MATERIAL_RULES["ore_fe"]["coef"]
            e1 = fuel_terms(Xb, base, 330, 65)["ore_fe"]
            MATERIAL_RULES["ore_fe"]["coef"] = 2 * c0_
            e2 = fuel_terms(Xb, base, 330, 65)["ore_fe"]
            MATERIAL_RULES["ore_fe"]["coef"] = c0_
            check("ore Fe coefficient is live: doubling it doubles the ore Fe fuel effect", e1 < 0 and abs(e2 - 2 * e1) < 1e-9, f"{e1:.3f} -> {e2:.3f}")
            d = base.copy(); d.loc["Sinter1", "Fe"] = 54.85
            fsi0 = solve_mbf(base)[3]["Fuel_supplied"]; fsi1 = solve_mbf(d)[3]["Fuel_supplied"]
            check("higher sinter Fe -> lower fuel (sinter Fe rule acts)", fsi1 < fsi0 - 0.5, f"{fsi1 - fsi0:+.2f} kg")

            f0 = solve_mbf(base)[3]["Fuel_supplied"]
            d = base.copy(); d.loc["Coke3", "Moisture_Pct"] = 7.5
            st_m, _, _, am_, _ = solve_mbf(d)
            check("coke moisture +2 pts raises fuel (plant rule 3 kg/pt)", st_m == "Optimal" and 4.0 < am_["Fuel_supplied"] - f0 < 9.0,
                  f"{am_['Fuel_supplied'] - f0:+.2f} kg" if st_m == "Optimal" else st_m)
            d = base.copy(); d.loc["Ore1", "Moisture_Pct"] = 5.0
            st_o, _, _, ao_, _ = solve_mbf(d)
            check("ore moisture +2 pts raises fuel", st_o == "Optimal" and 0.8 < ao_["Fuel_supplied"] - f0 < 6.0,
                  f"{ao_['Fuel_supplied'] - f0:+.2f} kg" if st_o == "Optimal" else st_o)
            d = base.copy(); d.loc["Sinter1", "Moisture_Pct"] = 3.0
            st_s, _, _, as_, _ = solve_mbf(d)
            check("moisture applies to sinter too", st_s == "Optimal" and as_["Fuel_supplied"] - f0 > 5.0,
                  f"{as_['Fuel_supplied'] - f0:+.2f} kg" if st_s == "Optimal" else st_s)
            FUEL_TERMS["other_moisture"] = False
            fo_off = solve_mbf(d)[3]["Fuel_supplied"]; fb_off = solve_mbf(base)[3]["Fuel_supplied"]
            d3 = base.copy(); d3.loc["Coke3", "Moisture_Pct"] = 7.5
            fc_off = solve_mbf(d3)[3]["Fuel_supplied"]
            FUEL_TERMS.update(FT_SAVED)
            check("sinter/flux moisture switch removes only that effect", abs(fo_off - fb_off) < 0.5 and fc_off - fb_off > 4.0,
                  f"sinter {fo_off - fb_off:+.2f}, coke {fc_off - fb_off:+.2f}")
            d = base.copy(); d.loc["Nut_Coke", ["Fe", "CaO", "MgO", "SiO2", "Al2O3", "FC", "Moisture_Pct", "Available"]] = [2.2, 0.4, 0.2, 9.3, 5.0, 75.0, 2.0, True]
            fn0 = solve_mbf(d)[3]["Fuel_supplied"]
            d.loc["Nut_Coke", "Moisture_Pct"] = 12.0
            st_n, _, _, an2, _ = solve_mbf(d)
            check("nut coke moisture counts in the coke pool", st_n == "Optimal" and an2["Fuel_supplied"] - fn0 > 1.5,
                  f"{an2['Fuel_supplied'] - fn0:+.2f} kg" if st_n == "Optimal" else st_n)
            mt = moisture_table(b65, base) if s65 == "Optimal" else None
            if mt is not None:
                body_ = mt[(mt["Material"] != "TOTAL") & (mt["Group"] != "")]
                check("wet & net table: wet >= dry, water = wet - dry",
                      (body_["Wet kg/tHM"] >= body_["Dry kg/tHM"] - 1e-9).all() and ((body_["Wet kg/tHM"] - body_["Dry kg/tHM"] - body_["Water kg/tHM"]).abs() < 0.02).all())
            globals()["PRICE_BASIS"] = "dry"; cd = solve_mbf(base)[2]
            globals()["PRICE_BASIS"] = "wet"; cw = solve_mbf(base)[2]
            globals()["PRICE_BASIS"] = "dry"
            check("price basis 'wet' costs more than 'dry' when materials carry water", cw > cd + 1.0, f"{cd:.0f} -> {cw:.0f}")
            ms_ = moisture_sensitivity(base, "Fuel_Coke", (-1, 1))["Fuel kg"].tolist()
            check("moisture sensitivity: drier coke -> lower fuel", len(ms_) == 3 and ms_[0] < ms_[1] < ms_[2], str([round(v, 2) for v in ms_]))
            al_ = assay_sensitivity(base, "Iron_ore", "Al2O3", (-1, 1))
            check("assay sensitivity handles Al2O3 (more ore Al2O3 -> more slag)",
                  (al_["Status"] == "Optimal").all() and al_["Slag kg"].iloc[2] > al_["Slag kg"].iloc[0], str(al_["Slag kg"].tolist()))

        finally:
            FUEL_TERMS.update(FT_SAVED)
            SINTER_MANUAL.clear(); SINTER_MANUAL.update(SM2)
            for k, v in MR_SAVED.items():
                MATERIAL_RULES[k].update(v)
            globals()["PRICE_BASIS"] = PB_SAVED

        # ---- RM stock
        two = base.copy()
        two.loc["Ore2"] = two.loc["Ore1"]; two.loc["Ore2", "Available"] = True; two.loc["Ore2", "Price_Rs_t"] = 5000.0
        two.loc["Ore1", "RM_Stock"] = 30000.0; two.loc["Ore2", "RM_Stock"] = 10000.0
        STOCK.update(balance=1.0)
        sb, bb, _, ab, _ = solve_mbf(two, explain=False)
        if sb == "Optimal":
            share1 = bb["Ore1"] / (bb["Ore1"] + bb["Ore2"])
            check("stock balance: usage split 75/25 by stock", abs(share1 - 0.75) < 0.005, f"Ore1 share {share1:.3f}")
        else:
            check("stock balance solves", False, sb)
        STOCK.update(balance=0.0)
        sc, bc, _, _, _ = solve_mbf(two, explain=False)
        check("balance off: cost decides (cheaper ore only)", sc == "Optimal" and bc["Ore1"] < 1e-6, f"Ore1 {bc['Ore1']:.1f}" if sc == "Optimal" else sc)
        STOCK.update(balance=1.0)

        SM3, MG_SAVED = dict(SINTER_MANUAL), MGO_MAX_PCT
        try:
            SINTER_MANUAL.update(on=True, pct=70.0)
            sp_, bp_, _, _, dp_ = solve_mbf(two, explain=False)
            share_ = bp_["Ore1"] / (bp_["Ore1"] + bp_["Ore2"]) if sp_ == "Optimal" else -1.0
            check("pinned share keeps the 100% stock balance (no silent relaxation)",
                  sp_ == "Optimal" and abs(share_ - 0.75) < 0.005 and not any("relaxed" in x for x in dp_), f"Ore1 share {share_:.3f}")
            SINTER_MANUAL.update(on=False)
            globals()["MGO_MAX_PCT"] = 7.5                        # squeeze the demo so sinter cannot reach 80%
            rg = sinter_range(base)
            check("sinter_range finds the upper limit set by the MgO cap", rg is not None and 77.5 < rg["hi"] < 78.5 and rg["lo"] <= 50.05 and not rg["gaps"], str(rg))
            lim_ = sinter_limiters(base, rg["hi"] + 0.3) if rg else []
            check("limiter analysis names the MgO cap", "MgO cap" in lim_, ", ".join(lim_))
            SINTER_MANUAL.update(on=True, pct=80.0)
            sx, _, _, _, dx = solve_mbf(base)
            check("pinned share beyond the limit -> Infeasible, and the messages say why",
                  sx == "Infeasible" and any("MgO cap" in x for x in dx) and any("can only run" in x for x in dx), " | ".join(dx)[-120:])
            SINTER_MANUAL.update(on=False)
            sb_, _, _, ab_, db_ = solve_mbf(base)
            check("normal run reports the unreachable top, names the MgO cap, and still explains its own choice",
                  sb_ == "Optimal" and any("can only run" in x and "MgO cap" in x for x in db_) and any(x.startswith("SINTER DECISION") for x in db_))
            swx = sinter_sweep(base, 70, 80, 5)
            check("sweep marks points above the limit and attaches the range note",
                  "Note" in swx.columns and swx.iloc[-1]["Status"] == "Infeasible" and "above the feasible limit" in str(swx.iloc[-1]["Note"])
                  and "can only run" in swx.attrs.get("note", ""))
            globals()["MGO_MAX_PCT"] = MG_SAVED
            swo = sinter_sweep(base, 65, 75, 5)
            check("sweep on a fully feasible range has no Note column", "Note" not in swo.columns)
        finally:
            globals()["MGO_MAX_PCT"] = MG_SAVED
            SINTER_MANUAL.clear(); SINTER_MANUAL.update(SM3)
            STOCK.update(balance=1.0)

        capd = base.copy(); capd.loc["Ore1", "RM_Stock"] = 5000.0
        STOCK.update(plan_hm_tonnes=10000.0)
        sk, bk, _, ak, _ = solve_mbf(capd, explain=False)
        check("stock cap honoured (<= 500 kg/tHM)", sk == "Optimal" and bk["Ore1"] <= 500.0 + 1e-6, f"{bk['Ore1']:.1f}" if sk == "Optimal" else sk)
        capd.loc["Ore1", "RM_Stock"] = 1000.0
        si_, _, _, _, di_ = solve_mbf(capd)
        check("insufficient stock -> Infeasible and says STOCK", si_ == "Infeasible" and "STOCK" in di_[0], di_[0][:40] if si_ == "Infeasible" else si_)
        STOCK.update(plan_hm_tonnes=0.0)
        zs = base.copy(); zs.loc["Coke3", "RM_Stock"] = 0.0
        check("zero stock -> treated as unavailable", solve_mbf(zs)[0] == "NO_PRODUCTION")

        # ---- sensitivity studio
        ps = price_sensitivity(base, "Sinter1", (-10, 10))
        pb = float(ps.loc[ps["Price change %"] == 0.0, "Cost Rs/tHM"].iloc[0])
        check("price sensitivity: baseline row equals the base cost, dearer sinter costs more",
              abs(pb - round(cost, 1)) < 0.11 and ps["Cost Rs/tHM"].iloc[-1] > ps["Cost Rs/tHM"].iloc[0], str(ps["Cost Rs/tHM"].tolist()))
        pg = price_sensitivity(base, "Group: Flux", (-10, 10))
        check("price sensitivity works for a whole group", (pg["Status"] == "Optimal").all() and "Limestone" in pg.attrs["note"])
        if full:
            snap = (REQUIRED_FE_KGTHM, AL2O3_MAX_PCT, MGO_MAX_PCT, BASICITY_MIN, FUEL_PER_KG_SLAG, FUEL_PER_KG_RAW_FLUX,
                    FURNACE_PROFILES[FURNACE["name"]]["base"], {k: v["coef"] for k, v in MATERIAL_RULES.items()})
            tor = tornado(base, 10)
            snap2 = (REQUIRED_FE_KGTHM, AL2O3_MAX_PCT, MGO_MAX_PCT, BASICITY_MIN, FUEL_PER_KG_SLAG, FUEL_PER_KG_RAW_FLUX,
                     FURNACE_PROFILES[FURNACE["name"]]["base"], {k: v["coef"] for k, v in MATERIAL_RULES.items()})
            check("tornado: every driver solves, ranked by swing, and every setting is restored",
                  (tor["Status"] == "Optimal").all() and tor["Swing Rs"].is_monotonic_decreasing and snap == snap2 and len(tor) >= 15, f"{len(tor)} drivers")
            be = sinter_breakeven(base)
            pf = be.loc[be["Case"].str.startswith("Sinter drops"), "Sinter price Rs/t"].iloc[0]
            chk = base.copy(); chk.loc["Sinter1", "Price_Rs_t"] = pf - 30.0
            s_below = solve_mbf(chk, explain=False)[3]["Sinter_share_pct"]
            chk.loc["Sinter1", "Price_Rs_t"] = pf + 30.0
            s_above = solve_mbf(chk, explain=False)[3]["Sinter_share_pct"]
            check("break-even: just above the floor price sinter sits at the 50% floor, just below it does not",
                  s_above <= 50.05 < s_below, f"price {pf:,.0f}: {s_below:.2f}% -> {s_above:.2f}%")

        # ---- Excel export
        import shutil
        from openpyxl import load_workbook
        tmpd = tempfile.mkdtemp()
        try:
            res_ = solve_mbf(base)
            bun = build_export_bundle(base, res_)
            now = _dt.datetime.now()
            ana = {"sweep": {"df": sinter_sweep(base, 60, 80, 10), "time": now, "fingerprint": bun["fingerprint"], "title": "Sinter sweep"},
                   "price": {"df": ps, "time": now, "fingerprint": bun["fingerprint"], "title": "Price sensitivity"},
                   "sweep_oxides": {"df": oxl_, "time": now, "fingerprint": bun["fingerprint"], "title": "Oxides by sinter share"},
                   "tornado": {"df": pd.DataFrame({"Driver": ["a", "b"], "Swing Rs": [10.0, 5.0], "Status": ["Optimal", "Optimal"]}),
                               "time": now, "fingerprint": bun["fingerprint"], "title": "Tornado"}}
            pth = write_export(bun, os.path.join(tmpd, "t.xlsx"), analysis=ana)
            wbk = load_workbook(pth)
            need_ = {"Summary", "Sinter Curve 0-100%", "Inputs", "Optimised Burden", "Slag & Chemistry", "Fuel Rate", "Fe Impact", "Moisture",
                     "Sinter Sweep", "Oxides by Sinter %", "Price Sensitivity", "Tornado", "Run Settings"}
            check("Excel export: all sheets present", need_ <= set(wbk.sheetnames), ", ".join(wbk.sheetnames))
            ws_ = wbk["Summary"]
            cc = next(((r_, c_) for r_ in range(1, 30) for c_ in range(1, 8) if ws_.cell(r_, c_).value == "Total raw-material cost (Rs/tHM)"), None)
            val = ws_.cell(cc[0] + 1, cc[1]).value if cc else None
            check("Excel export: cost on Summary equals the model result", val is not None and abs(val - res_[2]) < 1e-6, f"{val}")
            check("Excel export: Inputs sheet holds every input row", wbk["Inputs"].max_row - 4 == len(base), f"{wbk['Inputs'].max_row - 4} rows")
            wsb = wbk["Optimised Burden"]
            tr_ = next(r_ for r_ in range(1, wsb.max_row + 1) if wsb.cell(r_, 1).value == "TOTAL")
            hdr = [c.value for c in wsb[4]]
            dry = wsb.cell(tr_, hdr.index("Dry kg/tHM") + 1).value
            check("Excel export: burden total matches the model", abs(dry - res_[3]["Total_burden_kg"]) < 1e-6, f"{dry:.3f}")
            check("Excel export: charts present", len(wbk["Optimised Burden"]._charts) >= 2 and len(wbk["Fuel Rate"]._charts) >= 1
                  and len(wbk["Tornado"]._charts) >= 1 and len(wbk["Sinter Sweep"]._charts) >= 2
                  and len(wbk["Oxides by Sinter %"]._charts) == 3 and len(wbk["Slag & Chemistry"]._charts) >= 1
                  and len(wbk["Sinter Curve 0-100%"]._charts) == 2)
            wsc = wbk["Slag & Chemistry"]
            sl_hit = any(str(wsc.cell(r_, 1).value or "").startswith("SLAG OXIDE SOURCES") for r_ in range(1, wsc.max_row + 1))
            slag_v = next((wsc.cell(r_, 4).value for r_ in range(1, wsc.max_row + 1) if wsc.cell(r_, 1).value == "SLAG"), None)
            check("Excel export: oxide sources section present and its SLAG CaO kg matches", sl_hit and slag_v is not None
                  and abs(slag_v - res_[3]["CaO_kg"]) < 1e-2, f"{slag_v}")
            check("Excel export: settings snapshot written", wbk["Run Settings"].max_row > 20)
            wfi = wbk["Fe Impact"]
            fi_tot = next((wfi.cell(r_, 7).value for r_ in range(1, wfi.max_row + 1) if str(wfi.cell(r_, 1).value).startswith("TOTAL")), None)
            check("Excel export: Fe Impact total matches the model",
                  fi_tot is not None and abs(fi_tot - float(fe_impact_table(res_[1], base, res_[3]).iloc[-1]["Fuel effect kg/tHM"])) < 1e-6, f"{fi_tot}")
            bad = base.copy(); bad.loc["Coke3", "Available"] = False
            w2 = load_workbook(write_export(build_export_bundle(bad, solve_mbf(bad)), os.path.join(tmpd, "bad.xlsx")))
            check("Excel export: a failed run still exports inputs and the reason",
                  {"Summary", "Inputs", "Run Settings"} <= set(w2.sheetnames) and "Optimised Burden" not in w2.sheetnames)
            bad2 = base.copy(); bad2.loc["Quartzite", "Available"] = False; bad2.loc["Sinter1", "SiO2"] = 5.0
            SMx = dict(SINTER_MANUAL); SINTER_MANUAL.update(on=True, pct=95.0)
            try:
                rb2 = solve_mbf(bad2)
                w3 = load_workbook(write_export(build_export_bundle(bad2, rb2), os.path.join(tmpd, "bad2.xlsx")))
            finally:
                SINTER_MANUAL.clear(); SINTER_MANUAL.update(SMx)
            check("Excel export: an infeasible run still carries the 0-100% sinter curve", rb2[0] == "Infeasible" and "Sinter Curve 0-100%" in w3.sheetnames)
            tpl = base.reset_index().copy(); tpl["Availability"] = np.where(tpl["Available"], "ON", "OFF")
            tpl = tpl.drop(columns=["Available"]); tpl["Ash %"] = 12.0
            bio = io.BytesIO(); tpl.to_excel(bio, index=False)
            ld = load_excel_bytes(bio.getvalue())
            check("Excel loader: reads the template back (old Ash column ignored)", list(ld.columns) == COLUMNS[1:] and len(ld) == len(base))
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)

        # ---- heat balance (v11.6 / v11.7)
        H0 = copy.deepcopy(HEAT)
        SMh = dict(SINTER_MANUAL)
        try:
            h = ach["Heat"] if st == "Optimal" else None
            check("heat: every run carries a heat check and names it in the notes",
                  h is not None and h["C_need_kg"] > 0 and any(x.startswith("HEAT CHECK") for x in diag))
            if h is not None:
                s_ = h["supply"]["kJ_per_kgC"]
                check("heat: carbon need = heat demand / tuyere heat + reaction carbon",
                      abs(h["C_need_kg"] - (sum(h["D"].values()) / s_ + sum(h["C"].values()))) < 1e-9)
                check("heat: tuyere heat per kg C is physically plausible (6,000-12,000 kJ)", 6000 < s_ < 12000, f"{s_:,.0f}")
                check("heat: heat-balance minimum fuel = fuel supplied - surplus carbon / coke FC",
                      abs(h["fuel_min_kg"] - (ach["Fuel_supplied"] - h["C_surplus_kg"] * 100 / h["coke_FC_pct"])) < 1e-9)
                xv = {m: pulp.LpVariable(f"hv{i}", lowBound=0) for i, m in enumerate(base.index)}
                sio2r = HM_BASIS_KG * HM_SI_PCT / 100.0 / SIO2_TO_SI_CONST
                slag_e = chem_expr(xv, base, "CaO") + chem_expr(xv, base, "MgO") + chem_expr(xv, base, "SiO2") - sio2r + chem_expr(xv, base, "Al2O3")
                e_ = heat_carbon_need(xv, base, slag_e, ach["Sinter_share_pct"])[0]
                for m, v_ in xv.items():
                    v_.varValue = float(blend.get(m, 0.0))
                check("heat: the LP expression equals the reported need (the balance is linear in the burden)",
                      abs(pulp.value(e_) - h["C_need_kg"]) < 1e-6, f"{pulp.value(e_):.4f} vs {h['C_need_kg']:.4f}")
                Z = {m: 0.0 for m in base.index}
                n1 = heat_carbon_need(Z, base, 330.0, 65.0)[0]; n2 = heat_carbon_need(Z, base, 430.0, 65.0)[0]
                check("heat: +100 kg slag adds 100 x net slag enthalpy / tuyere heat of carbon",
                      abs((n2 - n1) - 100 * (HEAT["h_slag_kJ_kg"] - HEAT["h_gangue_trz_kJ_kg"]) / s_) < 1e-9, f"{n2 - n1:.3f} kg C")
                Zl = dict(Z); Zl["Limestone"] = 100.0
                nl = heat_carbon_need(Zl, base, 330.0, 65.0)[0]
                HEAT["blast_temp_C"] = 1100.0
                nh = heat_carbon_need(Z, base, 330.0, 65.0)[0]
                HEAT["blast_temp_C"] = H0["blast_temp_C"]; HEAT["drr"] = H0["drr"] + 0.05
                nd = heat_carbon_need(Z, base, 330.0, 65.0)[0]
                HEAT["drr"] = H0["drr"]
                check("heat: raw flux raises the need, hotter blast lowers it, more direct reduction raises it",
                      nl > n1 and nh < n1 and nd > n1, f"flux {nl - n1:+.1f}, blast {nh - n1:+.1f}, DRR {nd - n1:+.1f} kg C")
                new_loss = calibrate_heat_losses(ach)
                hc = calc_achieved(blend, base)["Heat"]
                check("heat: calibrating the losses to a run closes its balance exactly", abs(hc["C_surplus_kg"]) < 1e-6 and HEAT["calibrated"],
                      f"loss {new_loss:,.0f} MJ/tHM")
                HEAT.update(H0)
                SINTER_MANUAL.update(on=True, pct=65.0)
                r_chk = solve_mbf(base, explain=False)
                HEAT["mode"] = "floor"; HEAT["loss_MJ_tHM"] = H0["loss_MJ_tHM"] + 1500.0
                r_fl = solve_mbf(base, explain=False)
                okf_ = (r_chk[0] == "Optimal" and r_fl[0] == "Optimal"
                        and r_fl[3]["Coke_kg"] > r_chk[3]["Coke_kg"] + 1.0
                        and r_fl[3]["Fuel_supplied"] >= r_fl[3]["Fuel_rule"] - 1e-6
                        and r_fl[3]["Heat"]["C_surplus_kg"] >= -1e-6
                        and any(x.startswith("HEAT BALANCE FLOOR") for x in r_fl[4]))
                check("heat floor mode: when the heat need exceeds the rule, coke rises to meet it and the run says so", okf_,
                      f"coke {r_chk[3]['Coke_kg']:.1f} -> {r_fl[3]['Coke_kg']:.1f}" if r_fl[0] == "Optimal" else r_fl[0])
                HEAT["loss_MJ_tHM"] = 0.0
                r_lo = solve_mbf(base, explain=False)
                check("heat floor mode: when the rule already covers the heat need, the result equals check mode",
                      r_lo[0] == "Optimal" and abs(r_lo[2] - r_chk[2]) < 0.01, f"{r_lo[2]:.2f} vs {r_chk[2]:.2f}")
                HEAT.update(H0); HEAT["mode"] = "floor"
                bad_ = []
                for loss_ in (H0["loss_MJ_tHM"], H0["loss_MJ_tHM"] + 800.0):
                    HEAT["loss_MJ_tHM"] = loss_
                    for sp_ in (50.0, 70.0, 90.0, 100.0):
                        SINTER_MANUAL.update(on=True, pct=sp_)
                        rf_ = solve_mbf(base, explain=False)
                        HEAT["mode"] = "check"; rc_ = solve_mbf(base, explain=False); HEAT["mode"] = "floor"
                        if rf_[0] == "Optimal":
                            a_ = rf_[3]
                            tight = abs(a_["Fuel_supplied"] - a_["Fuel_rule"]) < 0.05 or abs(a_["Heat"]["C_surplus_kg"]) < 0.05
                            if not tight or rc_[0] != "Optimal" and a_["Heat"]["C_surplus_kg"] > 0.05:
                                bad_.append(sp_)
                check("heat floor mode: fuel is always exactly max(plant rule, heat need) - never extra coke for its ash", not bad_, str(bad_))
                HEAT.update(H0)
            SINTER_MANUAL.clear(); SINTER_MANUAL.update(SMh)
            cvh = cv_
            check("heat: the 0-100% curve carries the heat-balance minimum coke and a per-10-pts comparison",
                  "Heat-min coke kg" in cvh.columns and bool(cvh.attrs.get("heat_note")), str(cvh.attrs.get("heat_note"))[:90])
            T_ = {k: t for k, _, t in result_tables(st, blend, cost, ach, base)} if st == "Optimal" else {}
            check("heat: result tables include the heat balance and the comparison with the plant rules",
                  "heat" in T_ and "heat_rules" in T_ and any(str(v).startswith("SURPLUS") for v in T_["heat"]["Item"]))
            tmph = tempfile.mkdtemp()
            try:
                from openpyxl import load_workbook
                wbh = load_workbook(write_export(build_export_bundle(base, (st, blend, cost, ach, diag), cv_), os.path.join(tmph, "h.xlsx")))
                check("heat: Excel export has the Heat Balance sheet with its chart", "Heat Balance" in wbh.sheetnames and len(wbh["Heat Balance"]._charts) == 1)
            finally:
                import shutil
                shutil.rmtree(tmph, ignore_errors=True)
        finally:
            HEAT.clear(); HEAT.update(H0)
            SINTER_MANUAL.clear(); SINTER_MANUAL.update(SMh)

        # ---- heat audit (v11.7)
        H1 = copy.deepcopy(HEAT); SM4 = dict(SINTER_MANUAL)
        g0 = (HM_SI_PCT, HM_C_PCT, HM_FE_PCT, HM_FE_KGTHM)
        try:
            rec0 = synthetic_month(70.0, drr=0.40)
            Ra = audit_month(rec0)
            check("audit: DRR is recovered from a self-consistent month (gas route and carbon-charged route agree)",
                  abs(Ra["DRR_gas"] - 0.40) < 2e-3 and abs(Ra["DRR_fuel"] - 0.40) < 2e-3, f"{Ra['DRR_gas']:.4f} / {Ra['DRR_fuel']:.4f}")
            check("audit: cooling-water loss recovered, carbon balance closes, heat residual ~ 0",
                  abs(Ra["Q_cool_hot"] - rec0["truth"]["loss_MJ"]) < 0.5 and abs(Ra["C_gap_pct"]) < 0.05 and abs(Ra["residual_pct"]) < 0.1 and Ra["usable"],
                  f"loss {Ra['Q_cool_hot']:.1f}, gap {Ra['C_gap_pct']:.3f}%, residual {Ra['residual_pct']:.3f}%")
            check("audit: hot-zone circuits only (the upper-furnace circuit is excluded from the loss)", Ra["Q_cool_all"] > Ra["Q_cool_hot"] + 50)
            check("audit: leaves the model untouched (heat parameters and hot-metal constants restored)",
                  HEAT == H1 and (HM_SI_PCT, HM_C_PCT, HM_FE_PCT, HM_FE_KGTHM) == g0)
            Rb = audit_month(synthetic_month(70.0, drr=0.40, noise=0.01, seed=3))
            check("audit: with 1% noise on the meters and +/-0.3 pt on the gas the DRR stays within 0.08 and is still usable",
                  abs(Rb["DRR"] - 0.40) < 0.08 and Rb["usable"], f"{Rb['DRR']:.3f}, gap {Rb['C_gap_pct']:+.1f}%")
            se_ = audit_sensitivity(rec0).set_index("Case")
            check("audit: sensitivity table moves the right way (more blast -> lower DRR from the gas; more CO -> higher DRR)",
                  se_.loc["blast volume +3%", "DRR (gas)"] > 0.40 and se_.loc["top-gas CO +1 pt", "DRR (gas)"] > 0.40 > se_.loc["top-gas CO -1 pt", "DRR (gas)"]
                  and abs(se_.loc["top-gas CO +1 pt", "Carbon gap %"]) > 1.0)
            badm = dict(rec0["month"]); badm["tg_co_pct"] *= 1.6
            Rc = audit_month(dict(rec0, month=badm))
            check("audit: a month whose carbon balance is off by more than 10% is flagged NOT usable", (not Rc["usable"]) and abs(Rc["C_gap_pct"]) > 10.0, f"{Rc['C_gap_pct']:+.1f}%")
            recs4 = [synthetic_month(s_, drr=0.44 - 0.004 * (s_ - 65.0), label=f"m{s_:g}") for s_ in (55.0, 62.0, 70.0, 77.0)]
            A4 = audit_run(recs4)
            fd = A4["fits"].get("drr")
            check("audit: DRR slope vs sinter share is recovered from 4 months and is significant",
                  fd is not None and abs(fd["slope"] + 0.004) < 4e-4 and fd["applied"], f"{fd['slope']:+.5f}" if fd else "no fit")
            audit_apply(A4, "measured")
            worst = 0.0
            for r_ in A4["results"]:
                SINTER_MANUAL.update(on=True, pct=r_["sinter_pct"])
                st_, b_, c_, a_, _ = solve_mbf(plant_df, explain=False)
                q_ = a_["Heat"]["supply"]["kJ_per_kgC"]
                # model surplus (less the dust carbon that gives no heat) must equal the month's own loss deviation from the applied average
                worst = max(worst, abs((a_["Heat"]["C_surplus_kg"] - 15.0 * 0.35) - (r_["Q_cool_hot"] - HEAT["loss_MJ_tHM"]) * 1000.0 / q_))
            check("audit: after APPLY the model's heat balance reproduces every audited month (except its own loss deviation from the average)",
                  HEAT["calibrated"] and abs(HEAT["drr_per_pt_sinter"] + 0.004) < 4e-4 and "audited on 4" in HEAT["calibration_note"] and worst < 0.05,
                  f"worst mismatch {worst:.4f} kg C")
            HEAT.clear(); HEAT.update(copy.deepcopy(H1))
            A2 = audit_run(recs4[:2])
            check("audit: with only 2 usable months no slope is fitted or applied (only the mean DRR)",
                  "drr" not in A2["fits"] and any("at least 3 months" in f_ for f_ in A2["flags"]))
            check("audit: the plant thumb-rule fuel is compared with the fuel charged, month by month",
                  all(abs(r_["fuel_actual"] - r_["fuel_rule"]) < 0.1 for r_ in A4["results"]) and "Thumb-rule fuel kg/tHM" in A4["months"].columns)
            tmpa = tempfile.mkdtemp()
            try:
                p_ = audit_write_template(os.path.join(tmpa, "t.xlsx"), rec0)
                rd = audit_read(p_)
                Rd = audit_month(rd)
                check("audit: the Excel template round-trips (write, read, audit) to the same DRR",
                      abs(Rd["DRR"] - 0.40) < 0.02 and rd["df"].shape[0] == rec0["df"].shape[0] and len(rd["cooling"]) == 3, f"{Rd['DRR']:.4f}")
                blank = audit_write_template(os.path.join(tmpa, "b.xlsx"))
                try:
                    audit_read(blank); okb = False
                except ValueError as e_:
                    okb = "missing required" in str(e_)
                check("audit: a blank template is refused with the list of missing values", okb)
                pe = audit_export(A4, os.path.join(tmpa, "r.xlsx"))
                from openpyxl import load_workbook as _lw
                check("audit: results export has months, fits, flags and heat terms", {"Months", "Fits", "Flags", "Heat terms"} <= set(_lw(pe).sheetnames))
            finally:
                import shutil
                shutil.rmtree(tmpa, ignore_errors=True)
        finally:
            HEAT.clear(); HEAT.update(H1)
            SINTER_MANUAL.clear(); SINTER_MANUAL.update(SM4)

        # ---- ore fines credit
        dfc = base.copy(); dfc.loc["Ore1", ["Fines_Pct", "Fines_Credit_Rs_t"]] = [25.0, 6000.0]
        check("fines credit: price used = (price - fines x credit) / (1 - fines); blank = unchanged",
              abs(eff_price(dfc, "Ore1") - (6300.0 - 0.25 * 6000.0) / 0.75) < 1e-9 and abs(eff_price(base, "Ore1") - 6300.0) < 1e-9)
        c0_, c1_ = solve_mbf(base, explain=False)[2], solve_mbf(dfc, explain=False)[2]
        check("fines credit flows into the optimized cost", c1_ > c0_ + 1.0, f"{c0_:.1f} -> {c1_:.1f}")
        dfe = base.copy(); dfe.loc["Ore1", "Fines_Pct"] = 100.0
        check("Fines_Pct of 100 -> INPUT_ERROR", solve_mbf(dfe)[0] == "INPUT_ERROR")
    finally:
        STOCK.update(saved)

    if verbose:
        for n, ok, det in results:
            print(("  PASS  " if ok else "  FAIL  ") + n + (f"   [{det}]" if det else ""))
    passed = sum(ok for _, ok, _ in results)
    print(f"SELF-TEST: {passed}/{len(results)} passed" + ("" if full else "  (quick set; run_self_tests(full=True) adds the tornado and break-even checks)"))
    return passed == len(results)


# ================================================================
# 12. CONFIG (one per user session) AND SESSION ISOLATION
# ================================================================
# Immutable copies of the plant defaults, so a new Config() always starts from them
# even while another session's settings are loaded into the module globals.
_DEFAULT_RULES = {k: {"coef": float(v["coef"]), "ref": float(v["ref"])} for k, v in MATERIAL_RULES.items()}
_DEFAULT_PROFILES = {k: dict(v) for k, v in FURNACE_PROFILES.items()}
_DEFAULT_FUEL_TERMS = dict(FUEL_TERMS_DEFAULT)
_DEFAULT_REF_RATES = dict(REF_RATES)
_DEFAULT_GUIDE = tuple(MGO_AL2O3_GUIDE)

# Heat-balance settings shown in the dashboard: key -> (label, min, max, step, format, help)
HEAT_FIELDS = {
    "blast_temp_C": ("Hot-blast temperature, C", 400.0, 1400.0, 10.0, "%.0f", "At the tuyeres / bustle pipe."),
    "blast_humidity_g_Nm3": ("Blast moisture + steam, g per Nm3 dry blast", 0.0, 100.0, 1.0, "%.1f", None),
    "blast_O2_pct": ("O2 in the dry blast, %", 18.0, 40.0, 0.1, "%.2f", "21 = no oxygen enrichment."),
    "trz_temp_C": ("Thermal reserve zone temperature, C", 700.0, 1200.0, 10.0, "%.0f",
                   "Gas leaves and solids enter the hot zone at this temperature."),
    "drr": ("Degree of direct reduction at the reference sinter share", 0.0, 1.0, 0.01, "%.3f", "Measured by the heat audit."),
    "drr_per_pt_sinter": ("DRR change per +1 point of sinter share", -0.05, 0.05, 0.0005, "%.4f", "0 = not modelled."),
    "calc_hot_frac": ("Share of raw-flux carbonate calcined in the hot zone", 0.0, 1.0, 0.05, "%.2f", None),
    "sol_loss_frac": ("Share of that CO2 reacting with coke (CO2 + C -> 2CO)", 0.0, 1.0, 0.05, "%.2f", None),
    "h_hm_kJ_kg": ("Hot metal enthalpy at tapping, kJ/kg (vs 25 C)", 800.0, 2000.0, 10.0, "%.0f", None),
    "h_fe_trz_kJ_kg": ("Iron enthalpy at the TRZ, kJ/kg (vs 25 C)", 200.0, 1200.0, 10.0, "%.0f", None),
    "h_slag_kJ_kg": ("Slag enthalpy at tapping, kJ/kg (vs 25 C)", 1000.0, 2500.0, 10.0, "%.0f", None),
    "h_gangue_trz_kJ_kg": ("Gangue / flux enthalpy at the TRZ, kJ/kg (vs 25 C)", 300.0, 1500.0, 10.0, "%.0f", None),
    "loss_MJ_tHM": ("Lower-furnace heat losses, MJ per tHM", 0.0, 5000.0, 10.0, "%.1f",
                    "Cooling water and shell below the TRZ. Calibrate with a plant run or the heat audit."),
}

# The eight thumb-rule switches, in display order: key -> (label, one-line description of the rule)
RULE_SWITCHES = {
    "sinter_share": ("Sinter share", "1 kg fuel per point of sinter in (sinter + ore), reference 65 %"),
    "ore_fe": ("Ore Fe", "3 kg fuel per point of ore Fe vs 61.5 %, at 600 kg ore/tHM"),
    "sinter_fe": ("Sinter Fe", "3 kg fuel per point of sinter Fe vs 53.5 %, at 1,115 kg sinter/tHM"),
    "slag": ("Slag volume", "0.18 kg fuel per kg slag over 330 kg/tHM"),
    "raw_flux": ("Raw flux", "0.30 kg fuel per kg limestone / dolomite charged to the furnace"),
    "ore_moisture": ("Ore moisture", "2 kg fuel per point of ore moisture over 3 %"),
    "coke_moisture": ("Coke moisture", "3 kg fuel per point of coke + nut coke moisture over 5 %"),
    "other_moisture": ("Sinter/flux moisture", "2 kg fuel per point of sinter, flux and minor moisture (extension, not a plant rule)"),
}


@dataclass
class Config:
    """Every setting the dashboard can change. Defaults are the notebook v11.7 plant values."""
    # hot metal and Fe closure
    hm_fe_pct: float = 94.0
    hm_si_pct: float = 0.6
    hm_c_pct: float = 4.3
    fe_required_per_100kg: float = 96.5
    fe_closure_basis: str = "burden"            # "burden" | "all"
    # operating policy (sinter guard rails as a fraction, 0.50 = 50 %)
    sinter_min: float = 0.50
    sinter_max: float = 0.80
    sinter_manual_on: bool = False
    sinter_manual_pct: float = 70.0
    pci_fixed_kgthm: float = 120.0
    nut_coke_kgthm: float = 40.0
    nut_coke_mode: str = "fixed"                # "fixed" | "cap"
    # slag quality bands
    basicity_min: float = 0.99
    basicity_max: float = 1.01
    mgo_min_pct: float = 7.0
    mgo_max_pct: float = 8.0
    al2o3_min_pct: float = 17.0
    al2o3_max_pct: float = 18.5
    # minor materials
    minor_max_kgthm: float = 100.0
    minor_demand_only: bool = True
    # stock
    stock_plan_hm_tonnes: float = 0.0
    stock_balance: float = 1.0
    # furnace and fuel-rate rules
    furnace_name: str = "MBF-3"
    slag_ref_kgthm: float = 330.0
    fuel_per_kg_slag: float = 0.18
    sinter_ref_pct: float = 65.0
    fuel_per_pct_sinter: float = 1.0
    raw_flux_ref_kgthm: float = 2.0
    fuel_per_kg_raw_flux: float = 0.30
    ks_fixed: float = 25.0
    price_basis: str = "dry"                    # "dry" | "wet"
    om_rs_thm: float = 0.0                      # operations & maintenance cost, Rs per tonne of hot metal (on top of the raw materials)
    fuel_terms: dict = field(default_factory=lambda: dict(_DEFAULT_FUEL_TERMS))
    material_rules: dict = field(default_factory=lambda: copy.deepcopy(_DEFAULT_RULES))       # {rule: {"coef", "ref"}}
    furnace_profiles: dict = field(default_factory=lambda: copy.deepcopy(_DEFAULT_PROFILES))  # {furnace: {"base"}}
    ref_rates: dict = field(default_factory=lambda: dict(_DEFAULT_REF_RATES))                # {"ore", "sinter", "coke"} kg/tHM
    # model constants and diagnostic guides (plant defaults; change only with a reason)
    raw_flux_min_cao_mgo: float = 15.0          # a Flux row with CaO + MgO at or above this % is raw (carbonate) flux
    dr_degree_floor: float = 0.40               # degree of direct reduction used for the stoichiometric carbon floor only
    mn_reduction_eff: float = 0.85              # share of charged Mn reduced into the metal
    mgo_al2o3_guide_lo: float = 0.40
    mgo_al2o3_guide_hi: float = 0.55
    fe_c_target: float = 2.0
    fe_c_tol: float = 0.1
    # hot-zone heat balance (v11.6 / v11.7): mode, parameters and calibration state
    heat: dict = field(default_factory=lambda: copy.deepcopy(HEAT_DEFAULT))

    def rules_off(self):
        return [RULE_SWITCHES[k][0] for k in RULE_SWITCHES if not self.fuel_terms.get(k, True)]

    def problems(self):
        """Plain-language list of settings that cannot work together (empty list = fine)."""
        p = []
        if not (0.0 < self.sinter_min < 1.0 and 0.0 < self.sinter_max < 1.0):
            p.append("Sinter guard rails must be between 0 and 100 %.")
        elif self.sinter_min > self.sinter_max:
            p.append("Sinter guard rails: the minimum is above the maximum.")
        if self.sinter_manual_on and not (0.0 <= self.sinter_manual_pct <= 100.0):
            p.append("Manual sinter share must be between 0 and 100 %.")
        for lo, hi, nm in ((self.basicity_min, self.basicity_max, "B2"), (self.mgo_min_pct, self.mgo_max_pct, "MgO"),
                           (self.al2o3_min_pct, self.al2o3_max_pct, "Al2O3")):
            if lo > hi:
                p.append(f"{nm} band: the minimum is above the maximum.")
        if not (0.0 < self.fe_required_per_100kg < 100.0):
            p.append("Fe charged per 100 kg hot metal must be between 0 and 100.")
        if self.pci_fixed_kgthm < 0 or self.nut_coke_kgthm < 0:
            p.append("PCI and nut coke rates cannot be negative.")
        if self.minor_max_kgthm < 0:
            p.append("Minor-material cap cannot be negative.")
        if not (0.0 <= self.stock_balance <= 1.0):
            p.append("Stock balance must be between 0 and 1.")
        if self.stock_plan_hm_tonnes < 0:
            p.append("Planned hot metal cannot be negative.")
        if self.furnace_name not in self.furnace_profiles:
            p.append(f"Furnace '{self.furnace_name}' has no profile.")
        if self.fe_closure_basis not in ("burden", "all"):
            p.append("Fe closure basis must be 'burden' or 'all'.")
        if self.nut_coke_mode not in ("fixed", "cap"):
            p.append("Nut coke mode must be 'fixed' or 'cap'.")
        if self.price_basis not in ("dry", "wet"):
            p.append("Price basis must be 'dry' or 'wet'.")
        if self.ks_fixed <= 0:
            p.append("Ks must be above zero.")
        if self.om_rs_thm < 0:
            p.append("O&M cost cannot be negative.")
        if any(float(self.ref_rates.get(k, 0.0)) <= 0 for k in ("ore", "sinter", "coke")):
            p.append("Reference charge rates (ore, sinter, coke) must be above zero.")
        if self.mgo_al2o3_guide_lo > self.mgo_al2o3_guide_hi:
            p.append("MgO/Al2O3 guide: the minimum is above the maximum.")
        if self.fe_c_tol < 0:
            p.append("Fe/C guide tolerance cannot be negative.")
        if not (0.0 <= self.dr_degree_floor <= 1.0) or not (0.0 <= self.mn_reduction_eff <= 1.0):
            p.append("Direct-reduction degree and Mn reduction efficiency must be between 0 and 1.")
        if self.raw_flux_min_cao_mgo < 0:
            p.append("Raw-flux threshold cannot be negative.")
        h = self.heat
        if h.get("mode") not in ("check", "floor"):
            p.append("Heat balance mode must be 'check' or 'floor'.")
        for k in ("drr", "calc_hot_frac", "sol_loss_frac"):
            if not (0.0 <= float(h.get(k, 0.0)) <= 1.0):
                p.append(f"Heat balance: {HEAT_FIELDS[k][0]} must be between 0 and 1.")
        if not (1.0 <= float(h.get("blast_O2_pct", 21.0)) <= 100.0):
            p.append("Heat balance: O2 in the blast must be between 1 and 100 %.")
        if float(h.get("loss_MJ_tHM", 0.0)) < 0 or float(h.get("blast_humidity_g_Nm3", 0.0)) < 0:
            p.append("Heat balance: losses and blast moisture cannot be negative.")
        return p


# Config field -> module global it drives
_CFG_TO_GLOBAL = {
    "hm_fe_pct": "HM_FE_PCT", "hm_si_pct": "HM_SI_PCT", "hm_c_pct": "HM_C_PCT",
    "fe_required_per_100kg": "FE_REQUIRED_PER_100KG_HM", "fe_closure_basis": "FE_CLOSURE_BASIS",
    "sinter_min": "SINTER_MIN", "sinter_max": "SINTER_MAX", "pci_fixed_kgthm": "PCI_FIXED_KGTHM",
    "nut_coke_kgthm": "NUT_COKE_KGTHM", "nut_coke_mode": "NUT_COKE_MODE",
    "basicity_min": "BASICITY_MIN", "basicity_max": "BASICITY_MAX", "mgo_min_pct": "MGO_MIN_PCT", "mgo_max_pct": "MGO_MAX_PCT",
    "al2o3_min_pct": "AL2O3_MIN_PCT", "al2o3_max_pct": "AL2O3_MAX_PCT",
    "minor_max_kgthm": "MINOR_MAX_KGTHM", "minor_demand_only": "MINOR_DEMAND_ONLY",
    "slag_ref_kgthm": "SLAG_REF_KGTHM", "fuel_per_kg_slag": "FUEL_PER_KG_SLAG",
    "sinter_ref_pct": "SINTER_REF_PCT", "fuel_per_pct_sinter": "FUEL_PER_PCT_SINTER",
    "raw_flux_ref_kgthm": "RAW_FLUX_REF_KGTHM", "fuel_per_kg_raw_flux": "FUEL_PER_KG_RAW_FLUX",
    "ks_fixed": "KS_FIXED", "price_basis": "PRICE_BASIS", "om_rs_thm": "OM_RS_THM",
    "raw_flux_min_cao_mgo": "RAW_FLUX_MIN_CAO_MGO", "dr_degree_floor": "DIRECT_REDUCTION_DEGREE",
    "mn_reduction_eff": "MN_REDUCTION_EFF", "fe_c_target": "FE_C_RATIO_TARGET", "fe_c_tol": "FE_C_RATIO_TOL",
}
_ENGINE_LOCK = threading.RLock()


def _engine_state():
    g = globals()
    return {"simple": {n: g[n] for n in _CFG_TO_GLOBAL.values()},
            "STOCK": dict(STOCK), "SINTER_MANUAL": dict(SINTER_MANUAL), "FURNACE": dict(FURNACE),
            "FUEL_TERMS": dict(FUEL_TERMS),
            "MATERIAL_RULES": {k: dict(v) for k, v in MATERIAL_RULES.items()},
            "FURNACE_PROFILES": {k: dict(v) for k, v in FURNACE_PROFILES.items()},
            "REF_RATES": dict(REF_RATES), "HEAT": copy.deepcopy(HEAT), "GUIDE": g["MGO_AL2O3_GUIDE"],
            "derived": (g["REQUIRED_FE_KGTHM"], g["HM_FE_KGTHM"])}


def _restore_state(s):
    g = globals()
    g.update(s["simple"])
    g["REQUIRED_FE_KGTHM"], g["HM_FE_KGTHM"] = s["derived"]
    for name, saved in (("STOCK", s["STOCK"]), ("SINTER_MANUAL", s["SINTER_MANUAL"]), ("FURNACE", s["FURNACE"]),
                        ("FUEL_TERMS", s["FUEL_TERMS"])):
        d = g[name]; d.clear(); d.update(saved)
    for k, v in s["MATERIAL_RULES"].items():
        MATERIAL_RULES[k].clear(); MATERIAL_RULES[k].update(v)
    FURNACE_PROFILES.clear(); FURNACE_PROFILES.update({k: dict(v) for k, v in s["FURNACE_PROFILES"].items()})
    REF_RATES.clear(); REF_RATES.update(s["REF_RATES"])
    HEAT.clear(); HEAT.update(s["HEAT"])
    g["MGO_AL2O3_GUIDE"] = s["GUIDE"]


def _apply_config(cfg):
    g = globals()
    for f, name in _CFG_TO_GLOBAL.items():
        g[name] = getattr(cfg, f)
    g["REQUIRED_FE_KGTHM"] = HM_BASIS_KG * g["FE_REQUIRED_PER_100KG_HM"] / 100.0
    g["HM_FE_KGTHM"] = HM_BASIS_KG * g["HM_FE_PCT"] / 100.0
    STOCK.clear(); STOCK.update(plan_hm_tonnes=float(cfg.stock_plan_hm_tonnes), balance=float(cfg.stock_balance))
    SINTER_MANUAL.clear(); SINTER_MANUAL.update(on=bool(cfg.sinter_manual_on), pct=float(cfg.sinter_manual_pct))
    FURNACE.clear(); FURNACE.update(name=cfg.furnace_name)
    FUEL_TERMS.clear(); FUEL_TERMS.update(_DEFAULT_FUEL_TERMS)
    FUEL_TERMS.update({k: bool(v) for k, v in cfg.fuel_terms.items() if k in _DEFAULT_FUEL_TERMS})
    for k, v in cfg.material_rules.items():
        if k in MATERIAL_RULES:
            MATERIAL_RULES[k]["coef"] = float(v["coef"]); MATERIAL_RULES[k]["ref"] = float(v["ref"])
    FURNACE_PROFILES.clear(); FURNACE_PROFILES.update({k: {"base": float(v["base"])} for k, v in cfg.furnace_profiles.items()})
    REF_RATES.clear(); REF_RATES.update(_DEFAULT_REF_RATES)
    REF_RATES.update({k: float(v) for k, v in cfg.ref_rates.items() if k in _DEFAULT_REF_RATES})
    g["MGO_AL2O3_GUIDE"] = (float(cfg.mgo_al2o3_guide_lo), float(cfg.mgo_al2o3_guide_hi))
    HEAT.clear(); HEAT.update(copy.deepcopy(HEAT_DEFAULT))
    for k, v in cfg.heat.items():
        if k in HEAT_DEFAULT:
            HEAT[k] = (float(v) if k in HEAT_NUMERIC_KEYS else (bool(v) if k == "calibrated" else v))


@contextlib.contextmanager
def session(cfg=None):
    """Load `cfg` (default: plant defaults) into the engine for the duration of the block, then restore.
    Serialised by a lock, so two users' settings can never mix."""
    cfg = Config() if cfg is None else cfg
    with _ENGINE_LOCK:
        snap = _engine_state()
        try:
            _apply_config(cfg)
            yield cfg
        finally:
            _restore_state(snap)


# ================================================================
# 13. DASHBOARD-FACING FUNCTIONS  (each loads the user's Config for the duration of the call)
# ================================================================
def solve(df, cfg=None, explain=True):
    """Least-cost burden for `df` under `cfg`. Returns (status, blend, cost, achieved, diagnostics)."""
    with session(cfg):
        return solve_mbf(ensure_columns(df), explain=explain)


def make_bundle(df, res, cfg=None, with_curve=True, progress=None):
    """Freeze inputs, results, settings and the 0-100 % sinter curve of a run (used by the dashboard and the Excel export)."""
    with session(cfg) as c:
        d = ensure_columns(df)
        cv = sinter_curve(d, progress=progress) if (with_curve and res[0] in ("Optimal", "Infeasible")) else None
        b = build_export_bundle(d, res, cv)
        if not with_curve:
            b["curve"] = None
        b["cfg"] = copy.deepcopy(c)
        b["rules_off"] = c.rules_off()
        if res[0] == "Optimal":
            T = {k: t for k, _t, t in b["tables"]}
            b["compact_oxides"] = compact_oxide_table(T["oxides"])
            b["decision"] = next((x for x in res[4] if x.startswith("SINTER DECISION")), None)
        return b


def curve(df, cfg=None, step=None, progress=None):
    """Coke and cost at every sinter share from 0 % to 100 % (attrs: lo, hi, edges, note)."""
    with session(cfg):
        return sinter_curve(ensure_columns(df), step, progress)


def sweep(df, cfg=None, start=None, end=None, step=1.0, progress=None, with_oxides=True):
    """Sinter sweep; with_oxides=True returns (table, oxides-by-sinter long table)."""
    with session(cfg):
        return sinter_sweep(ensure_columns(df), start, end, step, with_oxides=with_oxides, progress=progress)


def sensitivity(df, cfg=None, group="Iron_ore", column="Fe", step=1.0):
    k = float(step)
    if k <= 0:
        raise ValueError("Step must be above zero.")
    with session(cfg):
        return assay_sensitivity(ensure_columns(df), group, column, (-2 * k, -k, k, 2 * k))


def price_sens(df, cfg=None, target=None, step_pct=10.0):
    k = float(step_pct)
    if k <= 0:
        raise ValueError("Step must be above zero.")
    with session(cfg):
        return price_sensitivity(ensure_columns(df), target, (-2 * k, -k, k, 2 * k))


def tornado_run(df, cfg=None, swing_pct=10.0):
    if float(swing_pct) <= 0:
        raise ValueError("Swing must be above zero.")
    with session(cfg):
        return tornado(ensure_columns(df), float(swing_pct))


def breakeven(df, cfg=None):
    with session(cfg):
        return sinter_breakeven(ensure_columns(df))


def export_bytes(bundle, analysis=None, extra=None):
    """The optimised-results workbook as .xlsx bytes (in memory; nothing is written to disk).
    extra: optional callable(workbook) that adds the dashboard's own sheets (see analytics.extra_sheets)."""
    buf = io.BytesIO()
    with session(bundle.get("cfg")):
        write_export(bundle, buf, analysis, extra)
    return buf.getvalue()


def range_of_sinter(df, cfg=None):
    """Feasible sinter range inside the guard rails (dict lo/hi/gaps, or None)."""
    with session(cfg):
        return sinter_range(ensure_columns(df))


def calibrate_losses(ach, cfg):
    """Heat settings of `cfg` with the lower-furnace losses set so the heat balance closes for this run's burden
    (`ach` from a run made with `cfg`). Returns a new heat dict; nothing is changed until the caller stores it."""
    with session(cfg):
        calibrate_heat_losses(ach)
        return copy.deepcopy(HEAT)


def audit_template_bytes(example=True, cfg=None):
    """The heat-audit template (.xlsx bytes). example=True pre-fills a synthetic month made by the model."""
    buf = io.BytesIO()
    with session(cfg):
        audit_write_template(buf, synthetic_month(70.0, label="EXAMPLE - replace") if example else None)
    return buf.getvalue()


def audit_run_cfg(recs, cfg=None):
    """Audit the plant months under the user's settings. Adds the fit text and each usable month's sensitivity table."""
    with session(cfg):
        A = audit_run(recs)
        A["fit_text"] = audit_fit_text(A)
        A["sensitivity"] = {str(r["period"]): audit_sensitivity(rec) for r, rec in zip(A["results"], recs) if r["usable"]}
        return A


def audit_apply_cfg(A, cfg=None, mode="measured"):
    """(new heat dict, note): the audit's measured values written into the heat settings of `cfg`.
    The caller decides whether to store the new dict in the user's Config."""
    with session(cfg):
        note = audit_apply(A, mode)
        return copy.deepcopy(HEAT), note


def audit_export_bytes(A, cfg=None):
    buf = io.BytesIO()
    with session(cfg):
        audit_export(A, buf)
    return buf.getvalue()


def audit_examples(cfg=None):
    """Four model-generated plant months (55, 62, 70, 77 % sinter, true DRR falling 0.004 per point) for trying the audit."""
    with session(cfg):
        return [synthetic_month(s_, drr=0.44 - 0.004 * (s_ - 65.0), label=f"Synthetic {s_:g}% sinter") for s_ in (55.0, 62.0, 70.0, 77.0)]


def heat_placeholders():
    """The literature placeholder heat settings (a fresh copy)."""
    return copy.deepcopy(HEAT_DEFAULT)


# public aliases used by the dashboard
band_status = _band_status
fingerprint = _fingerprint


def quick_self_test():
    """A few seconds: does the core still solve inside its limits, and do the plant-review settings hold?
    Returns a list of (check, passed, detail)."""
    res = []
    ck = lambda n, c, d="": res.append((n, bool(c), d))
    with session(Config()):
        st, blend, cost, ach, diag = solve_mbf(demo_df(), explain=True)
        ck("demo data solves to Optimal", st == "Optimal", st)
        if st == "Optimal":
            ck("Fe closure exact", abs(ach["Fe_burden_kg"] - REQUIRED_FE_KGTHM) < 1e-3)
            ck("B2 / MgO / Al2O3 inside their bands", BASICITY_MIN - 1e-4 <= ach["B2"] <= BASICITY_MAX + 1e-4
               and MGO_MIN_PCT - 1e-4 <= ach["MgO_pct"] <= MGO_MAX_PCT + 1e-4 and AL2O3_MIN_PCT - 1e-4 <= ach["Al2O3_pct"] <= AL2O3_MAX_PCT + 1e-4)
            ck("fuel supplied equals the plant rule", abs(ach["Fuel_supplied"] - ach["Fuel_rule"]) < 0.05)
            ck("the run says what decided the sinter share", any(x.startswith("SINTER DECISION") for x in diag))
            ox = oxide_sources_table(blend, demo_df(), ach)
            ck("oxide sources add up to the slag", abs(float(ox.loc[ox["Material"] == "SLAG", "CaO kg"].iloc[0]) - ach["CaO_kg"]) < 0.01)
            ck("the heat balance is checked on every run (check mode leaves the answer unchanged)",
               "Heat" in ach and any(x.startswith("HEAT CHECK") for x in diag) and HEAT["mode"] == "check")
        ck("plant-review rules in force (545 base, 1 kg/pt sinter, B2 0.99-1.01, Ks 25)",
           all(v["base"] == 545.0 for v in FURNACE_PROFILES.values()) and FUEL_PER_PCT_SINTER == 1.0
           and (BASICITY_MIN, BASICITY_MAX) == (0.99, 1.01) and KS_FIXED == 25.0)
        cv = sinter_curve(demo_df(), step=10.0)
        ck("0-100 % sinter curve solves and finds its feasible limit", cv.attrs["hi"] is not None and len(cv) == 11, cv.attrs.get("note", "")[:80])
    return res
