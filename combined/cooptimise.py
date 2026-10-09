"""
Furnace-aware sinter recipe search: the sinter inputs that give the lowest cost of one tonne of hot metal.

Why it is needed.  The sinter model meets its spec tiers first and then picks the cheapest recipe among those that qualify.
Many recipes qualify (Fe anywhere in 52.5-54.5, MgO anywhere in its window, Al2O3 anywhere below its maximum ...), and the
cheapest one is not necessarily the best for the furnace: a little more Fe or MgO, or a little less Al2O3, can cut the
furnace's coke, slag and flux by more than it adds to the sinter's price.

What changes.  Nothing about the conditions.  Every spec tier, tolerance, stock rule, ore-ratio rule, heat balance, FeO band
and furnace rule stays as set.  Only the sinter model's LAST step (cost) gets an optional credit per unit of Fe, MgO and
Al2O3, equal to what the furnace model says that unit is worth (sinter.optimizer, keyword ``chem_credit``; with no credit the
engine behaves exactly as before).

How the search works (no Streamlit code here, so it can be tested on its own):
  1. Measure what the furnace pays: move the sinter row's Fe / MgO / Al2O3 / SiO2 / CaO up and down a little, re-solve the
     furnace, read Rs per tHM per point of chemistry.
  2. Turn that into credits (several strengths, since the response is lopsided and a full-strength credit may overshoot),
     re-run the combined loop for each, and keep the best candidate that is cheaper AND no worse on any sinter goal.
  3. Re-measure at the new point and repeat while it still pays.
  4. Verify the winner at full precision against the current run.  It is accepted only if it saves at least the smallest
     gain asked for (default Rs 50 per tHM, the loop's own noise level) and every sinter goal is met at least as well.
     Otherwise the current recipe is kept and the result says so.  The current recipe is always a candidate, so the answer
     can never be worse than today's.

An optional extra lever, OFF by default so that every condition stays as set: how strictly the sinter model keeps the ores in
proportion to their stock (the dashboard's inventory weight).  Switching it on does not change the answer silently: the result
says that it needed that rule relaxed, to what, and how far the ores then drift from their stock shares.  It is the one
condition that can leave room for savings, so it is worth knowing what it costs.
"""
import copy
import time

import numpy as np
import pandas as pd

from combined import loop as L
from sinter import optimizer as sopt

KEYS = ("Fe", "MgO", "Al2O3", "SiO2", "CaO")       # measured and shown
CREDIT_KEYS = ("Fe", "MgO", "Al2O3")                # credited in the sinter model's last step (SiO2 and CaO are tied to the basicity goal)
STEP = {"Fe": 0.25, "MgO": 0.15, "Al2O3": 0.15, "SiO2": 0.15, "CaO": 0.15}      # percentage points moved either way when measuring
STATUS_RANK = {"Optimal": 0, "Relaxed": 1}
GOAL_SLACK = 0.01                                    # a candidate may not miss any sinter goal by more than the current recipe plus this


# ---------------------------------------------------------------------------------------------- small helpers
def hm(run):
    return float(run["furnace"]["res"][2])


def _quick(inp, run, credit, plan, weight=None):
    """One combined loop in the same quick mode as the scans (0.5 % tolerance, 4 passes), started from the current run."""
    kw = dict(inp["kw"])
    if credit:
        kw["chem_credit"] = dict(credit)
    if weight is not None:
        kw["inventory_weight"] = float(weight)
    return L.run_loop(inp["sdf"], kw, inp["targets"], inp["om"], inp["mdf"], inp["mcfg"], plan, tol=0.005, max_pass=4, refine_steps=0,
                      start_t=run["sinter"]["t"], cap_t0=run["cap_t"], explain_final=False)


def today_weight(inp):
    return float(inp["kw"].get("inventory_weight", sopt.DEFAULT_INVENTORY_WEIGHT))


