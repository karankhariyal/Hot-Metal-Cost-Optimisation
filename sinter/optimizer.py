"""
SINTER BURDEN OPTIMIZER v42.0 - backend module used by the Streamlit dashboard (app.py).

This is the same model as the Colab notebook (v41 goal-programming solver + v42 output section),
without the notebook widgets. The dashboard and the notebook run identical solver code.

The solver is a GOAL-PROGRAMMING LADDER (lexicographic / pre-emptive), so it always returns the best
recipe it can and says exactly what it gave up:

  Tier 0  HARD, never relaxed : RM stock, mass balance, Mill Scale 5-15 %, fixed recycle rates,
                                IOL share, coke min/max, heat balance, FeO band
  Tier 1  Fe, Basicity        \\ deviation from the SPEC minimised tier by tier; the plant-approved
  Tier 2  CaO, MgO, Al2O3,    /  TOLERANCE is a hard outer wall
          SiO2, Al2O3/SiO2
  then    FeO closeness -> RM stock ratio (inventory-weight dial) -> cost

Status:  Optimal          = every spec met
         Relaxed          = some spec missed but inside the approved tolerance
         Production_Risk  = outside tolerance (closest recipe shown) or the hard limits cannot be met
                            (blend = None + shortage message)
         No_Production    = no fuel

Extra detail of every run: get_last_run_report() -> goal table vs spec and tolerance, ore stock-ratio
drift, "hold the ratio" comparison (option B), days of cover and shortage / drift messages.

PLACEHOLDERS TO CONFIRM WITH THE PLANT: every tolerance in DEFAULT_TOLERANCES except SiO2 6.2 %,
FEO_PIN_TOL_PCT, RATIO_HOLD_BAND_PP, GOAL_WEIGHTS.
"""

import copy
import re
from io import BytesIO

import numpy as np
import pandas as pd
import pulp

VERSION = "v42.0"

# ----------------------------------------------------------------------------
# CONSTANTS
# ----------------------------------------------------------------------------
OUT_KG = 1000.0                      # net sinter per tonne (produced by the fresh materials)

FE_TARGET = 54.0
FE_LOWER = 52.5
FE_UPPER = 54.5
FE_TOLERANCE = 1.5
FE_CENTER_WEIGHT = 2.0
DEVIATION_WEIGHTS = {"Fe": 5.0, "Basicity": 6.0, "CaO": 5.0, "MgO": 4.0,
                     "Al2O3": 3.0, "SiO2": 2.0, "Al2O3_SiO2_ratio": 2.0}
PIN_TOLERANCE = 1e-3
SIO2_MAX_SHORTAGE = 6.2

# Iron ore rules (Mill Scale rule unchanged)
MILL_SCALE_MIN_BURDEN_PCT = 0.05
MILL_SCALE_MAX_BURDEN_PCT = 0.15
IRON_ORE_GENERIC_MAX_PCT = 0.80          # per ore, share of total iron ore
IRON_ORE_GENERIC_MAX_PCT_RELAXED = 0.95
IRON_ORE_GENERIC_MAX_PCT_CRISIS = 0.95
MAX_IRON_ORE_PORTION = 0.80              # total ore, share of burden
MAX_IRON_ORE_PORTION_CRISIS = 0.95
MAX_FLUX_PORTION = 0.25                  # guard only: total flux share of burden

# Return sinter
IOL_FINES_NOMINAL_PCT = 0.08             # share of the charged mix
BF_RETURNS_NOMINAL_PCT = 0.17            # share of the charged mix
IOL_MIN_PCT, IOL_MAX_PCT = 5.0, 8.0
BFR_MIN_PCT, BFR_MAX_PCT = 17.0, 20.0
RS_TOTAL_MIN_PCT, RS_TOTAL_MAX_PCT = 20.0, 25.0
TOTAL_RS_NOMINAL_PCT = IOL_FINES_NOMINAL_PCT + BF_RETURNS_NOMINAL_PCT
BURDEN_ESTIMATE_KG_T = 1150.0            # only used to size IOL / BFR stock caps

# Inventory ratio
INVENTORY_DEV_TOL_KG = 0.05
DEFAULT_INVENTORY_WEIGHT = 1.0
DEFAULT_TECH_LIMIT_GROUPS = ("Flux", "Recycle")
DEFAULT_STOCK_BASIS = "as_received"      # "as_received" (wet) or "dry"
DEFAULT_PLANNING_TONNES = 10000.0
DEFAULT_HORIZON_DAYS = 7.0

# Costs / heat / coke
DEFAULT_OM_COST_RS_T = 750.0
DEFAULT_COKE_CV_KCAL_KG = 6800.0
DEFAULT_COKE_FC_PCT = 71.35
DEFAULT_HEAT_LATENT_MOISTURE = 540.0
DEFAULT_HEAT_CALCINATION_PER_LOI_KG = 420.0
DEFAULT_HEAT_MELTING_PER_KG_SINTER = 60.0
DEFAULT_HEAT_LOSS_FRACTION = 0.12
DEFAULT_FIRING_RATIO_MAX = 1.10
ENFORCE_FIRING_RATIO_MAX = False
DEFAULT_COKE_MIN_KG_T = 55.0
DEFAULT_COKE_MAX_KG_T = 85.0
DEFAULT_FEO_MIN_PCT = 8.5
DEFAULT_FEO_TARGET_PCT = 9.2
DEFAULT_FEO_MAX_PCT = 10.0
DEFAULT_FEO_REFERENCE_THERMAL_SURPLUS_KCAL = 189180.0
DEFAULT_FEO_REFERENCE_PCT = 8.6
DEFAULT_FEO_THERMAL_SLOPE_PCT_PER_10K_KCAL = 0.35
DEFAULT_REFERENCE_COKE_CV_KCAL_KG = 6800.0
DEFAULT_REFERENCE_COKE_FC_PCT = 71.35

# Productivity
DEFAULT_FEED_RATE_T_H = 70.0
DEFAULT_PRODUCTIVITY_TARGET = 1.4
DEFAULT_STRAND_AREA_M2 = 50.0            # PLACEHOLDER - enter the real strand area
FINES_PRODUCTIVITY_LOSS_TABLE = {
    13.0: 0.00, 14.0: 0.25, 15.0: 0.50, 16.0: 0.75, 17.0: 1.00,
    18.0: 1.25, 19.0: 1.50, 20.0: 1.75, 21.0: 2.00, 22.0: 2.25,
    23.0: 2.50, 24.0: 2.75, 25.0: 3.00, 26.0: 3.25,
}

DEFAULT_FUEL_ASH_SETTINGS = {
    "KSL_COKE": {"Ash_AR_Pct": 14.0, "Ash_SiO2_Pct": 52.5, "Ash_Al2O3_Pct": 25.0,
                 "Ash_CaO_Pct": 5.5, "Ash_MgO_Pct": 3.0, "Ash_Fe2O3_Pct": 10.0},
    "LOCAL_COKE": {"Ash_AR_Pct": 14.0, "Ash_SiO2_Pct": 52.5, "Ash_Al2O3_Pct": 25.0,
                   "Ash_CaO_Pct": 5.5, "Ash_MgO_Pct": 3.0, "Ash_Fe2O3_Pct": 10.0},
}
ACTIVE_FUEL_ASH_SETTINGS = copy.deepcopy(DEFAULT_FUEL_ASH_SETTINGS)

TARGETS = {
    "Fe_min": 54.0, "SiO2_max": 5.8, "Al2O3_max": 4.5, "Al2O3_SiO2_max": 0.6,
    "Basicity_min": 1.9, "Basicity_max": 2.0, "MgO_min": 2.2, "MgO_max": 2.4,
    "CaO_min": 10.5, "CaO_max": 11.5,
}

ROLE_COLUMN = "Material_Role"
_CBC = lambda: pulp.PULP_CBC_CMD(msg=0)


# ----------------------------------------------------------------------------
# SMALL HELPERS
# ----------------------------------------------------------------------------
def sanitize_material_name(raw_name):
    name = str(raw_name).strip()
    name = re.sub(r"\s+", "_", name)
    return re.sub(r"[^A-Za-z0-9_]", "", name)


def _norm_name(s):
    return re.sub(r"[^A-Z0-9]+", "_", str(s).strip().upper()).strip("_")


def _fuel_materials(df):
    return [m for m in df.index if str(df.loc[m, "Group"]).strip() == "Fuel"]


def _find_by_name(df, name):
    for m in df.index:
        if _norm_name(m) == name:
            return m
    return None


def _mill_scale_name(df):
    return _find_by_name(df, "MILL_SCALE")


def _iol_name(df):
    m = _find_by_name(df, "IOL_FINES")
    if m:
        return m
    for m in df.index:
        if str(df.loc[m, "Group"]).strip() == "IOL_Fines_Mandate":
            return m
    return None


def _bfr_name(df):
    m = _find_by_name(df, "BF_RETURNS")
    if m:
        return m
    for m in df.index:
        if str(df.loc[m, "Group"]).strip() == "BF_Returns_Mandate":
            return m
    return None


def _avail(df, m):
    return float(df.loc[m, "Available_Tonnes"]) > 0.0


def _dry_stock_t(df, m, stock_basis=DEFAULT_STOCK_BASIS):
    stock = max(float(df.loc[m, "Available_Tonnes"]), 0.0)
    if str(stock_basis) == "as_received":
        mois = float(df.loc[m, "Moisture_Pct"]) / 100.0
        stock *= (1.0 - mois) if mois < 1.0 else 0.0
    return stock


def _ensure_material_role(df):
    d = df.copy()
    if ROLE_COLUMN not in d.columns:
        legacy = {"SKME", "THAKUR", "RBSSN", "SMIORE"}
        d[ROLE_COLUMN] = ["Alternative_Iron_Ore" if str(m).strip().upper() in legacy
                          else ("Primary_Iron_Ore" if str(d.loc[m, "Group"]).strip() == "Iron_ore" else "Other")
                          for m in d.index]
    for col, default in [("CV_kcal_kg", 0.0), ("FC_Pct", 0.0), ("Fines_Pct", 0.0),
                         ("Tech_Min", 0.0), ("Tech_Max", 0.0)]:
        if col not in d.columns:
            d[col] = default
    for col in ["Tech_Min", "Tech_Max", "Fines_Pct", "CV_kcal_kg", "FC_Pct"]:
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0.0)
    return d


def _eligible_iron_ores(df):
    d = _ensure_material_role(df)
    ores = [m for m in d.index if str(d.loc[m, "Group"]).strip() == "Iron_ore"]
    out = []
    for m in ores:
        alt = str(d.loc[m, ROLE_COLUMN]).strip() == "Alternative_Iron_Ore"
        if alt and not _avail(d, m):
            continue
        out.append(m)
    return out


# ----------------------------------------------------------------------------
# FUEL ASH SETTINGS
# ----------------------------------------------------------------------------
def _normalise_fuel_ash_settings(settings=None, df=None):
    base = copy.deepcopy(DEFAULT_FUEL_ASH_SETTINGS)
    if settings:
        for mat, vals in settings.items():
            base.setdefault(mat, {}).update({k: float(v) for k, v in vals.items()})
    if df is not None:
        for m in _fuel_materials(df):
            if m not in base:
                base[m] = copy.deepcopy(base.get("KSL_COKE", next(iter(base.values()))))
    return base


def set_active_fuel_ash_settings(settings):
    global ACTIVE_FUEL_ASH_SETTINGS
    ACTIVE_FUEL_ASH_SETTINGS = _normalise_fuel_ash_settings(settings)
    return copy.deepcopy(ACTIVE_FUEL_ASH_SETTINGS)


def get_active_fuel_ash_settings(df=None):
    return _normalise_fuel_ash_settings(ACTIVE_FUEL_ASH_SETTINGS, df=df)


def _fuel_ash_coefficients(df, fuel_ash_settings=None):
    settings = _normalise_fuel_ash_settings(fuel_ash_settings, df=df)
    coeff = {}
    for m in _fuel_materials(df):
        vals = settings.get(m, settings.get("KSL_COKE", {}))
        moisture = float(df.loc[m, "Moisture_Pct"]) / 100.0
        ash_ar = float(vals.get("Ash_AR_Pct", 0.0)) / 100.0
        adf = ash_ar / (1.0 - moisture) if moisture < 1.0 else 0.0
        coeff[m] = {"Ash": adf,
                    "SiO2": adf * float(vals.get("Ash_SiO2_Pct", 0.0)) / 100.0,
                    "Al2O3": adf * float(vals.get("Ash_Al2O3_Pct", 0.0)) / 100.0,
                    "CaO": adf * float(vals.get("Ash_CaO_Pct", 0.0)) / 100.0,
                    "MgO": adf * float(vals.get("Ash_MgO_Pct", 0.0)) / 100.0,
                    "Fe2O3": adf * float(vals.get("Ash_Fe2O3_Pct", 0.0)) / 100.0}
    return coeff


