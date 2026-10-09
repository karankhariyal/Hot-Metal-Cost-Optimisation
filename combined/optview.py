"""
What the "Optimise sinter inputs" page shows: the sinter burden and results and the furnace burden and results, today and (when a
search has found a saving) optimised, side by side, plus what moved the hot metal cost.  No Streamlit code here, so it can be
tested on its own.

Every function takes the two runs as plain combined-loop results (``run`` today, ``new`` optimised) and ``new=None`` means "show
today only", so the same page works before any search has been run and when the search keeps today's recipe.  Sinter and furnace
materials are kept in separate tables and never merged, even when the names match (KIOM).
"""
import numpy as np
import pandas as pd

from combined import kpis as K
from combined import loop as L

GROUP_LABEL = {"Sinter": "Sinter bought", "Iron_ore": "Iron ore", "Fuel_Coke": "Coke", "Fuel_NutCoke": "Nut coke", "Fuel_PCI": "PCI",
               "Flux": "Flux", "Minor": "Minor materials", "O&M": "Furnace O&M"}
GROUP_ORDER = list(GROUP_LABEL)


# ------------------------------------------------------------------------------------------ burdens
def _burden(a, b, kg_col, rs_col, kg_label, rs_label):
    """One burden table, today against optimised (b is None: today only).  Rs total ties to the model's own cost."""
    if b is None:
        d = a[["Material", "Group", kg_col, "% of burden", rs_col, "Note"]].copy().reset_index(drop=True)
        d.columns = ["Material", "Group", f"Now {kg_label}", "Now % of burden", f"Now {rs_label}", "Note"]
        out = d
    else:
        ia, ib = a.set_index("Material"), b.set_index("Material")
        names = list(dict.fromkeys(list(ia.index) + list(ib.index)))
        rows = []
        for m in names:
            ra = ia.loc[m] if m in ia.index else None
            rb = ib.loc[m] if m in ib.index else None
            row_g = (ra if ra is not None else rb)["Group"]
            note = str((ra if ra is not None else rb).get("Note", "") or "")
            is_om = m == "O&M"
            kg_a = (np.nan if is_om else float(ra[kg_col])) if ra is not None else (np.nan if is_om else 0.0)
            kg_b = (np.nan if is_om else float(rb[kg_col])) if rb is not None else (np.nan if is_om else 0.0)
            rs_a = float(ra[rs_col]) if ra is not None else 0.0
            rs_b = float(rb[rs_col]) if rb is not None else 0.0
            rows.append({"Material": m, "Group": row_g, f"Now {kg_label}": kg_a, f"Optimised {kg_label}": kg_b,
                         f"Change {kg_label}": kg_b - kg_a if not is_om else np.nan,
                         f"Now {rs_label}": rs_a, f"Optimised {rs_label}": rs_b, f"Change {rs_label}": rs_b - rs_a, "Note": note})
        out = pd.DataFrame(rows)
        out["_k"] = out[[f"Now {kg_label}", f"Optimised {kg_label}"]].max(axis=1).fillna(-1.0)
        out = out.sort_values("_k", ascending=False).drop(columns="_k").reset_index(drop=True)
    # total row: Rs always; kg without zero-cost BF returns and the O&M row
    cols = [c for c in out.columns if c.endswith(rs_label) or c.endswith(kg_label)]
    note = out["Note"].astype(str) if "Note" in out.columns else pd.Series([""] * len(out))
    counted = ~note.str.startswith("chemistry only") & (out["Material"] != "O&M")
    tot = {"Material": "Total", "Group": ""}
    for c in cols:
        tot[c] = float(out.loc[counted, c].sum()) if c.endswith(kg_label) else float(out[c].sum())
    if "Note" in out.columns:
        tot["Note"] = ""
    return pd.concat([out, pd.DataFrame([tot])], ignore_index=True)


def sinter_burden(run, new=None):
    sa, _ = L.compositions(run)
    sb = L.compositions(new)[0] if new is not None else None
    return _burden(sa, sb, "kg per t sinter", "Rs per t sinter", "kg/t", "Rs/t")


def furnace_burden(run, new=None):
    _, fa = L.compositions(run)
    fb = L.compositions(new)[1] if new is not None else None
    return _burden(fa, fb, "kg per tHM", "Rs per tHM", "kg/tHM", "Rs/tHM")