def max_drift(run):
    """Largest gap, in points, between an ore's share of the iron ore used and its share of the stock (sinter model's own table)."""
    t = (run["sinter"].get("report") or {}).get("ore_shares")
    try:
        return float(t["Drift pp"].abs().max()) if t is not None and len(t) else float("nan")
    except Exception:
        return float("nan")


def quality_ok(cand, base, targets, slack=GOAL_SLACK):
    """(ok, reason): the candidate's sinter is not in a worse status and misses no sinter goal by more than the current recipe does."""
    cs, bs = cand["sinter"], base["sinter"]
    if STATUS_RANK.get(cs["status"], 9) > STATUS_RANK.get(bs["status"], 9):
        return False, f"sinter status {cs['status']} is worse than {bs['status']}"
    cg, bg = sopt._spec_gaps(cs["achieved"], targets), sopt._spec_gaps(bs["achieved"], targets)
    worse = [k for k in cg if cg[k] > bg.get(k, 0.0) + slack]
    if worse:
        return False, "misses " + ", ".join(f"{k} by {cg[k]:.2f}" for k in worse) + " by more than today's recipe"
    return True, ""


# ---------------------------------------------------------------------------------------------- 1. what the furnace pays
def furnace_values(run, inp, keys=KEYS):
    """What the furnace pays for sinter chemistry, at this run's sinter and plan.
    Returns {key: {"value": Rs per tHM saved per +1 point (None if a furnace run failed), "up": slope above, "down": slope below,
    "lopsided": bool}}.  Positive value = more of it is better for the furnace.  Central difference over +/- STEP[key]."""
    f = run["furnace"]
    df, row, plan = f["df"], f["row"], float(run["plan"])
    c0 = float(f["res"][2])
    out = {}
    for k in keys:
        h = STEP[k]
        cost = {}
        for sgn in (1, -1):
            d = df.copy()
            d[k] = d[k].astype(float)
            d.loc[row, k] = float(d.loc[row, k]) + sgn * h
            res, _c = L.solve_furnace(d, inp["mcfg"], plan)
            cost[sgn] = float(res[2]) if res[0] == "Optimal" else None
        if cost[1] is None or cost[-1] is None:
            out[k] = {"value": None, "up": None, "down": None, "lopsided": False}
            continue
        up, dn = (c0 - cost[1]) / h, (cost[-1] - c0) / h             # Rs per tHM saved per point, moving up / moving down
        lop = (up * dn < 0 and max(abs(up), abs(dn)) > 20) or (min(abs(up), abs(dn)) > 20 and max(abs(up), abs(dn)) > 2 * min(abs(up), abs(dn)))
        out[k] = {"value": (cost[-1] - cost[1]) / (2 * h), "up": up, "down": dn, "lopsided": bool(lop)}
    return out


def credit_from_values(values, run, alpha, keys=CREDIT_KEYS):
    """Engine credit (Rs per tonne of sinter per kg/t of element) from furnace values (Rs per tHM per point), at strength alpha."""
    g0 = float(run["sinter"]["achieved"]["Gross_Sinter_kg_t"])      # the chemistry points are on the gross-sinter basis
    t_per_thm = float(run["furnace"]["kg"]) / 1000.0                  # tonnes of sinter the furnace uses per tHM
    if t_per_thm <= 0:
        return {}
    out = {}
    for k in keys:
        v = (values.get(k) or {}).get("value")
        if v is not None and abs(v) > 1e-9:
            out[k] = float(alpha) * (v / t_per_thm) / (g0 / 100.0)
    return out