def compute_fuel_ash_contribution(blend, df, fuel_ash_settings=None):
    settings = _normalise_fuel_ash_settings(fuel_ash_settings, df=df)
    totals = {"Ash_kg": 0.0, "SiO2_kg": 0.0, "Al2O3_kg": 0.0, "CaO_kg": 0.0, "MgO_kg": 0.0}
    per_fuel = {}
    for m in _fuel_materials(df):
        if m not in blend:
            continue
        dry_kg = float(blend[m])
        vals = settings.get(m, settings.get("KSL_COKE", {}))
        moisture = float(df.loc[m, "Moisture_Pct"]) / 100.0
        ash_ar = float(vals.get("Ash_AR_Pct", 0.0)) / 100.0
        ash_dry_pct = ash_ar / (1.0 - moisture) * 100.0 if moisture < 1.0 else 0.0
        ash_kg = dry_kg * ash_ar / (1.0 - moisture) if moisture < 1.0 else 0.0
        si = ash_kg * float(vals.get("Ash_SiO2_Pct", 0.0)) / 100.0
        al = ash_kg * float(vals.get("Ash_Al2O3_Pct", 0.0)) / 100.0
        ca = ash_kg * float(vals.get("Ash_CaO_Pct", 0.0)) / 100.0
        mg = ash_kg * float(vals.get("Ash_MgO_Pct", 0.0)) / 100.0
        fe2o3 = ash_kg * float(vals.get("Ash_Fe2O3_Pct", 0.0)) / 100.0
        totals["Ash_kg"] += ash_kg; totals["SiO2_kg"] += si; totals["Al2O3_kg"] += al
        totals["CaO_kg"] += ca; totals["MgO_kg"] += mg
        per_fuel[m] = {"Dry_Fuel_kg": dry_kg, "Ash_AR_Pct": ash_ar * 100.0, "Ash_Dry_Pct": ash_dry_pct,
                       "Ash_kg": ash_kg, "SiO2_kg": si, "Al2O3_kg": al, "CaO_kg": ca, "MgO_kg": mg,
                       "Fe2O3_kg": fe2o3}
    totals["Per_Fuel"] = per_fuel
    return totals


# ----------------------------------------------------------------------------
# DEFAULT MASTER
# ----------------------------------------------------------------------------
def get_default_chemistry():
    """Built-in master (same values as Master_Chemistry_Input_Template.xlsx).
    Tech Min/Max: 0 = no limit. Only Flux and Recycle use them by default."""
    rows = [
        # name, group, Fe, SiO2, Al2O3, CaO, MgO, LOI, moist, tmin, tmax, stock, price, cv, fc
        ("MILL_SCALE", "Iron_ore", 68.34, 2.00, 2.72, 0.0, 0.0, 2.50, 6.0, 0, 0, 2000, 7800, 0, 0),
        ("LLOYD", "Iron_ore", 63.52, 3.86, 2.27, 0.022, 0.034, 2.29, 5.0, 0, 0, 10000, 7820, 0, 0),
        ("DIOM", "Iron_ore", 57.17, 12.39, 2.93, 0.058, 0.114, 4.00, 6.0, 0, 0, 6000, 4600, 0, 0),
        ("SIOM", "Iron_ore", 59.34, 6.92, 3.72, 0.256, 0.331, 3.45, 6.0, 0, 0, 8000, 4600, 0, 0),
        ("KIOM", "Iron_ore", 58.41, 5.75, 5.48, 0.157, 0.018, 4.62, 6.0, 0, 0, 5000, 4900, 0, 0),
        ("INTERNAL_FINES", "Recycle", 50.0, 6.0, 4.5, 1.122, 0.06, 3.0, 1.1, 10, 10, 5000, 1000, 0, 0),
        ("IOL_Fines", "IOL_Fines_Mandate", 60.0, 5.0, 3.0, 8.79, 1.52, 3.0, 4.13, 0, 0, 5000, 5577, 0, 0),
        ("FLUE_DUST", "Recycle", 47.02, 7.07, 4.5, 1.1, 0.29, 15.0, 9.4, 10, 10, 3000, 500, 0, 0),
        ("IRON_POWDER", "Recycle", 47.02, 7.07, 4.5, 1.1, 0.29, 15.0, 9.4, 10, 10, 0, 500, 0, 0),
        ("BF_Returns", "BF_Returns_Mandate", 52.5, 5.62, 3.2, 10.74, 2.3, 3.0, 0.0, 0, 0, 5000, 0, 0, 0),
        ("DOLOMITE", "Flux", 0.54, 4.72, 0.95, 30.02, 18.75, 42.0, 2.0, 0, 0, 10000, 1340, 0, 0),
        ("LIMESTONE", "Flux", 0.88, 4.48, 1.19, 48.71, 2.59, 40.0, 2.0, 0, 0, 15000, 1355, 0, 0),
        ("QUICKLIME", "Flux", 0.01, 2.50, 0.61, 89.0, 1.57, 5.0, 0.0, 40, 65, 5000, 9200, 0, 0),
        ("LIME_POWDER", "Flux", 0.5, 3.0, 0.8, 52.0, 1.0, 38.0, 2.0, 0, 0, 0, 1200, 0, 0),
        ("KSL_COKE", "Fuel", 0, 0, 0, 0, 0, 0, 11.27, 0, 0, 6000, 15022, DEFAULT_COKE_CV_KCAL_KG, DEFAULT_COKE_FC_PCT),
        ("LOCAL_COKE", "Fuel", 0, 0, 0, 0, 0, 0, 11.27, 0, 0, 6000, 12500, DEFAULT_COKE_CV_KCAL_KG, DEFAULT_COKE_FC_PCT),
    ]
    cols = ["Material", "Group", "Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Moisture_Pct",
            "Tech_Min", "Tech_Max", "Available_Tonnes", "Price_Rs_t", "CV_kcal_kg", "FC_Pct"]
    df = pd.DataFrame(rows, columns=cols).set_index("Material")
    df["Fines_Pct"] = 0.0
    return _ensure_material_role(df)


# ----------------------------------------------------------------------------
# BOUNDS / RETURN-SINTER SETTINGS
# ----------------------------------------------------------------------------
def _tech_limits(df, m, tech_groups):
    g = str(df.loc[m, "Group"]).strip()
    if g not in tech_groups and g != "Recycle":
        return 0.0, 0.0
    return max(float(df.loc[m, "Tech_Min"]), 0.0), max(float(df.loc[m, "Tech_Max"]), 0.0)


def build_bounds(df, production_tonnes, tech_groups=DEFAULT_TECH_LIMIT_GROUPS,
                 stock_basis=DEFAULT_STOCK_BASIS, fixed=None):
    """kg/t bounds per material. RM stock over the planning horizon is a hard cap."""
    fixed = fixed or {}
    bfr = _bfr_name(df)
    bounds = {}
    prod = max(float(production_tonnes), 1e-9)
    for m in df.index:
        if m == bfr:
            bounds[m] = (0.0, 0.0)          # BFR is chemistry-only, never a physical burden
            continue
        if m in fixed:
            q = max(float(fixed[m]), 0.0)
            bounds[m] = (q, q)
            continue
        if not _avail(df, m):
            bounds[m] = (0.0, 0.0)
            continue
        cap = _dry_stock_t(df, m, stock_basis) / prod * 1000.0
        tmin, tmax = _tech_limits(df, m, tech_groups)
        hi = min(tmax, cap) if tmax > 0 else cap
        lo = min(tmin, hi)
        bounds[m] = (max(lo, 0.0), max(hi, 0.0))
    return bounds


def resolve_return_sinter(df, production_tonnes, iol_nominal, bf_nominal,
                          stock_basis=DEFAULT_STOCK_BASIS):
    """Effective BFR share (b) and IOL status. b is a share of the charged mix."""
    notes = []
    iol_nominal = min(max(float(iol_nominal), 0.0), 0.5)
    b_req = min(max(float(bf_nominal), 0.0), 0.6)
    bfr, iol = _bfr_name(df), _iol_name(df)
    prod = max(float(production_tonnes), 1e-9)

    b = b_req
    if b_req > 0:
        if bfr is None:
            b = 0.0; notes.append("BFR is not in the master - BFR chemistry share set to 0.")
        elif not _avail(df, bfr):
            b = 0.0; notes.append("BFR unavailable - its chemistry share is set to 0 %.")
        else:
            cap = _dry_stock_t(df, bfr, stock_basis) / prod * 1000.0     # kg/t available
            b_cap = cap / (BURDEN_ESTIMATE_KG_T + cap) if cap > 0 else 0.0
            if b_cap < b_req - 1e-9:
                b = b_cap
                notes.append(f"BFR stock covers only {b_cap*100:.1f} % of the charged mix for this horizon - share reduced from {b_req*100:.1f} %.")

    iol_mode, iol_cap = "none", 0.0
    if iol is not None:
        iol_cap = _dry_stock_t(df, iol, stock_basis) / prod * 1000.0 if _avail(df, iol) else 0.0
        nominal_est = iol_nominal / max(1.0 - b, 1e-9) * BURDEN_ESTIMATE_KG_T
        if iol_nominal <= 0:
            iol_mode = "off"
        elif iol_cap >= 0.98 * nominal_est:
            iol_mode = "share"
        else:
            iol_mode = "shortfall"
            notes.append(f"IOL Fines stock covers {iol_cap:.1f} kg/t vs {nominal_est:.1f} kg/t needed - IOL fixed at the stock limit; iron ore compensates.")
    return {"b": b, "b_requested": b_req, "iol_nominal": iol_nominal, "iol_mode": iol_mode,
            "iol_cap_kg_t": iol_cap, "notes": notes, "bfr": bfr, "iol": iol}


def charged_mix_summary(blend, df, bf_nominal):
    """Fresh burden, BFR poured and charged mix (kg per tonne of net sinter)."""
    bfr = _bfr_name(df)
    fresh = sum(float(v) for m, v in blend.items() if m != bfr)
    b = min(max(float(bf_nominal), 0.0), 0.6) if bfr else 0.0
    poured = b / (1.0 - b) * fresh if b > 0 else 0.0
    loi_b = float(df.loc[bfr, "LOI"]) / 100.0 if bfr else 0.0
    retained = poured * (1.0 - loi_b)
    iol = _iol_name(df)
    iol_kg = float(blend.get(iol, 0.0)) if iol else 0.0
    mix = fresh + poured
    return {"Fresh_Burden_kg_t": fresh, "BFR_Poured_kg_t": poured, "BFR_Retained_kg_t": retained,
            "Charged_Mix_kg_t": mix, "Gross_Sinter_kg_t": OUT_KG + retained,
            "BFR_Pct_of_Mix": poured / mix * 100.0 if mix else 0.0,
            "IOL_kg_t": iol_kg, "IOL_Pct_of_Mix": iol_kg / mix * 100.0 if mix else 0.0,
            "Return_Sinter_Pct_of_Mix": (iol_kg + poured) / mix * 100.0 if mix else 0.0}


# ----------------------------------------------------------------------------
# CHEMISTRY (LP expressions and post-solve)
# ----------------------------------------------------------------------------
def _lp_chemistry(x, df, fuel_ash_settings, b):
    """Element masses (kg per net tonne) and gross sinter mass as linear expressions."""
    bfr = _bfr_name(df)
    nonfuel = [m for m in x if m != bfr and str(df.loc[m, "Group"]).strip() != "Fuel"]
    fuels = [m for m in x if str(df.loc[m, "Group"]).strip() == "Fuel"]
    coeff = _fuel_ash_coefficients(df, fuel_ash_settings)
    F = pulp.lpSum(x[m] for m in x if m != bfr)
    k = b / (1.0 - b) if b > 0 else 0.0
    loi_b = float(df.loc[bfr, "LOI"]) / 100.0 if bfr else 0.0
    R = (k * (1.0 - loi_b)) * F                                  # BFR-derived sinter (kg)
    G = OUT_KG + R                                               # gross sinter (kg)

    def el(col):
        e = pulp.lpSum(x[m] * float(df.loc[m, col]) / 100.0 for m in nonfuel)
        if col in ("SiO2", "Al2O3", "CaO", "MgO"):
            e = e + pulp.lpSum(x[m] * coeff[m][col] for m in fuels if m in coeff)
        if bfr and b > 0:
            e = e + R * (float(df.loc[bfr, col]) / 100.0)
        return e
    return {c: el(c) for c in ["Fe", "SiO2", "Al2O3", "CaO", "MgO"]}, G, F


