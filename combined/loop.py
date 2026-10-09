"""
The combined model: one tonne of hot metal costed from the sinter plant through the blast furnace.

    sinter model  ->  sinter result (price, Fe, CaO, SiO2, Al2O3, MgO)  ->  furnace table's switched-on Sinter row
    furnace model ->  sinter used (kg/tHM)  ->  sinter tonnes needed (kg/tHM x plan tHM / 1000)  ->  sinter model again ...

No Streamlit code lives here, so the loop can be tested on its own.  The two engines are used exactly as they are
(sinter/optimizer.py, mbf/optimiser.py).  Nothing is ever edited in them.

Rules the loop follows (all from the project README):
  * the loop reports whether it converged, and shows the last passes when it did not;
  * a failed sinter run (no recipe) stops the run with the sinter model's own shortage message; an empty row is never
    passed to the furnace;
  * sinter is capped at what the plant can make (status Optimal or Relaxed): if the furnace asks for more, the largest
    producible tonnage is found and handed to the furnace as the sinter row's stock, so the furnace chooses between
    capped sinter and more ore;
  * the sinter solver keeps its last-run report at module level, so every call is made behind a lock and the report
    is captured straight away; the 0-100 % curve is not needed here (that is the MBF page's own analysis).
"""
import copy
import threading
import time

import numpy as np
import pandas as pd

from sinter import optimizer as sopt
from mbf import optimiser as mopt

SINTER_OK = ("Optimal", "Relaxed")
ROW_COLS = ["Price_Rs_t", "Fe", "CaO", "SiO2", "Al2O3", "MgO"]
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------------------------- helpers
def find_sinter_row(mbf_df):
    """Name of the furnace table's switched-on Sinter row (the first one if several are on), or None."""
    rows = [m for m in mbf_df.index if str(mbf_df.loc[m, "Group"]).strip() == "Sinter" and bool(mbf_df.loc[m, "Available"])]
    return rows[0] if rows else None


def row_values(achieved, raw_cost, om):
    """What the sinter model hands to the furnace: price = dry raw-material cost + O&M, chemistry from `achieved`."""
    return {"Price_Rs_t": float(raw_cost) + float(om), "Fe": float(achieved["Fe"]), "CaO": float(achieved["CaO"]),
            "SiO2": float(achieved["SiO2"]), "Al2O3": float(achieved["Al2O3"]), "MgO": float(achieved["MgO"])}


def furnace_table(mbf_df, row, vals, cap_t=None):
    """The furnace table for one run: every material from the MBF Excel except the sinter row, which takes the sinter
    result.  Other Sinter rows are switched off.  Moisture, S and Mn stay as in the MBF Excel (the sinter model does
    not produce them).  The sinter stock is the producible tonnage when the plant cannot make what is asked, else none."""
    d = mbf_df.copy()
    for c in ROW_COLS + ["RM_Stock"]:                  # an integer column would refuse a decimal price
        d[c] = d[c].astype(float)
    for m in d.index:
        if m != row and str(d.loc[m, "Group"]).strip() == "Sinter":
            d.loc[m, "Available"] = False
    for c in ROW_COLS:
        d.loc[row, c] = vals[c]
    d.loc[row, "Available"] = True
    d.loc[row, "RM_Stock"] = float(cap_t) if cap_t else np.nan
    return d


def solve_sinter(df, tonnes, kwargs, targets):
    """One sinter solve, behind a lock; returns (status, blend, cost, achieved, diag, report)."""
    with _LOCK:
        status, blend, cost, ach, diag, _fb = sopt.solve_blend_with_compensation(
            df, float(tonnes), targets, baseline_blend=None, **kwargs)
        report = sopt.get_last_run_report()
    return status, blend, cost, ach, diag, report


def solve_furnace(table, cfg, plan_thm, explain=False):
    c = copy.deepcopy(cfg)
    c.stock_plan_hm_tonnes = float(plan_thm)           # the plan drives the stock caps, as on the MBF Inputs page
    return mopt.solve(table, c, explain=explain), c