# ---------------------------------------------------------------------------------------------- 2-4. the search
def search(run, *, rounds=3, alphas=(0.25, 0.5, 1.0), min_gain=50.0, ratio_levels=(), progress=None, start=None):
    """Find the sinter recipe with the lowest hot metal cost inside every existing condition.  `run` is a finished combined
    run that carries run['inputs'].  Returns a dict (see keys below); `accepted` says whether the new recipe replaces today's."""
    t0 = time.time()
    inp, plan = run["inputs"], float(run["plan"])
    w0 = today_weight(inp)
    levels = [w0] + [float(w) for w in ratio_levels if abs(float(w) - w0) > 1e-9]       # today's weight first; the rest only when asked for
    out = {"ok": False, "accepted": False, "message": "", "values": None, "trail": None, "base": run, "best": None, "credit": None,
           "gain": 0.0, "min_gain": float(min_gain), "knife": bool(run.get("jump")), "seconds": 0.0, "weight": w0, "weight_today": w0,
           "conditions_changed": False}
    if not run.get("ok"):
        out["message"] = "The combined run did not finish, so there is nothing to improve."
        return out
    total = 2 + rounds * (1 + len(levels) * (len(alphas) + (1 if len(levels) > 1 else 0)))
    step = {"n": 0}

    def tick(msg):
        step["n"] += 1
        if progress:
            progress(min(step["n"] / total, 0.99), msg)

    tick("Re-running today's recipe in quick mode")
    base_q = _quick(inp, run, None, plan)
    if not base_q.get("ok"):
        out["message"] = "The quick re-run of today's recipe failed: " + (base_q.get("message") or "")
        return out
    best, best_hm, best_credit, best_w = base_q, hm(base_q), None, w0
    trail = [{"Round": 0, "Stock-ratio weight": w0, "Strength": "today", "Hot metal Rs/tHM": best_hm, "Sinter Rs/t": base_q["sinter"]["vals"]["Price_Rs_t"],
              "Sinter status": base_q["sinter"]["status"], "Kept": "yes", "Note": "current recipe"}]
    cache = {}
    cur = start if (start is not None and start.get("ok")) else base_q          # where the furnace's value of sinter chemistry is first measured
    for rd in range(1, int(rounds) + 1):
        tick(f"Round {rd}: measuring what the furnace pays for sinter chemistry")
        vals = furnace_values(cur, inp)
        if rd == 1:
            out["values"] = vals
        round_best, round_hm, round_credit, round_w = None, best_hm, None, best_w
        for w in levels:
            strengths = tuple(alphas) if w == w0 else (0.0,) + tuple(alphas)    # a relaxed weight is also tried with no credit: its own saving
            for a in strengths:
                tick(f"Round {rd}: " + (f"credit strength {a:g}" if w == w0 else f"stock-ratio weight {w:g}, credit {a:g}"))
                credit = credit_from_values(vals, cur, a) if a else {}
                if a and not credit:
                    trail.append({"Round": rd, "Stock-ratio weight": w, "Strength": a, "Hot metal Rs/tHM": np.nan, "Sinter Rs/t": np.nan, "Sinter status": "",
                                  "Kept": "no", "Note": "furnace gave no value"})
                    continue
                key = (w, tuple(sorted((k, round(v, 3)) for k, v in credit.items())))
                if key not in cache:
                    cache[key] = _quick(inp, run, credit, plan, weight=None if w == w0 else w)
                cand = cache[key]
                if not cand.get("ok"):
                    trail.append({"Round": rd, "Stock-ratio weight": w, "Strength": a, "Hot metal Rs/tHM": np.nan, "Sinter Rs/t": np.nan, "Sinter status": "no recipe",
                                  "Kept": "no", "Note": "sinter or furnace run failed"})
                    continue
                ok_q, why = quality_ok(cand, base_q, inp["targets"])
                c_hm = hm(cand)
                kept = ok_q and c_hm < round_hm - 1e-6
                trail.append({"Round": rd, "Stock-ratio weight": w, "Strength": a, "Hot metal Rs/tHM": c_hm, "Sinter Rs/t": cand["sinter"]["vals"]["Price_Rs_t"],
                              "Sinter status": cand["sinter"]["status"], "Kept": "yes" if kept else "no",
                              "Note": ("" if ok_q else why) + (" (knife-edge)" if cand.get("jump") else "")})
                if kept:
                    round_best, round_hm, round_credit, round_w = cand, c_hm, credit, w
        if round_best is None:
            break
        gained = best_hm - round_hm
        best, best_hm, best_credit, best_w, cur = round_best, round_hm, round_credit, round_w, round_best
        if gained < min_gain / 2.0:
            break
    out["trail"] = pd.DataFrame(trail)
    if best_credit is None and best_w == w0:
        out["ok"] = True
        out["message"] = ("No sinter recipe inside the existing conditions gives a lower hot metal cost: the furnace would pay for more Fe or MgO, "
                          "but the pinned sinter goals and the ore mix held to the stock ratio leave no room to supply it. The current recipe stands."
                          + (" Tick the stock-ratio option to see what that one rule costs." if len(levels) == 1 else ""))
        out["seconds"] = time.time() - t0
        return out
    tick("Checking the best recipe at full precision")
    kw = dict(inp["kw"])
    if best_credit:
        kw["chem_credit"] = dict(best_credit)
    kw["inventory_weight"] = float(best_w)
    full = L.run_loop(inp["sdf"], kw, inp["targets"], inp["om"], inp["mdf"], inp["mcfg"], plan, tol=run.get("tol", 0.001), max_pass=run.get("max_pass", 8),
                      start_t=run["sinter"]["t"], cap_t0=run["cap_t"])
    out["seconds"] = time.time() - t0
    out["credit"], out["weight"] = best_credit, best_w
    if not full.get("ok"):
        out["ok"] = True
        out["message"] = "The best candidate did not survive the full-precision check (" + (full.get("message") or "no result") + "). The current recipe stands."
        return out
    full["inputs"] = dict(inp, kw=kw)
    ok_q, why = quality_ok(full, run, inp["targets"])
    gain = hm(run) - hm(full)
    out.update(ok=True, best=full, gain=gain, knife=bool(run.get("jump") or full.get("jump")), conditions_changed=abs(best_w - w0) > 1e-9)
    if not ok_q:
        out["message"] = f"The cheapest candidate {why}, so it is not allowed. The current recipe stands."
    elif gain < min_gain:
        out["message"] = (f"The best candidate saves only Rs {gain:,.0f} per tHM at full precision, below the Rs {min_gain:,.0f} needed to be sure it is "
                          f"real and not the loop's own noise. The current recipe stands.")
    else:
        out["accepted"] = True
        if out["conditions_changed"]:
            out["message"] = (f"Saves Rs {gain:,.0f} per tHM ({100 * gain / hm(run):.2f} %), but only if the sinter model holds the ores to their stock shares less strictly "
                              f"(inventory weight {w0:g} to {best_w:g}); the largest ore drift from stock share becomes {max_drift(full):.1f} points. "
                              f"That is a change of condition, so it is your call.")
        else:
            out["message"] = (f"A recipe inside every existing condition saves Rs {gain:,.0f} per tHM ({100 * gain / hm(run):.2f} %).")
        if out["knife"]:
            out["message"] += " One of the two runs sits on the furnace's knife-edge, so read the saving as an estimate."
    return out