def compute_achieved(blend, df, OUT=OUT_KG, fuel_ash_settings=None, bf_nominal=BF_RETURNS_NOMINAL_PCT):
    """Sinter chemistry on the gross-sinter basis (net 1000 kg + BFR-derived sinter)."""
    bfr = _bfr_name(df)
    b = min(max(float(bf_nominal), 0.0), 0.6) if bfr else 0.0
    nonfuel = [m for m in blend if m != bfr and str(df.loc[m, "Group"]).strip() != "Fuel"]
    mass = {c: sum(blend[m] * float(df.loc[m, c]) / 100.0 for m in nonfuel)
            for c in ["Fe", "SiO2", "Al2O3", "CaO", "MgO"]}
    ash = compute_fuel_ash_contribution(blend, df, fuel_ash_settings)
    mass["SiO2"] += ash["SiO2_kg"]; mass["Al2O3"] += ash["Al2O3_kg"]
    mass["CaO"] += ash["CaO_kg"]; mass["MgO"] += ash["MgO_kg"]
    cm = charged_mix_summary(blend, df, b)
    R = cm["BFR_Retained_kg_t"] if b > 0 else 0.0
    if bfr and R > 0:
        for c in mass:
            mass[c] += R * float(df.loc[bfr, c]) / 100.0
    G = OUT + R
    a = {c: mass[c] / G * 100.0 for c in mass}
    a.update({"BFR_Poured_kg_t": cm["BFR_Poured_kg_t"], "BFR_Retained_kg_t": R,
              "Gross_Sinter_kg_t": G, "Charged_Mix_kg_t": cm["Charged_Mix_kg_t"],
              "Fuel_Ash_kg": ash["Ash_kg"], "Fuel_Ash_SiO2_kg": ash["SiO2_kg"],
              "Fuel_Ash_Al2O3_kg": ash["Al2O3_kg"], "Fuel_Ash_CaO_kg": ash["CaO_kg"],
              "Fuel_Ash_MgO_kg": ash["MgO_kg"]})
    if a["SiO2"] > 0:
        a["Basicity"] = a["CaO"] / a["SiO2"]
        a["Al2O3/SiO2"] = a["Al2O3"] / a["SiO2"]
        a["B4"] = (a["CaO"] + a["MgO"]) / (a["SiO2"] + a["Al2O3"])
    else:
        a["Basicity"] = a["Al2O3/SiO2"] = a["B4"] = 0.0
    return a


def compute_validation_chemistry(blend, df, finished_sinter_kg, recovery=None, fuel_ash_settings=None,
                                 bf_nominal=BF_RETURNS_NOMINAL_PCT):
    """Predict sinter chemistry from an ACTUAL dry recipe. `finished_sinter_kg` is the net sinter
    made by the fresh burden; the BFR-derived sinter is added on the same basis as the optimizer."""
    if finished_sinter_kg <= 0:
        raise ValueError("Finished sinter quantity must be greater than zero.")
    recovery = recovery or {"Fe": 1.0, "SiO2": 1.0, "Al2O3": 1.0, "CaO": 1.0, "MgO": 1.0}
    for key in ["Fe", "SiO2", "Al2O3", "CaO", "MgO"]:
        if key not in recovery or recovery[key] < 0:
            raise ValueError(f"Invalid recovery factor for {key}.")
    bfr = _bfr_name(df)
    nonfuel = [m for m in blend if m != bfr and str(df.loc[m, "Group"]).strip() != "Fuel"]
    inp = {c: sum(blend[m] * float(df.loc[m, c]) / 100.0 for m in nonfuel)
           for c in ["Fe", "SiO2", "Al2O3", "CaO", "MgO"]}
    loi_mass = sum(blend[m] * float(df.loc[m, "LOI"]) / 100.0 for m in nonfuel)
    ash = compute_fuel_ash_contribution(blend, df, fuel_ash_settings)
    inp["SiO2"] += ash["SiO2_kg"]; inp["Al2O3"] += ash["Al2O3_kg"]
    inp["CaO"] += ash["CaO_kg"]; inp["MgO"] += ash["MgO_kg"]
    fresh = sum(v for m, v in blend.items() if m != bfr)
    b = min(max(float(bf_nominal), 0.0), 0.6) if bfr else 0.0
    poured = b / (1 - b) * fresh if b > 0 else 0.0
    R = poured * (1.0 - (float(df.loc[bfr, "LOI"]) / 100.0 if bfr else 0.0))
    sinter = {c: inp[c] * recovery[c] for c in inp}
    if bfr and R > 0:
        for c in sinter:
            sinter[c] += R * float(df.loc[bfr, c]) / 100.0
    G = finished_sinter_kg + R
    res = {c: sinter[c] / G * 100.0 for c in sinter}
    res["Basicity"] = res["CaO"] / res["SiO2"] if res["SiO2"] > 0 else np.nan
    res["Al2O3/SiO2"] = res["Al2O3"] / res["SiO2"] if res["SiO2"] > 0 else np.nan
    res.update({"Total_dry_burden_kg": fresh, "Finished_sinter_kg": finished_sinter_kg,
                "Gross_sinter_kg": G, "BFR_Poured_kg": poured, "BFR_Retained_kg": R,
                "Actual_yield_pct": finished_sinter_kg / fresh * 100.0 if fresh > 0 else np.nan,
                "LOI_mass_kg": loi_mass, "Fuel_Ash_kg": ash["Ash_kg"]})
    return res


# ----------------------------------------------------------------------------
# COSTING TABLES
# ----------------------------------------------------------------------------
def compute_dry_cost_table(blend, df, om_cost):
    total = sum(blend.values())
    rm_cost = sum(blend[m] * df.loc[m, "Price_Rs_t"] / 1000 for m in blend)
    table = pd.DataFrame({
        "Group": [df.loc[m, "Group"] for m in blend],
        "Dry kg / t sinter": [blend[m] for m in blend],
        "% of Burden": [(blend[m] / total) * 100 if total else 0 for m in blend],
        "Dry Cost (Rs/t)": [round(blend[m] * df.loc[m, "Price_Rs_t"] / 1000, 2) for m in blend],
    }, index=list(blend.keys()))
    table = pd.concat([table, pd.DataFrame({"Group": ["TOTAL"], "Dry kg / t sinter": [total],
                                            "% of Burden": [100.0], "Dry Cost (Rs/t)": [round(rm_cost, 2)]},
                                           index=["TOTAL"])])
    return table, rm_cost, rm_cost + om_cost


def compute_wet_cost_table(blend, df, om_cost):
    rows, wet_cost_total, tdry, twet = [], 0.0, 0.0, 0.0
    for m in blend:
        mois = float(df.loc[m, "Moisture_Pct"]) / 100 if float(df.loc[m, "Moisture_Pct"]) < 100 else 0.0
        dry = blend[m]
        wet = dry / (1 - mois) if mois < 1 else dry
        cost = wet * df.loc[m, "Price_Rs_t"] / 1000
        wet_cost_total += cost; tdry += dry; twet += wet
        rows.append({"Material": m, "Group": df.loc[m, "Group"], "Dry kg / t sinter": dry,
                     "Moisture %": round(mois * 100, 2), "Wet (As-Received) kg": round(wet, 2),
                     "Wet Cost (Rs/t)": round(cost, 2)})
    table = pd.DataFrame(rows).set_index("Material")
    table = pd.concat([table, pd.DataFrame({"Group": ["TOTAL"], "Dry kg / t sinter": [tdry], "Moisture %": [0.0],
                                            "Wet (As-Received) kg": [twet], "Wet Cost (Rs/t)": [round(wet_cost_total, 2)]},
                                           index=["TOTAL"])])
    table["Moisture %"] = table["Moisture %"].astype(float)
    return table, wet_cost_total, wet_cost_total + om_cost


# ----------------------------------------------------------------------------
# HEAT BALANCE DIAGNOSTIC (unchanged model)
# ----------------------------------------------------------------------------
def compute_coke_heat_balance_diagnostic(blend, df, OUT=OUT_KG,
                                         latent_heat=DEFAULT_HEAT_LATENT_MOISTURE,
                                         calcination_heat=DEFAULT_HEAT_CALCINATION_PER_LOI_KG,
                                         melting_heat=DEFAULT_HEAT_MELTING_PER_KG_SINTER,
                                         loss_fraction=DEFAULT_HEAT_LOSS_FRACTION,
                                         feo_min=DEFAULT_FEO_MIN_PCT, feo_target=DEFAULT_FEO_TARGET_PCT,
                                         feo_max=DEFAULT_FEO_MAX_PCT,
                                         feo_ref_surplus=DEFAULT_FEO_REFERENCE_THERMAL_SURPLUS_KCAL,
                                         feo_ref_pct=DEFAULT_FEO_REFERENCE_PCT,
                                         feo_thermal_slope=DEFAULT_FEO_THERMAL_SLOPE_PCT_PER_10K_KCAL,
                                         ref_coke_cv=DEFAULT_REFERENCE_COKE_CV_KCAL_KG,
                                         ref_coke_fc=DEFAULT_REFERENCE_COKE_FC_PCT):
    fuel_mats = [m for m in _fuel_materials(df) if m in blend]
    if not fuel_mats:
        return None
    per_fuel, q_fuel, total_fuel = {}, 0.0, 0.0
    for m in fuel_mats:
        kg = blend.get(m, 0.0)
        cv = float(df.loc[m, "CV_kcal_kg"]) or DEFAULT_COKE_CV_KCAL_KG
        fc = float(df.loc[m, "FC_Pct"]) or DEFAULT_COKE_FC_PCT
        q = kg * fc / 100 * cv
        q_fuel += q; total_fuel += kg
        per_fuel[m] = {"kg": kg, "CV": cv, "FC": fc, "Q_kcal": q}
    bfr = _bfr_name(df)
    wet_mass = sum(blend[m] / (1 - df.loc[m, "Moisture_Pct"] / 100) if df.loc[m, "Moisture_Pct"] < 100 else blend[m]
                   for m in blend if m not in fuel_mats and m != bfr)
    dry_nonfuel = sum(blend[m] for m in blend if m not in fuel_mats and m != bfr)
    q_moist = max(wet_mass - dry_nonfuel, 0.0) * latent_heat
    loi_mass = sum(blend[m] * df.loc[m, "LOI"] / 100 for m in blend if m not in fuel_mats and m != bfr)
    q_calc = loi_mass * calcination_heat
    q_melt = OUT * melting_heat
    q_req = (q_moist + q_calc + q_melt) / (1 - loss_fraction) if loss_fraction < 1 else 0.0
    firing = q_fuel / q_req if q_req > 0 else 0.0
    surplus = q_fuel - q_req
    w_fc = sum(v["kg"] * v["FC"] for v in per_fuel.values()) / total_fuel if total_fuel > 0 else DEFAULT_COKE_FC_PCT
    w_cv = sum(v["kg"] * v["CV"] for v in per_fuel.values()) / total_fuel if total_fuel > 0 else DEFAULT_COKE_CV_KCAL_KG
    eff = total_fuel * (w_fc / ref_coke_fc) * (w_cv / ref_coke_cv)
    feo = feo_ref_pct + feo_thermal_slope * ((surplus - feo_ref_surplus) / 10000.0)
    if feo < feo_min:
        sug = f"FeO {feo:.2f}% below minimum {feo_min:.2f}% -> increase coke / review thermal conditions"
    elif feo > feo_max:
        sug = f"FeO {feo:.2f}% above maximum {feo_max:.2f}% -> reduce coke / review thermal conditions"
    elif abs(feo - feo_target) <= 0.15:
        sug = f"FeO {feo:.2f}% is close to target {feo_target:.2f}% - no adjustment suggested"
    elif feo < feo_target:
        sug = f"FeO {feo:.2f}% is below target {feo_target:.2f}% but within operating band"
    else:
        sug = f"FeO {feo:.2f}% is above target {feo_target:.2f}% but within operating band"
    return {"Per_Fuel": per_fuel, "Total_Fuel_kg": total_fuel, "Weighted_CV": w_cv, "Weighted_FC": w_fc,
            "Q_fuel_kcal": q_fuel, "Q_required_kcal": q_req, "Thermal_Surplus_kcal": surplus,
            "Firing_Ratio": firing, "FeO_Estimate_Pct": feo, "FeO_Min_Pct": feo_min,
            "FeO_Target_Pct": feo_target, "FeO_Max_Pct": feo_max,
            "Reference_Thermal_Surplus_kcal": feo_ref_surplus,
            "Thermal_Slope_Pct_per_10k_kcal": feo_thermal_slope, "Effective_Coke_kg_t": eff,
            "Controller_Suggestion": sug,
            "note": "PROVISIONAL FeO thermal-state model - calibrate the coefficients against plant Coke/FeO history."}