def _capacity(df, lo, hi, kwargs, targets, steps=12):
    """Largest tonnage between lo (known to be fine, or None) and hi (not fine) whose sinter status is Optimal/Relaxed."""
    best = None
    if lo is None:
        lo = hi * 0.05
        r = solve_sinter(df, lo, kwargs, targets)
        if r[0] not in SINTER_OK:
            return None, r
        best = r
    for _ in range(steps):
        mid = (lo + hi) / 2.0
        r = solve_sinter(df, mid, kwargs, targets)
        if r[0] in SINTER_OK:
            lo, best = mid, r
        else:
            hi = mid
    if best is None:
        best = solve_sinter(df, lo, kwargs, targets)
        if best[0] not in SINTER_OK:
            return None, best
    return lo, best


def _jump(hist, tol):
    """Two passes on either side of the balance point, closer together than the tolerance, yet each still far from
    balanced: the furnace's demand jumps there (it switches between two burdens as the sinter chemistry shifts), so
    no exact balance exists.  Returns (pass index on the 'plant makes at least what the furnace uses' side, other index)."""
    for ip, (tp, gp, i_p) in enumerate(hist):
        if gp <= 0:
            continue
        for tn, gn, i_n in hist:
            if gn < 0 and abs(tp - tn) <= tol * max(tp, tn) and min(abs(gp), abs(gn)) > tol * max(tp, tn):
                return i_n, i_p
    return None


def _next_tonnage(hist, t_new, cap_t):
    """Next sinter tonnage to try.  Sinter demand can fall as tonnage rises (the chemistry shifts as the plant is
    pushed), and then plain feedback bounces between two values.  So: once two passes disagree in sign (the furnace asked
    for more than was run, then for less), search inside that bracket (secant step, kept off the ends); until then take
    the furnace's answer as the next try."""
    pos = [(t, g) for t, g, _i in hist if g > 0]
    neg = [(t, g) for t, g, _i in hist if g < 0]
    if pos and neg:
        tp, gp = min(pos, key=lambda x: min(abs(x[0] - y[0]) for y in neg))
        tn, gn = min(neg, key=lambda y: abs(y[0] - tp))
        lo, hi = min(tp, tn), max(tp, tn)
        t_sec = tp + gp * (tn - tp) / (gp - gn)
        t_next = min(max(t_sec, lo + 0.1 * (hi - lo)), hi - 0.1 * (hi - lo))
    else:
        t_next = t_new
    return min(t_next, cap_t) if cap_t else t_next


