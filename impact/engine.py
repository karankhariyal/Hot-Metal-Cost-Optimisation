"""
Sinter-to-hot-metal impact: change anything in the sinter making and see, step by step, what it does to the cost of one
tonne of hot metal.  No Streamlit code here, so it can be tested on its own.

The three models (sinter, MBF, combined) are never touched.  This model reads the combined run as its baseline, applies the
changes to COPIES of the inputs, and re-runs the same combined loop, so a result here is exactly what the combined model
would give with those inputs.

Two views of the same change:
  by lever   the levers (prices, stock, O&M, returns, spec windows, tolerances, coke band, stock-ratio rule) are applied one
             after another in a fixed order and the loop is re-run each time, so the slices add up to the total.
  by path    how the change travels from sinter making to the furnace: the furnace's Sinter row is moved from its old to its
             new value one field at a time (price, Fe, Al2O3, MgO, SiO2, CaO, availability) and the furnace re-solved.
Both depend on the order, which is fixed and shown on the page; the total never does.
"""
import dataclasses
import time

import numpy as np
import pandas as pd

from combined import cooptimise as C
from combined import loop as L
from sinter import optimizer as sopt

# ---- the levers, in the order they are applied
GROUPS = ["prices", "stock", "om", "fom", "returns", "spec", "tol", "coke", "ratio"]
LABEL = {"prices": "Material prices", "stock": "Material stock", "om": "Sinter O&M", "fom": "Furnace O&M", "returns": "Return sinter (IOL Fines, BFR)",
         "spec": "Spec windows", "tol": "Tolerance edges", "coke": "Coke band", "ratio": "Stock-ratio strictness"}
SPEC_KEYS = ["Basicity_min", "Basicity_max", "MgO_min", "MgO_max", "CaO_min", "CaO_max", "SiO2_max", "Al2O3_max", "Al2O3_SiO2_max"]
TOL_KEYS = ["Fe_min", "Fe_max", "Basicity_min", "Basicity_max", "MgO_min", "MgO_max", "CaO_min", "CaO_max", "SiO2_max", "Al2O3_max", "Al2O3_SiO2_max"]
PATH = [("Price_Rs_t", "Sinter price"), ("Fe", "Sinter Fe"), ("Al2O3", "Sinter Al2O3"), ("MgO", "Sinter MgO"), ("SiO2", "Sinter SiO2"), ("CaO", "Sinter CaO")]


# ---------------------------------------------------------------------------------------------- the change set
def _nz(x):
    return x is not None and abs(float(x)) > 1e-12


def groups_changed(ch):
    """Which lever groups carry a change (in application order)."""
    on = {"prices": any(_nz(v) for v in ch.get("price_pct", {}).values()),
          "stock": any(_nz(v) for v in ch.get("stock_pct", {}).values()),
          "om": ch.get("om") is not None, "fom": ch.get("furnace_om") is not None,
          "returns": ch.get("iol_pct") is not None or ch.get("bfr_pct") is not None,
          "spec": bool(ch.get("targets")), "tol": bool(ch.get("tolerances")),
          "coke": ch.get("coke_min") is not None or ch.get("coke_max") is not None,
          "ratio": ch.get("inventory_weight") is not None}
    return [g for g in GROUPS if on[g]]


def base_tolerances(inp):
    return sopt._merge_tolerances(inp["kw"].get("tolerances"), inp["targets"])


def base_values(inp):
    kw = inp["kw"]
    return {"iol_pct": 100.0 * float(kw.get("iol_nominal", sopt.IOL_FINES_NOMINAL_PCT)), "bfr_pct": 100.0 * float(kw.get("bf_nominal", sopt.BF_RETURNS_NOMINAL_PCT)),
            "coke_min": float(kw.get("coke_min_rate", sopt.DEFAULT_COKE_MIN_KG_T)), "coke_max": float(kw.get("coke_max_rate", sopt.DEFAULT_COKE_MAX_KG_T)),
            "inventory_weight": float(kw.get("inventory_weight", sopt.DEFAULT_INVENTORY_WEIGHT)), "om": float(inp["om"]),
            "furnace_om": float(inp["mcfg"].om_rs_thm)}


