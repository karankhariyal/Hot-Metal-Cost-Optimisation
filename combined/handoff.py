"""
Glue between the two dashboards (their own session state) and the combined model.

What moves between the models (from the project README):
  sinter -> furnace : Price_Rs_t = dry raw-material cost + O&M, and Fe, CaO, SiO2, Al2O3, MgO from the sinter result,
                      written to the furnace table's switched-on Sinter row; moisture, S and Mn stay from the MBF Excel;
                      no stock cap (made on demand) unless the plant cannot make what is asked.
  furnace -> sinter : only the sinter tonnage needed (kg/tHM x plan tHM / 1000).
Sinter and MBF materials, stock and stock checks are never mixed, even when names match (KIOM, Limestone, Dolomite).
"""
import dataclasses
import datetime as dt
import hashlib

import numpy as np
import pandas as pd
import streamlit as st

from shared import nsrun
from combined import loop as L
from sinter import optimizer as sopt
from mbf import analytics as an

MODEL, TYPED = "From sinter model", "Typed row (original behaviour)"
SINTER_CHEM = ["Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Moisture_Pct"]


def boot():
    """Initialise both dashboards' state (defaults, built-in tables) without drawing anything."""
    return nsrun.load_app("sinter", render=False), nsrun.load_app("mbf", render=False)


# ------------------------------------------------------------------------------------------ reading the inputs
def sinter_inputs(sns):
    """The sinter dashboard's current inputs, built by its own helper functions (so they can never drift apart)."""
    df = sns["active_df"]()
    kw = sns["_solver_kwargs"]()
    om = float(st.session_state["sinter__om_cost"])
    return df, kw, sns["TARGETS"], om


def sinter_setup_problems(sns):
    try:
        return list(sns["setup_missing_items"]())
    except Exception:
        return []