# ---------------------------------------------------------------------------------------------- the loop
def run_loop(sinter_df, sinter_kwargs, targets, om, mbf_df, mbf_cfg, plan_thm, *, tol=0.001, max_pass=8,
             start_t=None, cap_t0=None, progress=None, explain_final=True, refine_steps=8):
    """Run the combined model.  Returns a dict.  `ok` is False when the run had to stop; `message` then says why in plain
    words.  The same inputs always give the same answer: the start does not depend on the furnace table's own Sinter row
    (whatever was typed or handed over earlier), the capacity search is fine enough not to depend on where it started,
    and a demand jump is refined to about a tonne before the result is read."""
    t_start = time.time()
    plan = float(plan_thm)
    out = {"ok": False, "message": "", "converged": False, "passes": [], "plan": plan, "om": float(om), "tol": tol,
           "max_pass": max_pass, "capped": False, "cap_t": None, "row": None, "sinter": None, "furnace": None}
    row = find_sinter_row(mbf_df)
    if row is None:
        out["message"] = ("No Sinter row is switched on in the furnace table. Switch one on under MBF > Materials & stock "
                          "(it will be replaced by the sinter model's result).")
        return out
    out["row"] = row
    st_ = {"cap": cap_t0, "last_ok": None, "n": 0}
    snaps = []                                      # every pass: {"t", "g", "sinter", "furnace", "rec"}

    def fail(msg, rec=None, sinter=None):
        out["message"] = msg
        if rec:
            out["passes"].append(rec)
        if sinter:
            out["sinter"] = sinter
        return None

    def do_pass(t, label=""):
        """One sinter run at tonnage t, its result into the furnace, the furnace solved.  Returns the pass, or None on failure."""
        st_["n"] += 1
        n = st_["n"]
        if progress:
            progress(n, f"Pass {n}: sinter at {t:,.0f} t")
        status, blend, cost, ach, diag, rep = solve_sinter(sinter_df, t, sinter_kwargs, targets)
        note = label
        if status not in SINTER_OK:
            lo = st_["last_ok"] if (st_["last_ok"] is not None and st_["last_ok"] < t) else None
            cap, r = _capacity(sinter_df, lo, t, sinter_kwargs, targets)
            if cap is None:
                return fail("The sinter model cannot make an acceptable recipe, so the furnace cannot be costed. "
                            + " ".join(str(x) for x in (diag or [])[-2:]),
                            {"Pass": n, "Sinter t": t, "Sinter status": status, "Sinter Rs/t": None, "Furnace Rs/tHM": None,
                             "Sinter kg/tHM": None, "Next t": None, "Note": "sinter failed"},
                            {"status": status, "blend": blend, "cost": cost, "achieved": ach, "diag": diag, "report": rep, "t": t})
            st_["cap"], t = cap, cap
            status, blend, cost, ach, diag, rep = r
            note = (note + "; " if note else "") + f"capped at {cap:,.0f} t (most the plant can make within tolerance)"
            out["capped"], out["cap_t"] = True, cap
        else:
            st_["last_ok"] = max(st_["last_ok"] or 0.0, t)
        vals = row_values(ach, cost, om)
        table = furnace_table(mbf_df, row, vals, st_["cap"])
        fres, fcfg = solve_furnace(table, mbf_cfg, plan)
        sin = {"status": status, "blend": blend, "cost": cost, "achieved": ach, "diag": diag, "report": rep, "t": t,
               "vals": vals, "raw_cost": float(cost)}
        if fres[0] != "Optimal":
            return fail(f"The furnace model could not find a burden with this sinter ({fres[0]}). " + " ".join(str(x) for x in fres[4][-2:]),
                        {"Pass": n, "Sinter t": t, "Sinter status": status, "Sinter Rs/t": vals["Price_Rs_t"], "Furnace Rs/tHM": None,
                         "Sinter kg/tHM": None, "Next t": None, "Note": f"furnace {fres[0]}"}, sin)
        kg = float(fres[1].get(row, 0.0))
        t_new = kg * plan / 1000.0
        rec = {"Pass": n, "Sinter t": t, "Sinter status": status, "Sinter Rs/t": vals["Price_Rs_t"], "Furnace Rs/tHM": float(fres[2]),
               "Sinter kg/tHM": kg, "Next t": t_new, "Note": note}
        out["passes"].append(rec)
        sn = {"t": t, "g": t_new - t, "sinter": sin, "furnace": {"res": fres, "df": table, "cfg": fcfg, "row": row, "kg": kg}, "rec": rec}
        snaps.append(sn)
        return sn

    def settled(sn):
        out["sinter"], out["furnace"] = sn["sinter"], sn["furnace"]
        out["converged"], out["ok"] = True, True

    # start: a fixed rate (1.2 t of sinter per tHM) so the result never depends on what the furnace table's row held
    t = float(start_t) if start_t else plan * 1.2
    done = False
    for _ in range(int(max_pass)):
        sn = do_pass(t)
        if sn is None:
            return out
        t_used, g = sn["t"], sn["g"]
        t_new = t_used + g
        cap = st_["cap"]
        if abs(g) <= tol * max(t_used, 1.0) or (cap is not None and abs(t_used - cap) <= tol * cap and t_new >= t_used * (1.0 - tol)):
            settled(sn)                              # balanced, or: ran AT the ceiling and the furnace wants at least that much
            done = True
            break
        hist = [(x["t"], x["g"], i) for i, x in enumerate(snaps)]
        jmp = _jump(hist, tol)
        if jmp is not None:
            i_n, i_p = jmp                           # the furnace's demand jumps between these two passes
            lo_sn, hi_sn = snaps[i_p], snaps[i_n]    # lo: furnace asks for more than run (g>0); hi: for less (g<0)
            for _k in range(int(refine_steps)):      # narrow the jump to about a tonne, so the result is path-independent
                if abs(hi_sn["t"] - lo_sn["t"]) <= 0.00005 * hi_sn["t"]:     # about half a tonne
                    break
                sn = do_pass((lo_sn["t"] + hi_sn["t"]) / 2.0, "refining the jump")
                if sn is None:
                    return out
                if abs(sn["g"]) <= tol * max(sn["t"], 1.0):
                    break
                if sn["g"] > 0:
                    lo_sn = sn
                else:
                    hi_sn = sn
            if abs(sn["g"]) <= tol * max(sn["t"], 1.0):
                settled(sn)
                done = True
                break
            a_, b_ = hi_sn["rec"], lo_sn["rec"]
            settled(hi_sn)
            out["jump"] = {"low_t": min(a_["Sinter t"], b_["Sinter t"]), "high_t": max(a_["Sinter t"], b_["Sinter t"]),
                           "cost_shown": a_["Furnace Rs/tHM"], "cost_other": b_["Furnace Rs/tHM"]}
            out["message"] = (f"The furnace is on a knife-edge here: between {out['jump']['low_t']:,.1f} and {out['jump']['high_t']:,.1f} t of sinter its demand moves "
                              f"from {b_['Sinter kg/tHM']:,.0f} to {a_['Sinter kg/tHM']:,.0f} kg per tHM, because tiny shifts in sinter chemistry flip its burden (several slag limits bind at once). "
                              f"The result shown is from the side where the plant makes at least what the furnace uses "
                              f"(Rs {a_['Furnace Rs/tHM']:,.0f} per tHM; the other side gives Rs {b_['Furnace Rs/tHM']:,.0f}). Treat differences of about Rs 50 per tHM or less as noise.")
            done = True
            break
        t = _next_tonnage([(x["t"], x["g"], i) for i, x in enumerate(snaps)], t_new, cap)
    if not done:
        sn = snaps[-1]
        out["sinter"], out["furnace"] = sn["sinter"], sn["furnace"]
        out["ok"] = True                             # a result exists, but it did not settle: the page says so and shows the last passes
        out["message"] = (f"The loop did not settle within {max_pass} passes (tolerance {tol*100:.1f} % on sinter tonnage). "
                          "Read the cost as an estimate and look at the last passes.")
    if explain_final and out["ok"] and out["furnace"]:     # the one furnace run that is shown gets its full notes
        f = out["furnace"]
        f["res"], f["cfg"] = solve_furnace(f["df"], mbf_cfg, plan, explain=True)
    out["seconds"] = time.time() - t_start
    return out