def apply(inp, ch, only=None):
    """A copy of the combined inputs with the changes applied.  `only` limits it to some lever groups.  The originals are not touched."""
    new = {"sdf": inp["sdf"].copy(), "kw": dict(inp["kw"]), "targets": dict(inp["targets"]), "om": float(inp["om"]), "mdf": inp["mdf"].copy(),
           "mcfg": dataclasses.replace(inp["mcfg"]), "plan": float(inp["plan"])}
    on = lambda g: only is None or g in only
    sdf = new["sdf"]
    for c in ("Price_Rs_t", "Available_Tonnes"):
        sdf[c] = sdf[c].astype(float)
    if on("prices"):
        for m, pct in ch.get("price_pct", {}).items():
            if m in sdf.index and _nz(pct):
                sdf.loc[m, "Price_Rs_t"] *= 1.0 + float(pct) / 100.0
    if on("stock"):
        for m, pct in ch.get("stock_pct", {}).items():
            if m in sdf.index and _nz(pct):
                sdf.loc[m, "Available_Tonnes"] = max(float(sdf.loc[m, "Available_Tonnes"]) * (1.0 + float(pct) / 100.0), 0.0)
    if on("om") and ch.get("om") is not None:
        new["om"] = float(ch["om"])
    if on("fom") and ch.get("furnace_om") is not None:
        new["mcfg"].om_rs_thm = float(ch["furnace_om"])
    if on("returns"):
        if ch.get("iol_pct") is not None:
            new["kw"]["iol_nominal"] = float(ch["iol_pct"]) / 100.0
        if ch.get("bfr_pct") is not None:
            new["kw"]["bf_nominal"] = float(ch["bfr_pct"]) / 100.0
    if on("spec") and ch.get("targets"):
        new["targets"].update({k: float(v) for k, v in ch["targets"].items()})
    if on("tol") and ch.get("tolerances"):
        tol = dict(base_tolerances(inp))
        tol.update({k: float(v) for k, v in ch["tolerances"].items()})
        new["kw"]["tolerances"] = tol
    if on("coke"):
        if ch.get("coke_min") is not None:
            new["kw"]["coke_min_rate"] = float(ch["coke_min"])
        if ch.get("coke_max") is not None:
            new["kw"]["coke_max_rate"] = float(ch["coke_max"])
    if on("ratio") and ch.get("inventory_weight") is not None:
        new["kw"]["inventory_weight"] = float(ch["inventory_weight"])
    return new


def describe(inp, ch):
    """The change set as a table: lever, item, base, changed."""
    rows, sdf, bv = [], inp["sdf"], base_values(inp)
    for m, pct in ch.get("price_pct", {}).items():
        if _nz(pct) and m in sdf.index:
            p = float(sdf.loc[m, "Price_Rs_t"])
            rows.append((LABEL["prices"], m, f"Rs {p:,.0f} /t", f"Rs {p * (1 + pct / 100):,.0f} /t  ({pct:+g} %)"))
    for m, pct in ch.get("stock_pct", {}).items():
        if _nz(pct) and m in sdf.index:
            t = float(sdf.loc[m, "Available_Tonnes"])
            rows.append((LABEL["stock"], m, f"{t:,.0f} t", f"{max(t * (1 + pct / 100), 0):,.0f} t  ({pct:+g} %)"))
    if ch.get("om") is not None:
        rows.append((LABEL["om"], "Sinter O&M", f"Rs {bv['om']:,.0f} /t", f"Rs {float(ch['om']):,.0f} /t"))
    if ch.get("furnace_om") is not None:
        rows.append((LABEL["fom"], "Furnace O&M", f"Rs {bv['furnace_om']:,.0f} /tHM", f"Rs {float(ch['furnace_om']):,.0f} /tHM"))
    for k, lab in (("iol_pct", "IOL Fines, % of charged mix"), ("bfr_pct", "BFR, % of charged mix")):
        if ch.get(k) is not None:
            rows.append((LABEL["returns"], lab, f"{bv[k]:g} %", f"{float(ch[k]):g} %"))
    for k, v in (ch.get("targets") or {}).items():
        rows.append((LABEL["spec"], k.replace("_", " "), f"{float(inp['targets'][k]):g}", f"{float(v):g}"))
    bt = base_tolerances(inp)
    for k, v in (ch.get("tolerances") or {}).items():
        rows.append((LABEL["tol"], k.replace("_", " "), f"{bt[k]:g}", f"{float(v):g}"))
    for k, lab in (("coke_min", "Coke minimum, kg/t"), ("coke_max", "Coke maximum, kg/t")):
        if ch.get(k) is not None:
            rows.append((LABEL["coke"], lab, f"{bv[k]:g}", f"{float(ch[k]):g}"))
    if ch.get("inventory_weight") is not None:
        rows.append((LABEL["ratio"], "Inventory weight (1 = ores held to stock shares)", f"{bv['inventory_weight']:g}", f"{float(ch['inventory_weight']):g}"))
    return pd.DataFrame(rows, columns=["Lever", "Item", "Base", "Changed"])