def _canon(x):
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple)):
        return [_canon(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return round(float(x), 9)
    return x


def fp_sinter(df, kw, targets, om):
    h = hashlib.sha1()
    h.update(pd.util.hash_pandas_object(df.reset_index(), index=False).values.tobytes())
    h.update(repr(_canon(kw)).encode())
    h.update(repr(_canon(targets)).encode())
    h.update(repr(round(float(om), 6)).encode())
    return h.hexdigest()[:12]


def fp_mbf(df, cfg):
    """Fingerprint of the furnace inputs.  The switched-on Sinter row's handed-over values and the plan are left out: every
    combined run overwrites them (sinter result, combined plan), so syncing them into the MBF page must not look like an edit."""
    from mbf import optimiser as mopt
    d = mopt.ensure_columns(df).copy()
    row = L.find_sinter_row(d)
    if row is not None:
        for c in L.ROW_COLS + ["RM_Stock"]:
            d[c] = d[c].astype(float)
            d.loc[row, c] = 0.0
    cd = dataclasses.asdict(cfg)
    cd.pop("stock_plan_hm_tonnes", None)
    h = hashlib.sha1()
    h.update(pd.util.hash_pandas_object(d.reset_index(), index=False).values.tobytes())
    h.update(repr(_canon(cd)).encode())
    return h.hexdigest()[:12]


# ------------------------------------------------------------------------------------------ the standalone sinter run
def track_sinter_run(sns):
    """Called after a sinter page has run: when the sinter dashboard just made a new run, stamp it with the time and a
    fingerprint of the inputs behind it, so later changes can be flagged as stale."""
    runs = st.session_state.get("sinter__runs", 0)
    trk = st.session_state.get("_sinter_track")
    if runs and (trk is None or trk["runs"] != runs):
        df, kw, targets, om = sinter_inputs(sns)
        st.session_state["_sinter_track"] = {"runs": runs, "time": dt.datetime.now(), "fp": fp_sinter(df, kw, targets, om), "om": om}
    if not runs:
        st.session_state.pop("_sinter_track", None)


def combined_run_current(sns):
    """The last combined run when none of its inputs has changed since, else None."""
    run = st.session_state.get("cmb_run")
    if not run or not run.get("ok") or not run.get("fp"):
        return None
    try:
        sdf, kw, targets, om = sinter_inputs(sns)
        ms = nsrun.state("mbf")
        now = (fp_sinter(sdf, kw, targets, om), fp_mbf(ms.df, ms.cfg), float(run["plan"]))
    except Exception:
        return None
    return run if tuple(run["fp"]) == now else None


def sinter_handoff(sns):
    """What the MBF page's Sinter row takes.  When a combined run is current, its converged sinter result (same sinter tonnage,
    same cap, same plan), so the MBF page and the combined page give the same sinter share and cost.  Otherwise the sinter
    dashboard's own last run (its planning tonnage), which is what the furnace sees before any combined run."""
    run = combined_run_current(sns)
    if run is not None:
        s = run["sinter"]
        return {"source": "combined", "status": s["status"], "vals": dict(s["vals"]), "tonnes": float(s["t"]), "time": run["time"],
                "stale": False, "ach": s["achieved"], "cap": float(run["cap_t"]) if run.get("capped") and run.get("cap_t") else None,
                "plan": float(run["plan"])}
    h = _sinter_dashboard_handoff(sns)
    if h is not None:
        h["source"], h["cap"], h["plan"] = "sinter", None, None
    return h


def _sinter_dashboard_handoff(sns):
    res = st.session_state.get("sinter__result")
    trk = st.session_state.get("_sinter_track")
    if not res or not res.get("blend") or not res.get("achieved") or not trk:
        return None
    vals = L.row_values(res["achieved"], res["cost"], trk["om"])
    df, kw, targets, om = sinter_inputs(sns)
    return {"status": res["status"], "vals": vals, "tonnes": float(res.get("prod", 0.0)), "time": trk["time"],
            "stale": fp_sinter(df, kw, targets, om) != trk["fp"], "ach": res["achieved"]}


# ------------------------------------------------------------------------------------------ the MBF page's sinter row
def sync_mbf_sinter_row(sns, mode):
    """Make the MBF dashboard's switched-on Sinter row follow the sinter model (or give the typed values back).
    Returns a dict describing the state, for the banner and the blocker."""
    S = nsrun.state("mbf")
    row = L.find_sinter_row(S.df)
    typed = st.session_state.setdefault("_typed_sinter_rows", {})
    info = {"mode": mode, "row": row, "handoff": None, "state": "typed"}
    if mode == TYPED:
        if typed:                                            # give the typed values back
            d = S.df.copy()
            for name, vals in typed.items():
                if name in d.index:
                    for c, v in vals.items():
                        d.loc[name, c] = v
            S.df = d
            S.mat_ver += 1
            typed.clear()
            S.changed, S.changed_source = True, "Sinter row (typed values restored)"
        return info
    if row is None:
        info["state"] = "norow"
        return info
    h = sinter_handoff(sns)
    info["handoff"] = h
    if h is None:
        info["state"] = "missing"
        return info
    info["state"] = "ok"
    if h.get("plan") and abs(float(S.cfg.stock_plan_hm_tonnes) - float(h["plan"])) > 1e-9:
        S.cfg.stock_plan_hm_tonnes = float(h["plan"])      # the combined plan drives the stock caps, as in the combined run
        S.cfg_ver += 1
        S.changed, S.changed_source = True, "Plan (from the combined run)"
    cols = L.ROW_COLS + ["RM_Stock"]
    cur = {c: S.df.loc[row, c] for c in cols}
    new = dict(h["vals"]); new["RM_Stock"] = float(h["cap"]) if h.get("cap") else np.nan
    same = all((pd.isna(cur[c]) and pd.isna(new[c])) or (not pd.isna(cur[c]) and not pd.isna(new[c]) and abs(float(cur[c]) - float(new[c])) < 1e-9) for c in cols)
    if not same:
        typed.setdefault(row, {c: cur[c] for c in cols})    # remember the typed values once
        d = S.df.copy()
        for c in cols:
            d[c] = d[c].astype(float)
        for c in cols:
            d.loc[row, c] = new[c]
        S.df = d
        S.mat_ver += 1                                      # reset the materials editor so it shows the new row
        S.changed, S.changed_source = True, "Sinter result"
    return info


def mbf_extra_blockers(sns):
    """Reasons the MBF dashboard may not run, added to its own list (so Run optimiser says why)."""
    if st.session_state.get("mbf_sinter_mode", MODEL) != MODEL:
        return []
    S = nsrun.state("mbf")
    if L.find_sinter_row(S.df) is None:
        return ["No Sinter row is switched on in the materials table. Switch one on (the sinter model's result takes its place)."]
    if sinter_handoff(sns) is None:
        return ["The sinter row comes from the sinter model, which has not been run yet. Run it under Sinter model > Dashboard, "
                "or choose 'Typed row' above."]
    return []


# ------------------------------------------------------------------------------------------ what changed between two runs
def inputs_snapshot(sdf, kw, targets, om, mdf, mcfg, plan):
    return {"sdf": sdf.copy(), "kw": dict(kw), "targets": dict(targets), "om": float(om),
            "mdf": mdf.copy(), "mcfg": dataclasses.replace(mcfg), "plan": float(plan)}


def _num(v):
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, (float, np.floating)) else str(v)


def diff_inputs(a, b, limit=14):
    """Plain-language list of what differs between two input snapshots (sinter side first, then the furnace side)."""
    out = []
    if a is None:
        return out
    sa, sb = a["sdf"], b["sdf"]
    for m in list(dict.fromkeys(list(sa.index) + list(sb.index))):
        if m not in sa.index or m not in sb.index:
            out.append(f"Sinter material {m} {'added' if m not in sa.index else 'removed'}")
            continue
        for col, lab in (("Price_Rs_t", "price"), ("Available_Tonnes", "stock t"), ("Tech_Min", "Tech min"), ("Tech_Max", "Tech max")):
            x, y = float(sa.loc[m, col]), float(sb.loc[m, col])
            if abs(x - y) > 1e-9:
                if col == "Available_Tonnes" and (x <= 0 or y <= 0):
                    out.append(f"Sinter {m} switched {'on' if y > 0 else 'off'}")
                else:
                    out.append(f"Sinter {m} {lab} {_num(x)} -> {_num(y)}")
        for col in SINTER_CHEM:
            x, y = float(sa.loc[m, col]), float(sb.loc[m, col])
            if abs(x - y) > 1e-9:
                out.append(f"Sinter {m} {col.replace('_Pct', '')} {_num(x)} -> {_num(y)}")
    if abs(a["om"] - b["om"]) > 1e-9:
        out.append(f"Sinter O&M {_num(a['om'])} -> {_num(b['om'])} Rs/t")
    for k in sorted(set(a["kw"]) | set(b["kw"])):
        x, y = a["kw"].get(k), b["kw"].get(k)
        if _canon(x) != _canon(y):
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                out.append(f"Sinter setting {k} {_num(x)} -> {_num(y)}")
            else:
                out.append(f"Sinter setting {k} changed")
    for k in sorted(set(a["targets"]) | set(b["targets"])):
        if a["targets"].get(k) != b["targets"].get(k):
            out.append(f"Sinter spec {k} {_num(a['targets'].get(k))} -> {_num(b['targets'].get(k))}")
    try:
        for x in an.describe_changes(a["mdf"], b["mdf"], a["mcfg"], b["mcfg"]):
            out.append("Furnace: " + x)
    except Exception:
        pass
    if abs(a["plan"] - b["plan"]) > 1e-9:
        out.append(f"Plan {_num(a['plan'])} -> {_num(b['plan'])} tHM")
    return out[:limit] + ([f"(+{len(out) - limit} more)"] if len(out) > limit else [])


# ------------------------------------------------------------------------------------------ stock checks (two, never added together)
def stock_checks(run):
    """Sinter plant stock check and furnace stock check for the final run, each against its own stock."""
    inp, f, s, plan = run["inputs"], run["furnace"], run["sinter"], run["plan"]
    kw = inp["kw"]
    rep = sopt.inventory_usage_report(s["blend"], inp["sdf"], s["t"], horizon_days=kw.get("horizon_days", 7.0),
                                      stock_basis=kw.get("stock_basis", sopt.DEFAULT_STOCK_BASIS))
    srows = []
    if not rep.empty:
        for _, r in rep.iterrows():
            if r["Tonnes used"] <= 1e-9:
                continue
            cover = float(r["Cover (x horizon)"]) * 100.0 if np.isfinite(r["Cover (x horizon)"]) else np.inf
            dry_stock = float(r["Tonnes used"]) * float(r["Cover (x horizon)"]) if np.isfinite(r["Cover (x horizon)"]) else float(r["Stock t"])
            srows.append({"Material": r["Material"], "Needed t": float(r["Tonnes used"]), "Stock t": dry_stock,      # both dry, as the sinter model counts
                          "Cover %": cover, "Days of cover": float(r["Days of cover"]) if np.isfinite(r["Days of cover"]) else np.inf})
    frows = []
    df = f["df"]
    for m, kg in f["res"][1].items():
        if kg <= 1e-9 or "RM_Stock" not in df.columns or pd.isna(df.loc[m, "RM_Stock"]):
            continue
        need = kg * plan / 1000.0
        stock = float(df.loc[m, "RM_Stock"])
        frows.append({"Material": m, "Needed t": need, "Stock t": stock, "Cover %": stock / need * 100.0 if need > 0 else np.inf})
    return pd.DataFrame(srows), pd.DataFrame(frows)