# ---------------------------------------------------------------------------------------------- the single model
def tolerance_inputs(inp):
    """A copy of the combined inputs whose sinter spec windows are the plant-approved tolerance edges (never wider than the tolerance)."""
    tol = sopt._merge_tolerances(inp["kw"].get("tolerances"), inp["targets"])
    t = dict(inp["targets"])
    for k, v in inp["targets"].items():
        if k in tol:
            t[k] = min(float(v), float(tol[k])) if k.endswith("_min") else max(float(v), float(tol[k]))
    return dict(inp, targets=t)


def optimise(run, *, rounds=3, min_gain=50.0, relax_stock=False, use_tolerance=False, progress=None):
    """The single model: the sinter recipe with the lowest hot metal cost.  `run` is a finished combined run that carries run['inputs'].

    Default (use_tolerance False): every condition stays as set, only the objective is the hot metal cost (``search``).
    use_tolerance True: the sinter's spec windows are also opened up to the plant-approved tolerance edges, so the recipe may sit
    anywhere inside the tolerance and the furnace decides.  That changes a condition: the result says so and names the goals that
    leave their spec, and the comparison is always against today's recipe under today's conditions."""
    ratio_levels = (0.5, 0.0) if relax_stock else ()
    if not use_tolerance:
        res = search(run, rounds=rounds, min_gain=min_gain, ratio_levels=ratio_levels, progress=progress)
        res["tolerance_used"] = False
        return res
    t0 = time.time()
    out = {"ok": False, "accepted": False, "message": "", "values": None, "trail": None, "base": run, "best": None, "credit": None, "gain": 0.0,
           "min_gain": float(min_gain), "knife": bool(run.get("jump")), "seconds": 0.0, "weight": today_weight(run["inputs"]),
           "weight_today": today_weight(run["inputs"]), "conditions_changed": True, "tolerance_used": True}
    if not run.get("ok"):
        out["message"] = "The combined run did not finish, so there is nothing to improve."
        return out
    inp, plan = run["inputs"], float(run["plan"])
    tinp = tolerance_inputs(inp)
    wide = dict(run, inputs=tinp)
    if progress:
        progress(0.03, "Sinter with its spec windows opened to the approved tolerance, at full precision")
    kw = dict(inp["kw"])
    wide_only = L.run_loop(tinp["sdf"], kw, tinp["targets"], tinp["om"], tinp["mdf"], tinp["mcfg"], plan, tol=run.get("tol", 0.001), max_pass=run.get("max_pass", 8),
                           start_t=run["sinter"]["t"], cap_t0=run["cap_t"])
    if wide_only.get("ok"):
        wide_only["inputs"] = dict(tinp, kw=kw)
    sub = search(wide, rounds=rounds, alphas=(0.5, 1.0, 2.0, 4.0), min_gain=0.0, ratio_levels=ratio_levels, start=run,
                 progress=(lambda f, msg: progress(0.08 + 0.9 * f, msg)) if progress else None)
    cands = []
    if wide_only.get("ok"):
        cands.append((wide_only, "sinter windows opened to the approved tolerance"))
    if sub.get("best") is not None and sub["best"].get("ok"):
        cands.append((sub["best"], "tolerance windows plus the furnace's value of Fe, MgO and Al2O3"))
    out["values"], out["credit"] = sub.get("values"), sub.get("credit")
    trail = list(sub["trail"].to_dict("records")) if sub.get("trail") is not None else []
    if wide_only.get("ok"):
        trail.append({"Round": 0, "Stock-ratio weight": out["weight"], "Strength": "none", "Hot metal Rs/tHM": hm(wide_only), "Sinter Rs/t": wide_only["sinter"]["vals"]["Price_Rs_t"],
                      "Sinter status": wide_only["sinter"]["status"], "Kept": "yes", "Note": "tolerance windows, no furnace credit (full precision)"})
    out["trail"] = pd.DataFrame(trail) if trail else None
    out["seconds"] = time.time() - t0
    if not cands:
        out["ok"] = True
        out["message"] = "No sinter recipe could be found with the windows opened to the approved tolerance. The current recipe stands."
        return out
    best, how = min(cands, key=lambda c: hm(c[0]))
    gain = hm(run) - hm(best)
    out.update(ok=True, best=best, gain=gain, knife=bool(run.get("jump") or best.get("jump")), weight=sub.get("weight", out["weight"]))
    out["conditions_changed"] = True
    if gain < min_gain:
        out["message"] = (f"With the sinter allowed to use its approved tolerance the best recipe saves Rs {gain:,.0f} per tHM, below the Rs {min_gain:,.0f} needed "
                          f"to be sure it is real and not the loop's own noise. The current recipe stands.")
        return out
    out["accepted"] = True
    out["how"] = how
    out["message"] = f"Saves Rs {gain:,.0f} per tHM ({100 * gain / hm(run):.2f} %) found by {how}."
    if out["knife"]:
        out["message"] += " One of the two runs sits on the furnace's knife-edge, so read the saving as an estimate."
    return out