# ---------------------------------------------------------------------------------------------- reading a result
def summary(run):
    """The headline numbers of a finished run, for the landing page, scenarios and export."""
    if not run or not run.get("ok"):
        return None
    f, s = run["furnace"], run["sinter"]
    res = f["res"]
    a, burden, plan = res[3], res[1], run["plan"]
    row = run["row"]
    df = f["df"]
    sb = s["blend"]
    cost = float(res[2])
    groups = {}
    for m, kg in burden.items():
        if kg <= 1e-9:
            continue
        g = str(df.loc[m, "Group"]).strip()
        groups.setdefault(g, {"kg": 0.0, "rs": 0.0})
        groups[g]["kg"] += kg
        groups[g]["rs"] += kg * float(df.loc[m, "Price_Rs_t"]) / 1000.0
    om = float(a.get("OM_Rs_tHM", 0.0))
    if om > 0:
        groups["O&M"] = {"kg": 0.0, "rs": om}
    return {
        "hm_cost": cost, "hm_om": om, "hm_raw": cost - om, "plan": plan, "plan_cost_cr": cost * plan / 1e7,
        "sinter_price": s["vals"]["Price_Rs_t"], "sinter_raw": s["raw_cost"], "sinter_om": run["om"],
        "sinter_kg": f["kg"], "sinter_t": s["t"], "sinter_status": s["status"], "furnace_status": res[0],
        "sinter_share": float(a["Sinter_share_pct"]), "coke_kg": float(a["Coke_kg"]), "nut_kg": float(a["NutCoke_kg"]),
        "pci_kg": float(a["PCI_kg"]), "fuel_kg": float(a["Fuel_supplied"]), "slag_kg": float(a["slag_kg"]),
        "flux_kg": float(a["Flux_kg"]), "groups": groups, "burden": {m: float(v) for m, v in burden.items() if v > 1e-9},
        "sinter_blend": {m: float(v) for m, v in sb.items() if v > 1e-9},
        "sinter_ach": {k: float(v) for k, v in s["achieved"].items() if isinstance(v, (int, float, np.floating))},
        "b2": float(a["B2"]), "mgo_pct": float(a["MgO_pct"]), "al2o3_pct": float(a["Al2O3_pct"]), "hm_s": float(a["S_HM_pct"]),
        "converged": run["converged"], "passes": len(run["passes"]), "capped": run["capped"], "cap_t": run["cap_t"],
    }