# ----------------------------------------------------------------------------
# INVENTORY-RATIO TARGETS
# ----------------------------------------------------------------------------
def _fuel_quality_index(df, fuels):
    vals = {}
    for m in fuels:
        cv = float(df.loc[m, "CV_kcal_kg"]) or DEFAULT_COKE_CV_KCAL_KG
        fc = float(df.loc[m, "FC_Pct"]) or DEFAULT_COKE_FC_PCT
        vals[m] = cv * fc
    ref = float(np.mean(list(vals.values()))) if vals else 1.0
    return {m: (v / ref if ref > 0 else 1.0) for m, v in vals.items()}


def inventory_ratio_sets(df, iron_ores, unavailable_iron, fixed=None, stock_basis=DEFAULT_STOCK_BASIS):
    """Groups whose usage is steered by the inventory ratio: {name: {material: weight}}.
    Iron ore (Mill Scale excluded) weight = dry stock; Fuel weight = dry stock x quality index."""
    fixed = fixed or {}
    sets = {}
    ms = _mill_scale_name(df)
    ores = [m for m in iron_ores if m not in unavailable_iron and m != ms and m not in fixed and _avail(df, m)]
    if len(ores) >= 2:
        sets["Iron ore"] = {m: _dry_stock_t(df, m, stock_basis) for m in ores}
    fuels = [m for m in _fuel_materials(df) if _avail(df, m) and m not in fixed]
    if len(fuels) >= 2:
        q = _fuel_quality_index(df, fuels)
        sets["Fuel"] = {m: _dry_stock_t(df, m, stock_basis) * q[m] for m in fuels}
    return sets


def inventory_usage_report(blend, df, production_tonnes, horizon_days=DEFAULT_HORIZON_DAYS,
                           stock_basis=DEFAULT_STOCK_BASIS, fixed=None):
    """Per-material stock use over the planning horizon, with target vs actual share."""
    if not blend:
        return pd.DataFrame()
    iron_ores = _eligible_iron_ores(df)
    unavailable = [m for m in iron_ores if not _avail(df, m)]
    sets = inventory_ratio_sets(df, iron_ores, unavailable, fixed, stock_basis)
    target = {}
    for gname, w in sets.items():
        tot_w = sum(w.values()) or 1.0
        tot_x = sum(blend.get(m, 0.0) for m in w) or 0.0
        for m, wi in w.items():
            target[m] = (gname, wi / tot_w * 100.0, (blend.get(m, 0.0) / tot_x * 100.0) if tot_x > 0 else 0.0)
    rows = []
    prod = float(production_tonnes)
    ms = _mill_scale_name(df)
    burden = sum(v for m, v in blend.items() if m != _bfr_name(df)) or 1.0
    for m in df.index:
        if m == _bfr_name(df) or not _avail(df, m):
            continue
        kg = float(blend.get(m, 0.0))
        used_t = kg * prod / 1000.0
        dry_stock = _dry_stock_t(df, m, stock_basis)
        cover = dry_stock / used_t if used_t > 1e-9 else np.inf
        row = {"Material": m, "Group": df.loc[m, "Group"], "Stock t": float(df.loc[m, "Available_Tonnes"]),
               "kg/t": kg, "Tonnes used": used_t,
               "% of stock used": used_t / dry_stock * 100.0 if dry_stock > 0 else np.nan,
               "Cover (x horizon)": cover,
               "Days of cover": cover * float(horizon_days) if np.isfinite(cover) else np.inf}
        if m in target:
            g, tshare, ashare = target[m]
            row.update({"Ratio group": g, "Target share %": tshare, "Actual share %": ashare,
                        "Deviation pp": ashare - tshare,
                        "Note": "limited by chemistry / other rules" if abs(ashare - tshare) > 5.0 else "follows stock ratio"})
        elif m == ms:
            row.update({"Ratio group": "-", "Target share %": np.nan, "Actual share %": kg / burden * 100.0,
                        "Deviation pp": np.nan, "Note": "Mill Scale rule: 5-15 % of burden"})
        else:
            row.update({"Ratio group": "-", "Target share %": np.nan, "Actual share %": np.nan,
                        "Deviation pp": np.nan, "Note": ""})
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# MAIN SOLVER
# ----------------------------------------------------------------------------
def check_fuel_gate(df):
    fuels = _fuel_materials(df)
    usable = [m for m in fuels if _avail(df, m)]
    if fuels and not usable:
        return False, ["No Fuel-group material is available (all coke sources have zero stock)."]
    return True, []


def get_iron_ore_tier(df, iron_ores, extra_missing=0, extra_reasons=None):
    extra_reasons = extra_reasons or []
    unavailable = [m for m in iron_ores if not _avail(df, m)]
    n = len(unavailable) + extra_missing
    shown = unavailable + [f"{r}(shortfall)" for r in extra_reasons]
    if n == 0:
        return ({m: IRON_ORE_GENERIC_MAX_PCT for m in iron_ores}, unavailable,
                "All iron ores available - common 80 % ceiling per ore.", "base")
    if n == 1:
        return ({m: IRON_ORE_GENERIC_MAX_PCT_RELAXED for m in iron_ores}, unavailable,
                f"One iron-ore shortfall ({', '.join(shown)}) - per-ore ceiling relaxed.", "relaxed")
    return ({m: IRON_ORE_GENERIC_MAX_PCT_CRISIS for m in iron_ores}, unavailable,
            f"{n} iron-ore shortfalls ({', '.join(shown)}) - crisis ceilings.", "crisis")


# ----------------------------------------------------------------------------
# GOAL-PROGRAMMING LADDER (v41)
# ----------------------------------------------------------------------------
# Tier 0  HARD (never relaxed): RM stock, mass balance, Mill Scale rule, fixed recycle rates,
#         IOL share, coke min/max, heat balance and the FeO band.
# Tier 1  Fe and Basicity  |  Tier 2  CaO, MgO, Al2O3, SiO2, Al2O3/SiO2
#         -> deviation from the SPEC is minimised tier by tier. The plant-approved TOLERANCE is a hard
#            outer wall (attempt 1). If even that cannot be met the model drops the wall (attempt 2)
#            and returns the closest recipe, labelled Production_Risk.
# Then    FeO closeness -> RM stock ratio (inventory weight dial) -> cost.
#
# TOLERANCES BELOW ARE PLACEHOLDERS except SiO2 6.2 % (the v40 plant-agreed shortage ceiling).
# Confirm every one with the plant before using the numbers for decisions.
DEFAULT_TOLERANCES = {
    "Fe_min": 52.0, "Fe_max": 55.0,
    "SiO2_max": 6.2,
    "Al2O3_max": 4.8,
    "Al2O3_SiO2_max": 0.65,
    "Basicity_min": 1.8, "Basicity_max": 2.1,
    "MgO_min": 2.0, "MgO_max": 2.6,
    "CaO_min": 10.0, "CaO_max": 12.4,
}
GOAL_WEIGHTS = {"Fe": 5.0, "Basicity": 6.0, "CaO": 5.0, "MgO": 4.0, "Al2O3": 3.0, "SiO2": 2.0, "Al2O3_SiO2": 2.0}
FEO_PIN_TOL_PCT = 0.15           # FeO closeness is pinned to within this many FeO points of its best (placeholder)
RATIO_HOLD_BAND_PP = 5.0           # "hold the stock ratio" option: each ore within +/- this many points of its stock share
RATIO_DRIFT_WARN_PP = 5.0          # warn when an ore's share drifts further than this from its stock share
SPEC_CHECK_TOL = 0.005             # pp / ratio units: "spec met" threshold AND the slack each goal keeps when its tier is pinned
LAST_RUN = {}


def get_last_run_report():
    """Goal table, ratio options and plain-language messages of the most recent solve."""
    return dict(LAST_RUN)


def _merge_tolerances(tolerances, targets):
    tol = dict(DEFAULT_TOLERANCES)
    for k, v in (tolerances or {}).items():
        if k in tol and v is not None:
            tol[k] = float(v)
    # a tolerance can never be tighter than the spec it protects
    tol["Fe_min"] = min(tol["Fe_min"], FE_LOWER); tol["Fe_max"] = max(tol["Fe_max"], FE_UPPER)
    for k in ("SiO2_max", "Al2O3_max", "Al2O3_SiO2_max", "Basicity_max", "MgO_max", "CaO_max"):
        tol[k] = max(tol[k], float(targets[k]))
    for k in ("Basicity_min", "MgO_min", "CaO_min"):
        tol[k] = min(tol[k], float(targets[k]))
    return tol


def _spec_gaps(a, targets):
    """Positive number = how far outside the spec (pp, or ratio units)."""
    g = {"Fe": max(FE_LOWER - a["Fe"], a["Fe"] - FE_UPPER, 0.0),
         "SiO2": max(a["SiO2"] - targets["SiO2_max"], 0.0),
         "Al2O3": max(a["Al2O3"] - targets["Al2O3_max"], 0.0),
         "Al2O3/SiO2": max(a["Al2O3/SiO2"] - targets["Al2O3_SiO2_max"], 0.0),
         "Basicity": max(targets["Basicity_min"] - a["Basicity"], a["Basicity"] - targets["Basicity_max"], 0.0),
         "MgO": max(targets["MgO_min"] - a["MgO"], a["MgO"] - targets["MgO_max"], 0.0),
         "CaO": max(targets["CaO_min"] - a["CaO"], a["CaO"] - targets["CaO_max"], 0.0)}
    return g


def _goal_table(a, targets, tol):
    rows = []
    spec_txt = {"Fe": f"{FE_LOWER:.1f}-{FE_UPPER:.1f}", "SiO2": f"<= {targets['SiO2_max']}", "Al2O3": f"<= {targets['Al2O3_max']}",
                "Al2O3/SiO2": f"<= {targets['Al2O3_SiO2_max']}", "Basicity": f"{targets['Basicity_min']}-{targets['Basicity_max']}",
                "MgO": f"{targets['MgO_min']}-{targets['MgO_max']}", "CaO": f"{targets['CaO_min']}-{targets['CaO_max']}"}
    tol_txt = {"Fe": f"{tol['Fe_min']:.1f}-{tol['Fe_max']:.1f}", "SiO2": f"<= {tol['SiO2_max']}", "Al2O3": f"<= {tol['Al2O3_max']}",
               "Al2O3/SiO2": f"<= {tol['Al2O3_SiO2_max']}", "Basicity": f"{tol['Basicity_min']}-{tol['Basicity_max']}",
               "MgO": f"{tol['MgO_min']}-{tol['MgO_max']}", "CaO": f"{tol['CaO_min']}-{tol['CaO_max']}"}
    tol_gap = {"Fe": max(tol["Fe_min"] - a["Fe"], a["Fe"] - tol["Fe_max"], 0.0),
               "SiO2": max(a["SiO2"] - tol["SiO2_max"], 0.0), "Al2O3": max(a["Al2O3"] - tol["Al2O3_max"], 0.0),
               "Al2O3/SiO2": max(a["Al2O3/SiO2"] - tol["Al2O3_SiO2_max"], 0.0),
               "Basicity": max(tol["Basicity_min"] - a["Basicity"], a["Basicity"] - tol["Basicity_max"], 0.0),
               "MgO": max(tol["MgO_min"] - a["MgO"], a["MgO"] - tol["MgO_max"], 0.0),
               "CaO": max(tol["CaO_min"] - a["CaO"], a["CaO"] - tol["CaO_max"], 0.0)}
    gaps = _spec_gaps(a, targets)
    for k in ["Fe", "Basicity", "CaO", "MgO", "Al2O3", "SiO2", "Al2O3/SiO2"]:
        status = ("Met" if gaps[k] <= 0.005 else "Met (rounding)") if gaps[k] <= SPEC_CHECK_TOL else ("OUTSIDE approved tolerance" if tol_gap[k] > SPEC_CHECK_TOL
                                                          else "Relaxed (within approved tolerance)")
        rows.append({"Goal": k, "Spec": spec_txt[k], "Achieved": a[k], "Outside spec by": gaps[k],
                     "Approved tolerance": tol_txt[k], "Status": status})
    return pd.DataFrame(rows)