def tolerance_note(run, best):
    """'' or a sentence naming the sinter goals that sit outside today's spec (inside the approved tolerance) in the recipe found."""
    from combined import kpis as K                     # local import: kpis needs only the sinter engine, this keeps the module order simple
    cards = K.limit_cards(best, L.summary(best), spec_run=run)["sinter"]
    used = [f"{c['label'].replace('Sinter ', '')} {c['value'].strip()}" for c in cards if c["status"] == "warn"]
    bad = [c["label"] for c in cards if c["status"] == "bad"]
    if bad:
        return "Outside the approved tolerance: " + ", ".join(bad) + "."
    return ("Uses the approved tolerance on: " + ", ".join(used) + ".") if used else "Every sinter goal stays inside today's spec."


# ---------------------------------------------------------------------------------------------- reading a result
def values_table(values):
    """The furnace's value of sinter chemistry as a table: Rs per tHM per +1 point."""
    rows = []
    for k in KEYS:
        v = (values or {}).get(k)
        if not v or v["value"] is None:
            continue
        rows.append({"Sinter chemistry": k, "Furnace pays, Rs/tHM per +1 point": v["value"], "Moving up": v["up"], "Moving down": v["down"],
                     "Lopsided": "yes" if v["lopsided"] else "", "Credited": "yes" if k in CREDIT_KEYS else "no (tied to basicity goal)"})
    return pd.DataFrame(rows)