def compositions(run):
    """The two burdens of a finished run as tables: (sinter burden per t sinter, furnace burden per tHM).

    Sinter: dry kg per tonne of sinter and Rs per tonne of sinter at the sinter file's own prices; BF returns are poured into
    the mix at zero cost and count in chemistry only (the sinter model's rule).  Furnace: dry kg per tHM and Rs per tHM at the
    prices the furnace model used (price basis and fines credit included), then O&M, which makes the total the hot metal cost.
    """
    if not run or not run.get("ok"):
        return None, None
    s_, f = run["sinter"], run["furnace"]
    sdf = run["inputs"]["sdf"]
    bfr = sopt._bfr_name(sdf)
    blend = {m: float(v) for m, v in s_["blend"].items() if v > 1e-9}
    fresh = sum(v for m, v in blend.items() if m != bfr)
    srows = []
    for m, v in sorted(blend.items(), key=lambda kv: -kv[1]):
        price = 0.0 if m == bfr else float(sdf.loc[m, "Price_Rs_t"])
        srows.append({"Material": m, "Group": str(sdf.loc[m, "Group"]), "kg per t sinter": v,
                      "% of burden": (100.0 * v / fresh) if m != bfr else np.nan, "Rs per t sinter": v * price / 1000.0,
                      "Note": "chemistry only, zero cost" if m == bfr else ""})
    sin = pd.DataFrame(srows)
    df, res = f["df"], f["res"]
    total_kg = sum(float(v) for v in res[1].values())
    frows = []
    with mopt.session(f["cfg"]):
        for m, v in sorted(res[1].items(), key=lambda kv: -kv[1]):
            if v <= 1e-9:
                continue
            frows.append({"Material": m, "Group": str(df.loc[m, "Group"]).strip(), "kg per tHM": float(v),
                          "% of burden": 100.0 * float(v) / total_kg if total_kg > 0 else np.nan,
                          "Rs per tHM": float(v) * float(mopt.eff_price(df, m)) / 1000.0,
                          "Note": "from the sinter model" if m == run["row"] else ""})
    om = float(res[3].get("OM_Rs_tHM", 0.0))
    if om > 0:
        frows.append({"Material": "O&M", "Group": "O&M", "kg per tHM": np.nan, "% of burden": np.nan, "Rs per tHM": om, "Note": "operations and maintenance"})
    fur = pd.DataFrame(frows)
    return sin, fur