# ---------------------------------------------------------------------------------------------- running
def _quick(base_run, inp):
    """The scans' quick mode (0.5 % tolerance, 4 passes), started from the baseline run."""
    return L.run_loop(inp["sdf"], inp["kw"], inp["targets"], inp["om"], inp["mdf"], inp["mcfg"], inp["plan"], tol=0.005, max_pass=4, refine_steps=0,
                      start_t=base_run["sinter"]["t"], cap_t0=base_run["cap_t"], explain_final=False)


def _full(base_run, inp, progress=None):
    """The same run the combined page would do with these inputs (same tolerance and passes as the baseline)."""
    return L.run_loop(inp["sdf"], inp["kw"], inp["targets"], inp["om"], inp["mdf"], inp["mcfg"], inp["plan"], tol=base_run.get("tol", 0.001),
                      max_pass=base_run.get("max_pass", 8), progress=progress)


def path_chain(base, new, inp):
    """Move the furnace's Sinter row from the baseline's values to the new run's, one field at a time, re-solving the furnace each time.
    Rows: step, hot metal cost after the step, change from the step.  The last row is the loop's own settling, so the steps add up exactly."""
    f0, f1 = base["furnace"], new["furnace"]
    d, row, plan, cfg = f0["df"].copy(), base["row"], float(base["plan"]), inp["mcfg"]
    for c in [p[0] for p in PATH] + ["RM_Stock"]:
        d[c] = d[c].astype(float)

    def solve(t):
        res, _c = L.solve_furnace(t, cfg, plan)
        return float(res[2]) if res[0] == "Optimal" else None
    cost = solve(d)
    start = float(f0["res"][2]) if cost is None else cost
    rows, cur = [], start
    steps = [(c, lab) for c, lab in PATH] + [("RM_Stock", "Sinter availability (stock cap)")]
    for col, lab in steps:
        old, newv = d.loc[row, col], f1["df"].loc[row, col]
        same = (pd.isna(old) and pd.isna(newv)) or (not pd.isna(old) and not pd.isna(newv) and abs(float(old) - float(newv)) < 1e-9)
        if same:
            rows.append({"Step": lab, "Base": old, "Changed": newv, "Hot metal Rs/tHM after": cur, "Change Rs/tHM": 0.0})
            continue
        d.loc[row, col] = newv
        c2 = solve(d)
        if c2 is None:
            rows.append({"Step": lab, "Base": old, "Changed": newv, "Hot metal Rs/tHM after": np.nan, "Change Rs/tHM": np.nan})
            continue
        rows.append({"Step": lab, "Base": old, "Changed": newv, "Hot metal Rs/tHM after": c2, "Change Rs/tHM": c2 - cur})
        cur = c2
    om0, om1 = float(inp["mcfg"].om_rs_thm), float(new["inputs"]["mcfg"].om_rs_thm)
    if abs(om1 - om0) > 1e-9:                                  # furnace O&M is a fixed Rs per tHM on top of the raw materials
        cur += om1 - om0
        rows.append({"Step": "Furnace O&M", "Base": om0, "Changed": om1, "Hot metal Rs/tHM after": cur, "Change Rs/tHM": om1 - om0})
    final = float(f1["res"][2])
    rows.append({"Step": "Loop settling and rounding", "Base": np.nan, "Changed": np.nan, "Hot metal Rs/tHM after": final, "Change Rs/tHM": final - cur})
    out = pd.DataFrame(rows)
    out["Change Rs/tHM"] = out["Change Rs/tHM"].fillna(0.0)
    return out, start