def _spec_text(k, t):
    return {"Fe": f"{sopt.FE_LOWER:g}-{sopt.FE_UPPER:g}", "SiO2": f"<= {t['SiO2_max']:g}", "Al2O3": f"<= {t['Al2O3_max']:g}",
            "CaO": f"{t['CaO_min']:g}-{t['CaO_max']:g}", "MgO": f"{t['MgO_min']:g}-{t['MgO_max']:g}",
            "Basicity": f"{t['Basicity_min']:g}-{t['Basicity_max']:g}"}.get(k, "")


def compare_tables(base, new, targets):
    """(headline, sinter recipe, sinter chemistry, furnace burden) comparing today's run with the optimised one."""
    sb, sn = L.summary(base), L.summary(new)
    head = pd.DataFrame([
        ("Hot metal cost, Rs/tHM", sb["hm_cost"], sn["hm_cost"]), ("Sinter price, Rs/t (raw + O&M)", sb["sinter_price"], sn["sinter_price"]),
        ("Sinter used, kg/tHM", sb["sinter_kg"], sn["sinter_kg"]), ("Sinter share of burden, %", sb["sinter_share"], sn["sinter_share"]),
        ("Coke, kg/tHM", sb["coke_kg"], sn["coke_kg"]), ("Fuel supplied, kg/tHM", sb["fuel_kg"], sn["fuel_kg"]),
        ("Slag, kg/tHM", sb["slag_kg"], sn["slag_kg"]), ("Flux, kg/tHM", sb["flux_kg"], sn["flux_kg"]),
        ("Largest ore drift from stock share, points", max_drift(base), max_drift(new))], columns=["Item", "Now", "Optimised"])
    head["Change"] = head["Optimised"] - head["Now"]

    def side(a, b, label, unit):
        names = sorted(set(a) | set(b), key=lambda m: -(max(a.get(m, 0.0), b.get(m, 0.0))))
        d = pd.DataFrame([{label: m, f"Now {unit}": a.get(m, 0.0), f"Optimised {unit}": b.get(m, 0.0)} for m in names])
        d[f"Change {unit}"] = d[f"Optimised {unit}"] - d[f"Now {unit}"]
        return d
    sin = side(sb["sinter_blend"], sn["sinter_blend"], "Material", "kg per t sinter")
    fur = side(sb["burden"], sn["burden"], "Material", "kg per tHM")
    ab, an_ = base["sinter"]["achieved"], new["sinter"]["achieved"]
    chem = pd.DataFrame([{"Sinter chemistry": k, "Spec": _spec_text(k, targets), "Now": float(ab[k]), "Optimised": float(an_[k]), "Change": float(an_[k]) - float(ab[k])}
                         for k in ("Fe", "SiO2", "Al2O3", "CaO", "MgO", "Basicity")])
    return head, sin, chem, fur