def _ladder_once(df, production_tonnes, targets, cfg, iol_nominal, bf_nominal, ratio_hard=False, tier0_only=False):
    """One full lexicographic solve. Returns a result dict (or a bool when tier0_only)."""
    c = cfg
    OUT = OUT_KG
    df = _ensure_material_role(df)
    fixed = {k: v for k, v in (c["fixed_usage"] or {}).items() if k in df.index and k != _bfr_name(df)}
    tech_groups = tuple(c["apply_tech_limits_to"] or ())
    fuel_ash_settings = (get_active_fuel_ash_settings(df) if c["fuel_ash_settings"] is None
                         else _normalise_fuel_ash_settings(c["fuel_ash_settings"], df=df))
    prod = float(production_tonnes)
    stock_basis = c["stock_basis"]
    iron_ores = _eligible_iron_ores(df)
    fluxes = [m for m in df.index if str(df.loc[m, "Group"]).strip() == "Flux"]
    fuels = _fuel_materials(df)
    bfr, iol, ms = _bfr_name(df), _iol_name(df), _mill_scale_name(df)
    dial = min(max(float(c["inventory_weight"]), 0.0), 1.0)
    tol = _merge_tolerances(c["tolerances"], targets)
    margin = max(float(c["robust_margin"]), 0.0)
    spec = dict(targets)                                   # spec actually steered to (tighter when a margin is set)
    spec["SiO2_max"] = targets["SiO2_max"] - margin
    spec["Al2O3_max"] = targets["Al2O3_max"] - margin
    fe_lo, fe_hi = FE_LOWER + margin, FE_UPPER

    fuel_ok, fuel_problems = check_fuel_gate(df)
    if not fuel_ok:
        return {"status": "No_Production", "blend": None, "cost": None, "achieved": None,
                "diag": ["PRODUCTION IMPOSSIBLE: fuel requirement cannot be met."] + fuel_problems, "fallback": False}

    rs = resolve_return_sinter(df, prod, iol_nominal, bf_nominal, stock_basis)
    b = rs["b"]
    diagnostics = list(rs["notes"])
    iol_shortfall = rs["iol_mode"] == "shortfall"
    iron_ore_max_pct, unavailable_iron, iron_msg, iron_tier = get_iron_ore_tier(
        df, iron_ores, extra_missing=1 if iol_shortfall else 0,
        extra_reasons=["IOL_Fines"] if iol_shortfall else None)
    diagnostics.append(iron_msg)
    bounds = build_bounds(df, prod, tech_groups, stock_basis, fixed)

    moisture_factor, loi_factor = {}, {}
    for m in df.index:
        if m in fuels or m == bfr:
            continue
        mois = float(df.loc[m, "Moisture_Pct"]) / 100
        moisture_factor[m] = mois / (1 - mois) if mois < 1 else 0.0
        loi_factor[m] = float(df.loc[m, "LOI"]) / 100
    fuel_cv = {m: (float(df.loc[m, "CV_kcal_kg"]) or DEFAULT_COKE_CV_KCAL_KG) for m in fuels}
    fuel_fc = {m: (float(df.loc[m, "FC_Pct"]) or DEFAULT_COKE_FC_PCT) for m in fuels}
    ratio_sets = inventory_ratio_sets(df, iron_ores, unavailable_iron, fixed, stock_basis)

    def build(tag, tol_hard, with_goals=True):
        prob = pulp.LpProblem(f"Sinter_{tag}", pulp.LpMinimize)
        x = {m: pulp.LpVariable(f"x{tag}_{m}", lowBound=bounds[m][0], upBound=bounds[m][1]) for m in df.index}
        chem, G, F = _lp_chemistry(x, df, fuel_ash_settings, b)

        # ---- TIER 0: retained-mass balance ----
        ash_coeff = _fuel_ash_coefficients(df, fuel_ash_settings)
        non_fuel = [m for m in x if m != bfr and str(df.loc[m, "Group"]).strip() != "Fuel"]
        mass = (pulp.lpSum(x[m] * (1 - float(df.loc[m, "LOI"]) / 100) for m in non_fuel)
                + pulp.lpSum(x[m] * ash_coeff[m]["Ash"] for m in x if m in ash_coeff))
        prob += mass >= OUT - 2, "Mass_Balance_Lower"
        prob += mass <= OUT + 2, "Mass_Balance_Upper"

        # ---- TIER 0: iron ore, mill scale, flux guard ----
        total_ore = pulp.lpSum(x[m] for m in iron_ores)
        for m in iron_ores:
            if m in unavailable_iron:
                continue
            prob += x[m] <= iron_ore_max_pct.get(m, 0.8) * total_ore + 0.001, f"{m}_max_share_of_ore"
        if ms is not None and ms in x and _avail(df, ms) and ms not in fixed:
            prob += x[ms] >= MILL_SCALE_MIN_BURDEN_PCT * F, "MILL_SCALE_Burden_Min"
            prob += x[ms] <= MILL_SCALE_MAX_BURDEN_PCT * F, "MILL_SCALE_Burden_Max"
        cap_ore = MAX_IRON_ORE_PORTION_CRISIS if iron_tier == "crisis" or iol_shortfall else MAX_IRON_ORE_PORTION
        prob += total_ore <= cap_ore * F, "Max_Iron_Ore_Portion"
        prob += pulp.lpSum(x[m] for m in fluxes) <= float(c["max_flux_portion"]) * F, "Max_Flux_Portion"

        # ---- TIER 0: IOL share of the charged mix ----
        if iol is not None and iol in x and iol not in fixed and rs["iol_mode"] == "share":
            prob += x[iol] == (rs["iol_nominal"] / (1.0 - b)) * F, "IOL_share_of_charged_mix"
        elif iol is not None and iol in x and iol not in fixed and rs["iol_mode"] == "shortfall":
            prob += x[iol] == min(rs["iol_cap_kg_t"], bounds[iol][1]), "IOL_at_stock_limit"
        elif iol is not None and iol in x and iol not in fixed and rs["iol_mode"] == "off":
            prob += x[iol] == 0, "IOL_off"

        # ---- optional per-ore share guard rail ----
        if c["ore_share_cap"] and ratio_sets.get("Iron ore"):
            members = list(ratio_sets["Iron ore"].keys())
            cap_eff = min(1.0, max(float(c["ore_share_cap"]), 1.3 / len(members)))
            T = pulp.lpSum(x[m] for m in members)
            for m in members:
                prob += x[m] <= cap_eff * T, f"Ore_share_cap_{m}"

        # ---- optional HARD stock-ratio band ("hold the ratio" option) ----
        if ratio_hard and ratio_sets.get("Iron ore"):
            w_ = ratio_sets["Iron ore"]; tw_ = sum(w_.values()); T_ = pulp.lpSum(x[m] for m in w_)
            band = float(c["ratio_band_pp"]) / 100.0
            for m, wi in w_.items():
                prob += x[m] >= max(wi / tw_ - band, 0.0) * T_, f"RatioHold_lo_{m}"
                prob += x[m] <= min(wi / tw_ + band, 1.0) * T_, f"RatioHold_hi_{m}"

        # ---- TIER 0: heat balance and FeO band; FeO closeness is a later goal ----
        feo_dev = None
        cost_expr = pulp.lpSum(x[m] * float(df.loc[m, "Price_Rs_t"]) / 1000 for m in x)
        if fuels:
            total_fuel = pulp.lpSum(x[m] for m in fuels)
            prob += total_fuel >= c["coke_min_rate"], "Coke_Practical_Min"
            prob += total_fuel <= c["coke_max_rate"], "Coke_Practical_Max"
            q_fuel = pulp.lpSum(x[m] * fuel_fc[m] / 100 * fuel_cv[m] for m in fuels)
            q_moist = c["latent_heat"] * pulp.lpSum(x[m] * moisture_factor.get(m, 0.0) for m in x if m not in fuels and m != bfr)
            q_calc = c["calcination_heat"] * pulp.lpSum(x[m] * loi_factor.get(m, 0.0) for m in x if m not in fuels and m != bfr)
            q_req = (q_moist + q_calc + c["melting_heat"] * OUT) / (1 - c["loss_fraction"])
            surplus = q_fuel - q_req
            if c["manual_override"]:
                prob += total_fuel == c["manual_coke_rate"], "Manual_Coke_Override"
            else:
                prob += q_fuel >= q_req, "Heat_Balance_Min"
                if c["enforce_firing_ratio_max"]:
                    prob += q_fuel <= q_req * c["firing_ratio_max"], "Firing_Ratio_Max"
            feo_pred = c["feo_ref_pct"] + c["feo_thermal_slope"] * ((surplus - c["feo_ref_surplus"]) / 10000.0)
            prob += feo_pred >= c["feo_min"], "FeO_Min_Hard"
            prob += feo_pred <= c["feo_max"], "FeO_Max_Hard"
            if not c["manual_override"]:
                feo_dev = pulp.LpVariable(f"FeO_dev_{tag}", lowBound=0)
                prob += feo_dev >= feo_pred - c["feo_target"], "FeO_dev_pos"
                prob += feo_dev >= c["feo_target"] - feo_pred, "FeO_dev_neg"

        # ---- stock-ratio deviation (soft, as in v40) ----
        devs = []
        for gname, w in ratio_sets.items():
            tot_w = sum(w.values())
            if tot_w <= 0:
                continue
            T = pulp.lpSum(x[m] for m in w)
            for m, wi in w.items():
                d = pulp.LpVariable(f"dev_{tag}_{m}", lowBound=0)
                prob += d >= x[m] - (wi / tot_w) * T, f"dev_pos_{m}"
                prob += d >= (wi / tot_w) * T - x[m], f"dev_neg_{m}"
                devs.append(d)
        dev_expr = pulp.lpSum(devs) if devs else None

        tiers = {"T1": [], "T2": []}
        tier_d = {"T1": [], "T2": []}
        if with_goals:
            sref = float(targets["SiO2_max"])
            Fe, Si, Al, Ca, Mg = chem["Fe"], chem["SiO2"], chem["Al2O3"], chem["CaO"], chem["MgO"]
            # (label, family, tier, spec LHS<=0, tolerance LHS<=0, scale kg)
            G_ = [
                ("Fe_low", "Fe", 1, fe_lo / 100 * G - Fe, tol["Fe_min"] / 100 * G - Fe, (fe_lo - tol["Fe_min"]) / 100 * OUT),
                ("Fe_high", "Fe", 1, Fe - fe_hi / 100 * G, Fe - tol["Fe_max"] / 100 * G, (tol["Fe_max"] - fe_hi) / 100 * OUT),
                ("Bas_low", "Basicity", 1, spec["Basicity_min"] * Si - Ca, tol["Basicity_min"] * Si - Ca,
                 (spec["Basicity_min"] - tol["Basicity_min"]) * sref / 100 * OUT),
                ("Bas_high", "Basicity", 1, Ca - spec["Basicity_max"] * Si, Ca - tol["Basicity_max"] * Si,
                 (tol["Basicity_max"] - spec["Basicity_max"]) * sref / 100 * OUT),
                ("CaO_low", "CaO", 2, spec["CaO_min"] / 100 * G - Ca, tol["CaO_min"] / 100 * G - Ca,
                 (spec["CaO_min"] - tol["CaO_min"]) / 100 * OUT),
                ("CaO_high", "CaO", 2, Ca - spec["CaO_max"] / 100 * G, Ca - tol["CaO_max"] / 100 * G,
                 (tol["CaO_max"] - spec["CaO_max"]) / 100 * OUT),
                ("MgO_low", "MgO", 2, spec["MgO_min"] / 100 * G - Mg, tol["MgO_min"] / 100 * G - Mg,
                 (spec["MgO_min"] - tol["MgO_min"]) / 100 * OUT),
                ("MgO_high", "MgO", 2, Mg - spec["MgO_max"] / 100 * G, Mg - tol["MgO_max"] / 100 * G,
                 (tol["MgO_max"] - spec["MgO_max"]) / 100 * OUT),
                ("Al2O3_high", "Al2O3", 2, Al - spec["Al2O3_max"] / 100 * G, Al - tol["Al2O3_max"] / 100 * G,
                 (tol["Al2O3_max"] - spec["Al2O3_max"]) / 100 * OUT),
                ("SiO2_high", "SiO2", 2, Si - spec["SiO2_max"] / 100 * G, Si - tol["SiO2_max"] / 100 * G,
                 (tol["SiO2_max"] - spec["SiO2_max"]) / 100 * OUT),
                ("AlSi_high", "Al2O3_SiO2", 2, Al - spec["Al2O3_SiO2_max"] * Si, Al - tol["Al2O3_SiO2_max"] * Si,
                 (tol["Al2O3_SiO2_max"] - spec["Al2O3_SiO2_max"]) * sref / 100 * OUT),
            ]
            for label, fam, tier, spec_lhs, tol_lhs, scale in G_:
                d = pulp.LpVariable(f"gdev_{tag}_{label}", lowBound=0)
                prob += spec_lhs - d <= 0, f"goal_{label}"
                if tol_hard:
                    prob += tol_lhs <= 0, f"tolerance_{label}"
                tiers[f"T{tier}"].append(GOAL_WEIGHTS[fam] * d / max(scale, 0.5))
                ratio_type = label in ("Bas_low", "Bas_high", "AlSi_high")
                allow_kg = SPEC_CHECK_TOL * (sref / 100.0 * OUT if ratio_type else OUT / 100.0)
                tier_d[f"T{tier}"].append((d, allow_kg))
            if c["enforce_b4"]:
                prob += (Ca + Mg) - c["b4_min"] * (Si + Al) >= 0, "B4_min"
                prob += (Ca + Mg) - c["b4_max"] * (Si + Al) <= 0, "B4_max"
        t_exprs = [(k, pulp.lpSum(v), tier_d[k]) for k, v in tiers.items() if v]
        return prob, x, cost_expr, feo_dev, dev_expr, t_exprs, chem

    def ladder(prob, cost_expr, feo_dev, dev_expr, t_exprs, chem=None):
        vals = {}
        # Optional furnace-value credit (Rs per t sinter per kg/t of element).  It only changes what the LAST step minimises,
        # after every goal tier, the FeO band and the stock-ratio pin are fixed; with no credit this IS cost_expr.
        final_obj = cost_expr
        credit = {k: float(v) for k, v in (c.get("chem_credit") or {}).items() if chem is not None and k in chem and v}
        if credit:
            final_obj = cost_expr - pulp.lpSum(v * chem[k] for k, v in credit.items())
        prob.sense = pulp.LpMinimize
        for k, expr, dlist in t_exprs:                              # goal tiers 1, 2
            prob.setObjective(expr); prob.solve(_CBC())
            if pulp.LpStatus[prob.status] != "Optimal":
                return pulp.LpStatus[prob.status], vals
            v = max(0.0, float(pulp.value(expr) or 0.0)); vals[k] = v
            for i_, (dv_, allow_) in enumerate(dlist):              # pin every goal on its own (v40 style)
                prob += dv_ <= max(float(dv_.value() or 0.0), 0.0) + allow_, f"Pin_{k}_{i_}"
        if feo_dev is not None:                                     # FeO closeness
            prob.setObjective(feo_dev); prob.solve(_CBC())
            if pulp.LpStatus[prob.status] != "Optimal":
                return pulp.LpStatus[prob.status], vals
            vals["FeO"] = max(0.0, float(pulp.value(feo_dev) or 0.0))
            prob += feo_dev <= vals["FeO"] + c["feo_pin_tol"], "FeO_Pin"
        vals["Ratio"] = 0.0
        if dev_expr is not None and dial > 1e-9 and not ratio_hard:  # stock ratio (dial)
            prob.setObjective(dev_expr); prob.solve(_CBC())
            if pulp.LpStatus[prob.status] != "Optimal":
                return pulp.LpStatus[prob.status], vals
            dev_min = float(pulp.value(dev_expr) or 0.0)
            allowed = dev_min + INVENTORY_DEV_TOL_KG
            if dial < 0.999:
                prob.setObjective(final_obj); prob.solve(_CBC())
                if pulp.LpStatus[prob.status] != "Optimal":
                    return pulp.LpStatus[prob.status], vals
                dev_cost = float(pulp.value(dev_expr) or 0.0)
                allowed = dev_min + (1.0 - dial) * max(dev_cost - dev_min, 0.0) + INVENTORY_DEV_TOL_KG
            vals["Ratio"] = dev_min
            prob += dev_expr <= allowed, "Inventory_Dev_Pin"
        prob.setObjective(final_obj); prob.solve(_CBC())            # cost last
        st = pulp.LpStatus[prob.status]
        if st == "Optimal":
            vals["Cost"] = float(pulp.value(cost_expr))
        return st, vals

    if tier0_only:
        prob, x, cost_expr, *_ = build("T0", False, with_goals=False)
        prob.setObjective(cost_expr); prob.solve(_CBC())
        return pulp.LpStatus[prob.status] == "Optimal"

    def finalize(prob, x, attempt, vals):
        blend = {m: round(max(float(x[m].value() or 0.0), 0.0), 2) for m in x}
        cost = sum(blend[m] * float(df.loc[m, "Price_Rs_t"]) / 1000 for m in blend)
        achieved = compute_achieved(blend, df, OUT, fuel_ash_settings, bf_nominal=b)
        gaps = _spec_gaps(achieved, targets)
        if max(gaps.values()) <= SPEC_CHECK_TOL:
            status = "Optimal"
        else:
            status = "Relaxed" if attempt == 1 else "Production_Risk"
        diag = list(diagnostics)
        cm = charged_mix_summary(blend, df, b)
        diag.append(f"Charged mix: fresh burden {cm['Fresh_Burden_kg_t']:.1f} kg/t + BFR poured {cm['BFR_Poured_kg_t']:.1f} kg/t "
                    f"(BFR {cm['BFR_Pct_of_Mix']:.1f} % of mix, not in burden, Rs 0) = {cm['Charged_Mix_kg_t']:.1f} kg/t; "
                    f"IOL {cm['IOL_Pct_of_Mix']:.1f} % of mix.")
        heat = compute_coke_heat_balance_diagnostic(blend, df, OUT, c["latent_heat"], c["calcination_heat"], c["melting_heat"],
                                                    c["loss_fraction"], c["feo_min"], c["feo_target"], c["feo_max"], c["feo_ref_surplus"],
                                                    c["feo_ref_pct"], c["feo_thermal_slope"], c["ref_coke_cv"], c["ref_coke_fc"])
        if heat:
            diag.append(f"Total fuel {heat['Total_Fuel_kg']:.1f} kg/t | Predicted FeO {heat['FeO_Estimate_Pct']:.2f}% "
                        f"(target {c['feo_target']:.2f}%, band {c['feo_min']:.2f}-{c['feo_max']:.2f}%).")
        return {"status": status, "blend": blend, "cost": cost, "achieved": achieved, "diag": diag,
                "fallback": attempt == 2, "vals": vals, "attempt": attempt, "b": b, "tol": tol,
                "iol_nominal": iol_nominal, "bf_nominal": bf_nominal}

    # ---- attempt 1: approved tolerance is a hard wall ----
    p1, x1, cost1, feo1, dev1, te1, chem1 = build("A", True)
    st, vals = ladder(p1, cost1, feo1, dev1, te1, chem1)
    if st == "Optimal":
        return finalize(p1, x1, 1, vals)
    # ---- attempt 2: no wall - closest possible recipe (Production_Risk) ----
    p2, x2, cost2, feo2, dev2, te2, chem2 = build("B", False)
    st, vals = ladder(p2, cost2, feo2, dev2, te2, chem2)
    if st == "Optimal":
        r = finalize(p2, x2, 2, vals)
        r["diag"].append("No recipe stays inside the approved tolerances - closest achievable recipe shown (Production_Risk).")
        return r
    diagnostics.append("Hard limits (stock, mass balance, Mill Scale, fixed recycle, coke / heat balance, FeO band) cannot all be met.")
    return {"status": "Production_Risk", "blend": None, "cost": None, "achieved": None, "diag": diagnostics, "fallback": True}