def lever_steps(base_run, ch, groups, total, progress=None):
    """Apply the changed lever groups one after another (cumulative) in quick runs.  Rows: lever, change from that lever.  A last row
    carries the difference between quick mode and full precision so the slices add up to the full-precision total."""
    inp = base_run["inputs"]
    prev = _quick(base_run, inp)
    if not prev.get("ok"):
        return None, "The quick re-run of the baseline failed."
    prev_hm, rows, applied = C.hm(prev), [], []
    for i, g in enumerate(groups):
        applied.append(g)
        if progress:
            progress(0.35 + 0.55 * i / max(len(groups), 1), f"Adding {LABEL[g].lower()}")
        r = _quick(base_run, apply(inp, ch, only=applied))
        if not r.get("ok"):
            rows.append({"Lever": LABEL[g], "Change Rs/tHM": np.nan, "Note": "no recipe: " + (r.get("message") or "")[:80]})
            return pd.DataFrame(rows), "A step had no recipe, so the slices stop there."
        h = C.hm(r)
        rows.append({"Lever": LABEL[g], "Change Rs/tHM": h - prev_hm, "Note": "sinter " + r["sinter"]["status"] + (", knife-edge" if r.get("jump") else "")})
        prev_hm = h
    resid = total - sum(float(x["Change Rs/tHM"]) for x in rows)
    rows.append({"Lever": "Loop precision (quick steps vs full run)", "Change Rs/tHM": resid, "Note": ""})
    return pd.DataFrame(rows), ""


def run_impact(base_run, ch, progress=None):
    """Apply the changes and trace their effect from sinter making to hot metal.  `base_run` is the combined run (never changed)."""
    t0 = time.time()
    inp = base_run["inputs"]
    groups = groups_changed(ch)
    out = {"ok": False, "message": "", "changes": ch, "groups": groups, "base": base_run, "new": None, "total": 0.0, "levers": None, "lever_note": "",
           "chain": None, "head": None, "sin": None, "chem": None, "fur": None, "describe": describe(inp, ch), "knife": bool(base_run.get("jump")), "seconds": 0.0}
    if not groups:
        out["message"] = "No change is set yet."
        return out
    if progress:
        progress(0.05, "Running the combined loop with the changes")
    new_inp = apply(inp, ch)
    new = _full(base_run, new_inp)
    if not new.get("ok"):
        out["message"] = "With these changes the combined model has no recipe: " + (new.get("message") or "see the Hot metal cost page for how it stops.")
        out["new"] = new
        out["seconds"] = time.time() - t0
        return out
    new["inputs"] = new_inp
    total = C.hm(new) - C.hm(base_run)
    if progress:
        progress(0.2, "Tracing the change from sinter to the furnace")
    chain, start = path_chain(base_run, new, inp)
    out.update(ok=True, new=new, total=total, chain=chain, knife=bool(base_run.get("jump") or new.get("jump")))
    head, sin, chem, fur = C.compare_tables(base_run, new, inp["targets"])
    out.update(head=head, sin=sin, chem=chem, fur=fur)
    if len(groups) > 1:
        out["levers"], out["lever_note"] = lever_steps(base_run, ch, groups, total, progress)
    out["message"] = (f"Hot metal cost moves by Rs {total:+,.0f} per tHM ({100 * total / C.hm(base_run):+.2f} %).")
    out["seconds"] = time.time() - t0
    if progress:
        progress(1.0, "Done")
    return out


# ---------------------------------------------------------------------------------------------- what each condition costs
def default_conditions(inp):
    """The rules that can be relaxed one at a time, each with its own default step and unit."""
    bv = base_values(inp)
    return [
        {"key": "ratio", "name": "Ores held to their stock shares", "unit": "inventory weight to", "step": 0.0, "lo": 0.0, "hi": 1.0},
        {"key": "basicity", "name": "Basicity window", "unit": "widen each side by", "step": 0.1, "lo": 0.0, "hi": 0.5},
        {"key": "mgo", "name": "MgO window", "unit": "widen each side by", "step": 0.2, "lo": 0.0, "hi": 1.0},
        {"key": "cao", "name": "CaO window", "unit": "widen each side by", "step": 0.5, "lo": 0.0, "hi": 2.0},
        {"key": "sio2", "name": "SiO2 maximum", "unit": "raise by", "step": 0.3, "lo": 0.0, "hi": 1.0},
        {"key": "al2o3", "name": "Al2O3 maximum", "unit": "raise by", "step": 0.3, "lo": 0.0, "hi": 1.0},
        {"key": "tol_sio2", "name": "SiO2 tolerance edge", "unit": "raise by", "step": 0.2, "lo": 0.0, "hi": 1.0},
        {"key": "tol_mgo", "name": "MgO tolerance floor", "unit": "lower by", "step": 0.2, "lo": 0.0, "hi": 1.0},
        {"key": "tol_al2o3", "name": "Al2O3 tolerance edge", "unit": "raise by", "step": 0.3, "lo": 0.0, "hi": 1.0},
        {"key": "tol_fe", "name": "Fe tolerance floor", "unit": "lower by", "step": 0.5, "lo": 0.0, "hi": 1.5},
        {"key": "coke", "name": "Coke band", "unit": "widen each side by (kg/t)", "step": 10.0, "lo": 0.0, "hi": 30.0},
    ]