# ------------------------------------------------------------------------------------------ results
def _results(rows_a, rows_b):
    out = []
    for i, (item, unit, va) in enumerate(rows_a):
        r = {"Item": item, "Unit": unit, "Now": float(va)}
        if rows_b is not None:
            vb = float(rows_b[i][2])
            r.update({"Optimised": vb, "Change": vb - float(va)})
        out.append(r)
    return pd.DataFrame(out)


def _sinter_rows(run):
    sm = L.summary(run)
    k = K.sinter_kpis(sm, run["inputs"]["sdf"])
    return [("Sinter price (raw + O&M)", "Rs/t", sm["sinter_price"]), ("Sinter raw cost", "Rs/t", sm["sinter_raw"]),
            ("Fe", "%", k["fe"]), ("Al2O3", "%", k["al2o3"]), ("Basicity (CaO/SiO2)", "", k["basicity"]), ("SiO2", "%", k["sio2"]), ("MgO", "%", k["mgo"]),
            ("CaO", "%", sm["sinter_ach"].get("CaO", np.nan)),
            ("Coke used", "kg/t", k["coke_kg"]), ("Mill scale used", "kg/t", k["mill_kg"]),
            ("Iron ore (mill scale included)", "% of burden", k["ore_pct"]), ("Sinter made", "t", k["tonnes"])]


def _furnace_rows(run):
    sm = L.summary(run)
    k = K.furnace_kpis(sm, run["furnace"]["df"])
    return [("Hot metal cost", "Rs/tHM", sm["hm_cost"]), ("  of which raw materials", "Rs/tHM", sm["hm_raw"]), ("  of which furnace O&M", "Rs/tHM", sm["hm_om"]),
            ("Sinter in the sinter + ore mix", "%", k["sinter_share"]), ("Sinter used", "kg/tHM", k["sinter_kg"]),
            ("Coke", "kg/tHM", sm["coke_kg"]), ("Nut coke", "kg/tHM", sm["nut_kg"]), ("PCI", "kg/tHM", k["pci_kg"]), ("Fuel rate", "kg/tHM", sm["fuel_kg"]),
            ("Slag volume", "kg/tHM", k["slag_kg"]), ("Flux used", "kg/tHM", k["flux_kg"]), ("Iron ore used", "kg/tHM", k["ore_kg"]),
            ("Slag B2", "", sm["b2"]), ("Slag MgO", "%", sm["mgo_pct"]), ("Slag Al2O3", "%", sm["al2o3_pct"]), ("Hot metal S", "%", sm["hm_s"])]


def sinter_results(run, new=None):
    return _results(_sinter_rows(run), _sinter_rows(new) if new is not None else None)


def furnace_results(run, new=None):
    return _results(_furnace_rows(run), _furnace_rows(new) if new is not None else None)


def status_line(run, new=None):
    """('Sinter: Relaxed now, Relaxed optimised', 'Furnace: Optimal now, ...') for the captions."""
    def one(key):
        a = run["sinter"]["status"] if key == "sinter" else L.summary(run)["furnace_status"]
        if new is None:
            return f"{a}"
        b = new["sinter"]["status"] if key == "sinter" else L.summary(new)["furnace_status"]
        return f"{a} now, {b} optimised"
    return one("sinter"), one("furnace")


# ------------------------------------------------------------------------------------------ what moved the cost
def cost_bridge(run, new):
    """[(label, change in Rs/tHM)] by furnace cost group, then a remainder so the rows add up exactly to the change in hot metal cost."""
    sa, sb = L.summary(run), L.summary(new)
    rows = []
    for g in GROUP_ORDER:
        d = sb["groups"].get(g, {}).get("rs", 0.0) - sa["groups"].get(g, {}).get("rs", 0.0)
        if abs(d) > 1e-9 or g in sa["groups"] or g in sb["groups"]:
            rows.append((GROUP_LABEL[g], float(d)))
    rest = (sb["hm_cost"] - sa["hm_cost"]) - sum(d for _l, d in rows)
    if abs(rest) > 0.5:
        rows.append(("Other (price basis, fines credit)", float(rest)))
    return rows