def _max_producible_tonnes(df, production_tonnes, targets, cfg, iol_nominal, bf_nominal):
    """Largest production (t) for which the hard limits can be met - bisection on the stock caps."""
    lo, hi = 0.0, float(production_tonnes)
    if not _ladder_once(df, max(hi * 0.02, 1.0), targets, cfg, iol_nominal, bf_nominal, tier0_only=True):
        return 0.0
    for _ in range(12):
        mid = (lo + hi) / 2.0
        if mid > 0 and _ladder_once(df, mid, targets, cfg, iol_nominal, bf_nominal, tier0_only=True):
            lo = mid
        else:
            hi = mid
    return lo


def _ore_share_table(blend, df, prod, horizon_days, stock_basis, fixed):
    iron_ores = _eligible_iron_ores(df)
    unavailable = [m for m in iron_ores if not _avail(df, m)]
    sets = inventory_ratio_sets(df, iron_ores, unavailable, fixed, stock_basis)
    w = sets.get("Iron ore")
    if not w or not blend:
        return pd.DataFrame()
    tw = sum(w.values()); tx = sum(blend.get(m, 0.0) for m in w) or 1.0
    rows = []
    for m, wi in w.items():
        used_t = blend.get(m, 0.0) * prod / 1000.0
        cover = (_dry_stock_t(df, m, stock_basis) / used_t * float(horizon_days)) if used_t > 1e-9 else np.inf
        rows.append({"Ore": m, "Stock share %": wi / tw * 100.0, "Recipe share %": blend.get(m, 0.0) / tx * 100.0,
                     "Drift pp": blend.get(m, 0.0) / tx * 100.0 - wi / tw * 100.0, "kg/t": blend.get(m, 0.0),
                     "Days of cover": cover})
    return pd.DataFrame(rows)