def condition_changes(inp, key, step):
    """The change set that relaxes one condition by `step`."""
    t, tol, bv = inp["targets"], base_tolerances(inp), base_values(inp)
    s = float(step)
    if key == "ratio":
        return {"inventory_weight": s}
    if key == "basicity":
        return {"targets": {"Basicity_min": t["Basicity_min"] - s, "Basicity_max": t["Basicity_max"] + s}}
    if key == "mgo":
        return {"targets": {"MgO_min": t["MgO_min"] - s, "MgO_max": t["MgO_max"] + s}}
    if key == "cao":
        return {"targets": {"CaO_min": t["CaO_min"] - s, "CaO_max": t["CaO_max"] + s}}
    if key == "sio2":
        return {"targets": {"SiO2_max": t["SiO2_max"] + s}}
    if key == "al2o3":
        return {"targets": {"Al2O3_max": t["Al2O3_max"] + s}}
    if key == "tol_sio2":
        return {"tolerances": {"SiO2_max": tol["SiO2_max"] + s}}
    if key == "tol_mgo":
        return {"tolerances": {"MgO_min": tol["MgO_min"] - s}}
    if key == "tol_al2o3":
        return {"tolerances": {"Al2O3_max": tol["Al2O3_max"] + s}}
    if key == "tol_fe":
        return {"tolerances": {"Fe_min": tol["Fe_min"] - s}}
    if key == "coke":
        return {"coke_min": bv["coke_min"] - s, "coke_max": bv["coke_max"] + s}
    raise KeyError(key)


def condition_sweep(base_run, conditions, progress=None):
    """Relax ONE condition at a time (the rest unchanged) in quick runs and report what each would be worth.
    `conditions` is a list of dicts with key, name, unit, step.  Returns (table, quick baseline hot metal cost)."""
    inp = base_run["inputs"]
    base_q = _quick(base_run, inp)
    if not base_q.get("ok"):
        return pd.DataFrame(), None
    b_hm, b_sp, b_st = C.hm(base_q), float(base_q["sinter"]["vals"]["Price_Rs_t"]), base_q["sinter"]["status"]
    rows = []
    for i, c in enumerate(conditions):
        if progress:
            progress(i / max(len(conditions), 1), f"Relaxing: {c['name']}")
        ch = condition_changes(inp, c["key"], c["step"])
        r = _quick(base_run, apply(inp, ch))
        row = {"Condition": c["name"], "Relaxed": f"{c['unit']} {c['step']:g}"}
        if not r.get("ok"):
            row.update({"Hot metal change Rs/tHM": np.nan, "Sinter price change Rs/t": np.nan, "Sinter status": "no recipe", "Fe %": np.nan, "SiO2 %": np.nan,
                        "MgO %": np.nan, "Al2O3 %": np.nan, "Note": (r.get("message") or "")[:80]})
        else:
            a = r["sinter"]["achieved"]
            row.update({"Hot metal change Rs/tHM": C.hm(r) - b_hm, "Sinter price change Rs/t": float(r["sinter"]["vals"]["Price_Rs_t"]) - b_sp,
                        "Sinter status": r["sinter"]["status"] + ("" if r["sinter"]["status"] == b_st else f" (was {b_st})"),
                        "Fe %": float(a["Fe"]), "SiO2 %": float(a["SiO2"]), "MgO %": float(a["MgO"]), "Al2O3 %": float(a["Al2O3"]),
                        "Note": "knife-edge" if r.get("jump") else ""})
        rows.append(row)
    out = pd.DataFrame(rows).sort_values("Hot metal change Rs/tHM", na_position="last").reset_index(drop=True)
    if progress:
        progress(1.0, "Done")
    return out, b_hm