def solve_blend_with_compensation(df, production_tonnes, targets, baseline_blend=None,
                                  enforce_b4=False, b4_min=1.8, b4_max=2.0,
                                  iol_nominal=IOL_FINES_NOMINAL_PCT, bf_nominal=BF_RETURNS_NOMINAL_PCT,
                                  latent_heat=DEFAULT_HEAT_LATENT_MOISTURE,
                                  calcination_heat=DEFAULT_HEAT_CALCINATION_PER_LOI_KG,
                                  melting_heat=DEFAULT_HEAT_MELTING_PER_KG_SINTER,
                                  loss_fraction=DEFAULT_HEAT_LOSS_FRACTION,
                                  firing_ratio_max=DEFAULT_FIRING_RATIO_MAX,
                                  enforce_firing_ratio_max=ENFORCE_FIRING_RATIO_MAX,
                                  coke_min_rate=DEFAULT_COKE_MIN_KG_T, coke_max_rate=DEFAULT_COKE_MAX_KG_T,
                                  feo_min=DEFAULT_FEO_MIN_PCT, feo_target=DEFAULT_FEO_TARGET_PCT,
                                  feo_max=DEFAULT_FEO_MAX_PCT,
                                  feo_ref_surplus=DEFAULT_FEO_REFERENCE_THERMAL_SURPLUS_KCAL,
                                  feo_ref_pct=DEFAULT_FEO_REFERENCE_PCT,
                                  feo_thermal_slope=DEFAULT_FEO_THERMAL_SLOPE_PCT_PER_10K_KCAL,
                                  ref_coke_cv=DEFAULT_REFERENCE_COKE_CV_KCAL_KG,
                                  ref_coke_fc=DEFAULT_REFERENCE_COKE_FC_PCT,
                                  manual_override=False, manual_coke_rate=65.0, fuel_ash_settings=None,
                                  inventory_weight=DEFAULT_INVENTORY_WEIGHT,
                                  apply_tech_limits_to=DEFAULT_TECH_LIMIT_GROUPS,
                                  stock_basis=DEFAULT_STOCK_BASIS, max_flux_portion=MAX_FLUX_PORTION,
                                  ore_share_cap=None, fixed_usage=None,
                                  tolerances=None, float_returns=False, compare_ratio_hold=True,
                                  ratio_band_pp=RATIO_HOLD_BAND_PP, robust_margin=0.0,
                                  horizon_days=DEFAULT_HORIZON_DAYS, feo_pin_tol=None, chem_credit=None, **_ignored):
    """Returns (status, blend, cost, achieved, diagnostics, is_fallback).
    status: Optimal (spec met) | Relaxed (inside approved tolerance) | Production_Risk (outside tolerance,
    closest recipe shown, or hard limits infeasible with blend None) | No_Production.
    Extra detail (goal table, ratio options, messages) is in get_last_run_report()."""
    cfg = dict(enforce_b4=enforce_b4, b4_min=b4_min, b4_max=b4_max, latent_heat=latent_heat,
               calcination_heat=calcination_heat, melting_heat=melting_heat, loss_fraction=loss_fraction,
               firing_ratio_max=firing_ratio_max, enforce_firing_ratio_max=enforce_firing_ratio_max,
               coke_min_rate=coke_min_rate, coke_max_rate=coke_max_rate, feo_min=feo_min, feo_target=feo_target,
               feo_max=feo_max, feo_ref_surplus=feo_ref_surplus, feo_ref_pct=feo_ref_pct,
               feo_thermal_slope=feo_thermal_slope, ref_coke_cv=ref_coke_cv, ref_coke_fc=ref_coke_fc,
               manual_override=manual_override, manual_coke_rate=manual_coke_rate, fuel_ash_settings=fuel_ash_settings,
               inventory_weight=inventory_weight, apply_tech_limits_to=apply_tech_limits_to, stock_basis=stock_basis,
               max_flux_portion=max_flux_portion, ore_share_cap=ore_share_cap, fixed_usage=fixed_usage,
               tolerances=tolerances, ratio_band_pp=ratio_band_pp, robust_margin=robust_margin,
               feo_pin_tol=(FEO_PIN_TOL_PCT if feo_pin_tol is None else float(feo_pin_tol)),
               chem_credit=chem_credit)
    df = _ensure_material_role(df)
    prod = float(production_tonnes)
    LAST_RUN.clear()

    # candidate return-sinter settings (the user's own values are always a candidate)
    combos = [(iol_nominal, bf_nominal)]
    if float_returns:
        for io in (IOL_MIN_PCT, (IOL_MIN_PCT + IOL_MAX_PCT) / 2.0, IOL_MAX_PCT):
            for bf in (BFR_MIN_PCT, (BFR_MIN_PCT + BFR_MAX_PCT) / 2.0, BFR_MAX_PCT):
                if io + bf <= RS_TOTAL_MAX_PCT + 1e-9:
                    combos.append((io / 100.0, bf / 100.0))

    def rank(r):
        v = r.get("vals", {})
        return (r["attempt"], round(v.get("T1", 0.0), 3), round(v.get("T2", 0.0), 3), round(v.get("FeO", 0.0), 3),
                round(v.get("Ratio", 0.0), 1), round(r["cost"], 1))

    results = []
    first = None
    for io, bf in combos:
        r = _ladder_once(df, prod, targets, cfg, io, bf)
        if first is None:
            first = r
        if r.get("blend") is not None:
            results.append(r)
    if not results:
        diag = list(first["diag"])
        if first["status"] == "No_Production":
            return "No_Production", None, None, None, diag, False
        _ms = _mill_scale_name(df)
        if not [m for m in _eligible_iron_ores(df) if m != _ms and _avail(df, m)]:
            diag.append("SHORTAGE: no iron ore other than Mill Scale is in stock.")
            LAST_RUN.update({"messages": diag})
            return "Production_Risk", None, None, None, diag, True
        cap_t = _max_producible_tonnes(df, prod, targets, cfg, iol_nominal, bf_nominal)
        if cap_t > 0:
            diag.append(f"SHORTAGE: the stock supports about {cap_t:,.0f} t of sinter over this horizon (asked {prod:,.0f} t).")
        else:
            diag.append("SHORTAGE: even a very small production cannot meet the hard limits - check stock, coke and heat-balance inputs.")
        LAST_RUN.update({"messages": diag})
        return "Production_Risk", None, None, None, diag, True

    best = min(results, key=rank)
    diag = list(best["diag"])
    if float_returns and len(results) > 1:
        diag.append(f"Return sinter chosen by the model: IOL {best['iol_nominal']*100:.1f} % + BFR {best['bf_nominal']*100:.1f} % of the charged mix.")
    blend, achieved = best["blend"], best["achieved"]
    goal_tbl = _goal_table(achieved, targets, best["tol"])
    messages = []
    for _, row in goal_tbl.iterrows():
        if not str(row["Status"]).startswith("Met"):
            messages.append(f"{row['Goal']}: {row['Achieved']:.3f} vs spec {row['Spec']} (outside by {row['Outside spec by']:.3f}); "
                            f"approved tolerance {row['Approved tolerance']} - {row['Status']}.")
    fixed = {k: v for k, v in (fixed_usage or {}).items()}
    share = _ore_share_table(blend, df, prod, horizon_days, stock_basis, fixed)
    options = pd.DataFrame()
    if not share.empty:
        worst = share.loc[share["Drift pp"].abs().idxmax()]
        fastest = share.loc[share["Days of cover"].idxmin()]
        if abs(worst["Drift pp"]) > RATIO_DRIFT_WARN_PP:
            messages.append(f"STOCK RATIO: {worst['Ore']} takes {worst['Recipe share %']:.1f} % of the ore against a stock share of "
                            f"{worst['Stock share %']:.1f} % ({worst['Drift pp']:+.1f} pp).")
            if compare_ratio_hold and inventory_weight > 0:
                rh = _ladder_once(df, prod, targets, cfg, best["iol_nominal"], best["bf_nominal"], ratio_hard=True)
                rows = [{"Option": "A - quality first (this recipe)", "Status": best["status"], "Cost Rs/t": best["cost"],
                         "SiO2 %": achieved["SiO2"], "Fe %": achieved["Fe"], "Basicity": achieved["Basicity"],
                         "Max ore drift pp": float(share["Drift pp"].abs().max())}]
                if rh.get("blend") is not None:
                    sh2 = _ore_share_table(rh["blend"], df, prod, horizon_days, stock_basis, fixed)
                    rows.append({"Option": f"B - hold every ore within +/-{ratio_band_pp:.0f} pp of stock share", "Status": rh["status"],
                                 "Cost Rs/t": rh["cost"], "SiO2 %": rh["achieved"]["SiO2"], "Fe %": rh["achieved"]["Fe"],
                                 "Basicity": rh["achieved"]["Basicity"],
                                 "Max ore drift pp": float(sh2["Drift pp"].abs().max()) if not sh2.empty else np.nan})
                    missed = [f"{k} would be {rh['achieved'][k]:.2f} (outside spec by {v:.2f})" for k, v in _spec_gaps(rh["achieved"], targets).items() if v > SPEC_CHECK_TOL]
                    dc = rh["cost"] - best["cost"]
                    if rh["status"] == "Optimal":
                        messages.append(f"Holding every ore within +/-{ratio_band_pp:.0f} pp of its stock share IS possible with all quality "
                                        f"targets met (cost Rs {dc:+,.0f}/t).")
                    elif rh["status"] == "Relaxed":
                        messages.append(f"Holding the ratio within +/-{ratio_band_pp:.0f} pp is possible inside the approved tolerance "
                                        f"(cost Rs {dc:+,.0f}/t) but leaves: {', '.join(missed)}.")
                    else:
                        messages.append(f"Holding the ratio within +/-{ratio_band_pp:.0f} pp is NOT possible inside the approved tolerances - "
                                        f"it would leave: {', '.join(missed)}. Its cost is not comparable.")
                else:
                    rows.append({"Option": f"B - hold every ore within +/-{ratio_band_pp:.0f} pp of stock share", "Status": "Not possible",
                                 "Cost Rs/t": np.nan, "SiO2 %": np.nan, "Fe %": np.nan, "Basicity": np.nan, "Max ore drift pp": np.nan})
                    messages.append(f"Holding the ratio within +/-{ratio_band_pp:.0f} pp is not possible with the current stock and hard limits.")
                options = pd.DataFrame(rows)
        if np.isfinite(fastest["Days of cover"]):
            messages.append(f"Fastest-drawn ore: {fastest['Ore']} - about {fastest['Days of cover']:.1f} days of cover at this recipe "
                            f"(horizon {float(horizon_days):.0f} days).")
    for mmsg in messages:
        diag.append(mmsg)
    LAST_RUN.update({"goal_table": goal_tbl, "ratio_options": options, "ore_shares": share, "messages": messages,
                     "tier_values": best.get("vals", {}), "returns_used": {"iol": best["iol_nominal"], "bfr": best["bf_nominal"]}})
    return best["status"], blend, best["cost"], achieved, diag, best["fallback"]


# ----------------------------------------------------------------------------
# PRODUCTIVITY
# ----------------------------------------------------------------------------
def calculate_productivity_loss_from_fines(total_fines_pct):
    fines = float(total_fines_pct)
    if not np.isfinite(fines):
        raise ValueError("Total fines percentage must be finite.")
    pts = sorted(FINES_PRODUCTIVITY_LOSS_TABLE.items())
    if fines <= pts[0][0]:
        return pts[0][1]
    if fines >= pts[-1][0]:
        return pts[-1][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= fines <= x2:
            return y1 + (fines - x1) * (y2 - y1) / (x2 - x1)
    return pts[-1][1]


def calculate_productivity_metrics(total_dry_burden_kg_t, feed_rate_t_h, productivity_target=None,
                                   total_fines_pct=None, charged_mix_kg_t=None, gross_sinter_kg_t=OUT_KG,
                                   strand_area_m2=DEFAULT_STRAND_AREA_M2):
    """Productivity responds to the recipe: feed rate is the CHARGED MIX (t/h, dry basis).
       yield on mix = net sinter / charged mix ; net sinter t/h = feed x 1000 / charged mix
       productivity (t/m2/h) = net sinter t/h / strand area, reduced by the fines loss %."""
    burden = float(total_dry_burden_kg_t); feed = float(feed_rate_t_h)
    mix = float(charged_mix_kg_t) if charged_mix_kg_t else burden
    area = float(strand_area_m2)
    if not np.isfinite(burden) or burden <= 0:
        raise ValueError("Total dry burden must be greater than zero.")
    if not np.isfinite(feed) or feed <= 0:
        raise ValueError("Feed rate must be greater than zero.")
    if not np.isfinite(area) or area <= 0:
        raise ValueError("Strand area must be greater than zero.")
    net = feed * OUT_KG / mix
    gross = feed * float(gross_sinter_kg_t) / mix
    base = net / area
    loss = calculate_productivity_loss_from_fines(total_fines_pct) if total_fines_pct is not None else 0.0
    final = base * (1.0 - loss / 100.0)
    res = {"Total_Dry_Burden_kg_t": burden, "Charged_Mix_kg_t": mix, "Sinter_Yield": OUT_KG / mix,
           "Sinter_Yield_Pct": OUT_KG / mix * 100.0, "Feed_Rate_t_h": feed, "Strand_Area_m2": area,
           "Gross_Sinter_t_h": gross, "Net_Sinter_t_h": net, "Base_Productivity": base,
           "Productivity_Loss_Pct": loss, "Final_Productivity": final}
    if total_fines_pct is not None:
        res["Total_Fines_Pct"] = float(total_fines_pct)
    if productivity_target is not None:
        tgt = float(productivity_target)
        if not np.isfinite(tgt) or tgt <= 0:
            raise ValueError("Productivity target must be greater than zero.")
        res.update({"Productivity_Target": tgt, "Achievement_Pct": final / tgt * 100.0,
                    "Target_Achieved": final >= tgt})
    return res


# ----------------------------------------------------------------------------
# EXCEL MASTER LOADER
# ----------------------------------------------------------------------------
MASTER_REQUIRED_COLUMNS = ["Material", "Group", "Fe_%", "SiO2_%", "Al2O3_%", "CaO_%", "MgO_%", "LOI_%",
                           "Moisture_%", "Available_Stock_t", "Price_Rs_t"]
MASTER_OPTIONAL_COLUMNS = ["Material_Role", "Tech_Min_kg_t", "Tech_Max_kg_t", "Fines_%", "CV_kcal_kg", "FC_Pct"]
MASTER_COLUMN_ALIASES = {
    "Material": ["Material", "Material_Name", "Material Name"],
    "Group": ["Group", "Material_Group", "Material Type", "Material_Type"],
    "Material_Role": ["Material_Role", "Material Role", "Role"],
    "Fe_%": ["Fe_%", "Fe", "Fe %", "Fe_Pct", "Fe_Pct_%"],
    "SiO2_%": ["SiO2_%", "SiO2", "SiO2 %", "SiO2_Pct", "SiO\u2082", "SiO\u2082 %"],
    "Al2O3_%": ["Al2O3_%", "Al2O3", "Al2O3 %", "Al2O3_Pct", "Al\u2082O\u2083", "Al\u2082O\u2083 %"],
    "CaO_%": ["CaO_%", "CaO", "CaO %", "CaO_Pct"],
    "MgO_%": ["MgO_%", "MgO", "MgO %", "MgO_Pct"],
    "LOI_%": ["LOI_%", "LOI", "LOI %", "LOI_Pct"],
    "Moisture_%": ["Moisture_%", "Moisture_Pct", "Moisture", "Moisture %", "Moisture_Pct_%"],
    "Tech_Min_kg_t": ["Tech_Min_kg_t", "Tech_Min", "Tech Min", "Technical_Min", "Technical_Min_kg_t", "Min_kg_t"],
    "Tech_Max_kg_t": ["Tech_Max_kg_t", "Tech_Max", "Tech Max", "Technical_Max", "Technical_Max_kg_t", "Max_kg_t"],
    "Available_Stock_t": ["Available_Stock_t", "Available_Tonnes", "Available Stock", "Stock_t", "Stock", "Availability_t"],
    "Price_Rs_t": ["Price_Rs_t", "Price", "Price Rs/t", "Price_Rs_per_t", "Cost_Rs_t"],
    "Fines_%": ["Fines_%", "Fines", "% Fines", "Fines %", "Fines_Pct", "Fines_Pct_%"],
    "CV_kcal_kg": ["CV_kcal_kg", "CV", "Calorific_Value", "CV (kcal/kg)"],
    "FC_Pct": ["FC_Pct", "FC", "Fixed_Carbon", "Fixed Carbon %", "FC %"],
}


def _normalize_excel_header(value):
    import unicodedata
    s = unicodedata.normalize("NFKC", str(value)).strip()
    s = s.replace("\u2082", "2").replace("\u2083", "3").replace("\u2084", "4").replace("\u2080", "0").replace("\u2081", "1")
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _resolve_master_columns(columns):
    normalized = {}
    for c in columns:
        normalized.setdefault(_normalize_excel_header(c), []).append(c)
    mapping, missing, ambiguous = {}, [], []
    for canonical, aliases in MASTER_COLUMN_ALIASES.items():
        selected = None
        for alias in aliases:
            matches = normalized.get(_normalize_excel_header(alias), [])
            if matches:
                if len(matches) > 1:
                    ambiguous.append(f"{canonical}: multiple identical headers {matches}")
                else:
                    selected = matches[0]
                break
        if selected is None:
            if canonical in MASTER_REQUIRED_COLUMNS:
                missing.append(canonical)
        else:
            mapping[canonical] = selected
    if missing or ambiguous:
        return None, missing, ambiguous
    return mapping, [], []


def _find_matching_master_sheet(uploaded_bytes):
    sheets = pd.read_excel(BytesIO(uploaded_bytes), sheet_name=None)
    candidates = []
    for name, frame in sheets.items():
        mapping, missing, ambiguous = _resolve_master_columns(frame.columns)
        if mapping is not None:
            candidates.append((name, frame, mapping))
    if not candidates:
        details = []
        for name, frame in sheets.items():
            _, missing, _ = _resolve_master_columns(frame.columns)
            details.append(f"'{name}': missing {missing}")
        raise ValueError("No worksheet contains the required master columns (matched by header, not by sheet name).\n"
                         + "\n".join(details))
    if len(candidates) > 1:
        raise ValueError("Multiple worksheets match the master columns: "
                         + ", ".join(repr(c[0]) for c in candidates) + ". Keep only one input table.")
    return candidates[0]


def load_master_chemistry_excel(uploaded_file):
    """uploaded_file: dict {filename: bytes}. Tech Min/Max, Fines_%, CV, FC, Role are optional."""
    if not uploaded_file:
        raise ValueError("No Excel file was uploaded.")
    file_name = next(iter(uploaded_file))
    sheet_name, raw, cmap = _find_matching_master_sheet(uploaded_file[file_name])
    raw = raw.rename(columns={actual: canon for canon, actual in cmap.items()}).copy()
    for col in MASTER_OPTIONAL_COLUMNS:
        if col not in raw.columns:
            raw[col] = 0.0 if col != "Material_Role" else ""
    raw = raw[MASTER_REQUIRED_COLUMNS + MASTER_OPTIONAL_COLUMNS].copy()
    raw["Material"] = raw["Material"].astype(str).str.strip()
    raw = raw[raw["Material"].ne("") & raw["Material"].ne("nan")].copy()
    for c in [c for c in raw.columns if c not in ("Material", "Group", "Material_Role")]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce").fillna(0.0)
    raw["Group"] = raw["Group"].astype(str).str.strip()
    raw["Material_Role"] = raw["Material_Role"].astype(str).str.strip().replace({"nan": ""})
    if (raw["Fines_%"] < 0).any() or (raw["Fines_%"] > 100).any():
        bad = raw.loc[(raw["Fines_%"] < 0) | (raw["Fines_%"] > 100), "Material"].tolist()
        raise ValueError(f"Fines % must be between 0 and 100 for: {bad}")
    if raw["Material"].duplicated().any():
        raise ValueError(f"Duplicate Material names in master: {sorted(raw.loc[raw['Material'].duplicated(keep=False), 'Material'].unique())}")
    for c in ["Fe_%", "SiO2_%", "Al2O3_%", "CaO_%", "MgO_%", "LOI_%", "Moisture_%", "Available_Stock_t", "Price_Rs_t"]:
        if (raw[c] < 0).any():
            raise ValueError(f"Negative values found in column {c}.")
    if (raw["Moisture_%"] >= 100).any():
        raise ValueError("Moisture % must be below 100.")
    mask = raw["Group"].eq("Recycle")
    raw.loc[mask, "Tech_Max_kg_t"] = raw.loc[mask, "Tech_Min_kg_t"]          # recycle = fixed rate
    raw["Available_Tonnes"] = raw["Available_Stock_t"]
    raw["Tech_Min"] = raw["Tech_Min_kg_t"]; raw["Tech_Max"] = raw["Tech_Max_kg_t"]
    raw["Moisture_Pct"] = raw["Moisture_%"]
    raw["Fe"] = raw["Fe_%"]; raw["SiO2"] = raw["SiO2_%"]; raw["Al2O3"] = raw["Al2O3_%"]
    raw["CaO"] = raw["CaO_%"]; raw["MgO"] = raw["MgO_%"]; raw["LOI"] = raw["LOI_%"]
    raw["Fines_Pct"] = raw["Fines_%"]
    keep = ["Group", "Material_Role", "Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Tech_Min", "Tech_Max",
            "Available_Tonnes", "Price_Rs_t", "Moisture_Pct", "Fines_Pct", "CV_kcal_kg", "FC_Pct"]
    df = raw.set_index("Material")[keep]
    blank_role = df["Material_Role"].eq("")
    if blank_role.any():
        df = df.drop(columns=["Material_Role"])
        df = _ensure_material_role(df)
        df = df[keep]
    return _ensure_material_role(df)


# ----------------------------------------------------------------------------
# DASHBOARD HELPERS
# ----------------------------------------------------------------------------
def quality_table(achieved, targets, fe_lo=FE_LOWER, fe_hi=FE_UPPER):
    a = achieved
    rows = [
        ("Fe", a.get("Fe", 0.0), f"{fe_lo:.1f}-{fe_hi:.1f}", fe_lo <= a.get("Fe", 0.0) <= fe_hi),
        ("SiO2", a.get("SiO2", 0.0), f"<= {targets['SiO2_max']}", a.get("SiO2", 0.0) <= targets["SiO2_max"]),
        ("Al2O3", a.get("Al2O3", 0.0), f"<= {targets['Al2O3_max']}", a.get("Al2O3", 0.0) <= targets["Al2O3_max"]),
        ("Al2O3/SiO2", a.get("Al2O3/SiO2", 0.0), f"<= {targets['Al2O3_SiO2_max']}", a.get("Al2O3/SiO2", 0.0) <= targets["Al2O3_SiO2_max"]),
        ("Basicity", a.get("Basicity", 0.0), f"{targets['Basicity_min']}-{targets['Basicity_max']}", targets["Basicity_min"] <= a.get("Basicity", 0.0) <= targets["Basicity_max"]),
        ("MgO", a.get("MgO", 0.0), f"{targets['MgO_min']}-{targets['MgO_max']}", targets["MgO_min"] <= a.get("MgO", 0.0) <= targets["MgO_max"]),
        ("CaO", a.get("CaO", 0.0), f"{targets['CaO_min']}-{targets['CaO_max']}", targets["CaO_min"] <= a.get("CaO", 0.0) <= targets["CaO_max"]),
        ("B4", a.get("B4", 0.0), "1.8-2.2 (info)", True),
    ]
    return pd.DataFrame([{"KPI": k, "Achieved": v, "Target": t, "Status": "OK" if ok else "OUT OF RANGE"}
                         for k, v, t, ok in rows])


def solve_manual_scenario(df, production_tonnes, targets, baseline_blend, fixed, **kwargs):
    """Manual Burden Control: pin the user-changed materials at their kg/t and re-optimise the rest."""
    scenario_df = _ensure_material_role(df).copy(deep=True)
    for col in ["Tech_Min", "Tech_Max", "Available_Tonnes"]:
        scenario_df[col] = pd.to_numeric(scenario_df[col], errors="coerce").astype(float)
    if not scenario_df.index.is_unique:
        raise ValueError("Manual Burden Control requires unique Material names.")
    bfr, iol = _bfr_name(scenario_df), _iol_name(scenario_df)
    pinned = {}
    for m, qty in fixed.items():
        if m not in scenario_df.index or m in (bfr, iol):
            continue                                   # BFR is chemistry-only; IOL follows the R/S %
        qty = max(0.0, float(qty))
        pinned[m] = qty
        needed = qty / 1000.0 * float(production_tonnes)
        if float(scenario_df.at[m, "Available_Tonnes"]) < needed:
            scenario_df.at[m, "Available_Tonnes"] = needed * 1.05 + 1.0
    kwargs = dict(kwargs)
    kwargs["fixed_usage"] = pinned
    return solve_blend_with_compensation(scenario_df, production_tonnes, targets,
                                         baseline_blend=baseline_blend, **kwargs)


def what_if_analysis(df, targets, production_tonnes=DEFAULT_PLANNING_TONNES, **kwargs):
    """Knock out each material (stock = 0) one at a time and re-solve."""
    df = _ensure_material_role(df)
    bfr, iol = _bfr_name(df), _iol_name(df)
    cands = [m for m in df.index if _avail(df, m) and (
        str(df.loc[m, "Group"]).strip() in ("Iron_ore", "Flux", "Fuel", "IOL_Fines_Mandate", "BF_Returns_Mandate")
        or m in (bfr, iol))]
    kwargs = {k: v for k, v in kwargs.items() if k != "fixed_usage"}
    kwargs["compare_ratio_hold"] = False
    rows = []
    for mat in cands:
        sc = df.copy()
        sc.loc[mat, "Available_Tonnes"] = 0.0
        status, blend, cost, ach, diag, fb = solve_blend_with_compensation(sc, production_tonnes, targets, **kwargs)
        if status == "No_Production":
            rows.append({"Missing Material": mat, "Status": "NO PRODUCTION", "Cost \u20b9/t": np.nan})
        elif blend is not None:
            label = {"Optimal": "Feasible", "Relaxed": "Quality relaxed"}.get(status, status)
            rows.append({"Missing Material": mat, "Status": label, "Cost \u20b9/t": round(cost, 2) if cost is not None else np.nan})
        else:
            rows.append({"Missing Material": mat, "Status": "INFEASIBLE", "Cost \u20b9/t": np.nan})
    return pd.DataFrame(rows)
