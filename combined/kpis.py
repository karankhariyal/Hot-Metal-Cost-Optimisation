"""
KPI cards and quality-limit cards for the Hot metal cost page.  No Streamlit code here, so it can be tested on its own.

Two KPI panels, one per plant:
  Sinter plant   per tonne of sinter: mill scale used, Fe, Al2O3, basicity, SiO2, MgO, coke used; then (unless show_ore=False, as on
                 the Hot metal cost page) the iron ore used (kg, % of the fresh burden, and which ores).
  Blast furnace  per tonne of hot metal: sinter % of the sinter + ore mix, PCI, slag volume, flux used (kg and which), then (with
                 fuel_tiles=True, as on the Hot metal cost page) coke used and nut coke used, then the iron ore used (kg, % of the
                 sinter + ore mix, and which ores).

Quality limits: one small card per limit with a coloured edge: green = inside the target, amber = outside the target but
inside the approved tolerance, red = outside the tolerance (or outside a furnace limit, which has no tolerance).

Every number comes from combined.loop.summary(run), so the cards agree with the rest of the page.
"""
import html
import re

from sinter import optimizer as sopt

EPS = 5e-3                      # the same slack the old bars used when deciding "inside"
_CLS = {"warn": "a", "bad": "r"}   # status -> CSS class (amber, red); inside the target has none


# ------------------------------------------------------------------------------------------ numbers
def _name_s(m):
    return str(m).title()


def _name_f(m):
    return str(m).replace("_", " ")


def _by_kg(pairs):
    return sorted(pairs, key=lambda kv: -kv[1])


def sinter_kpis(sm, sdf):
    """Numbers for the sinter panel (all per tonne of sinter unless the name says otherwise)."""
    blend = sm["sinter_blend"]
    bfr = sopt._bfr_name(sdf)
    ms = sopt._mill_scale_name(sdf)
    grp = sdf["Group"].astype(str)
    fresh = sum(v for m, v in blend.items() if m != bfr)
    ach = sm["sinter_ach"]
    tonnes = float(sm.get("sinter_t") or 0.0)
    mill = float(blend.get(ms, 0.0)) if ms else 0.0
    ores = _by_kg([(m, v) for m, v in blend.items() if m in sdf.index and grp[m] == "Iron_ore"])
    fuels = _by_kg([(m, v) for m, v in blend.items() if m in sdf.index and grp[m] == "Fuel"])
    ore_kg = sum(v for _m, v in ores)
    pct = (lambda x: 100.0 * x / fresh if fresh > 0 else 0.0)
    return {
        "tonnes": tonnes, "fresh": fresh,
        "mill_kg": mill, "mill_pct": pct(mill), "mill_t": mill * tonnes / 1000.0,
        "fe": ach.get("Fe"), "al2o3": ach.get("Al2O3"), "basicity": ach.get("Basicity"), "sio2": ach.get("SiO2"), "mgo": ach.get("MgO"),
        "coke_kg": sum(v for _m, v in fuels), "coke_pct": pct(sum(v for _m, v in fuels)), "coke_parts": fuels,
        "ore_kg": ore_kg, "ore_pct": pct(ore_kg), "ores": ores,
    }


def furnace_kpis(sm, fdf):
    """Numbers for the furnace panel (per tonne of hot metal)."""
    grp = lambda m: str(fdf.loc[m, "Group"]).strip()
    burden = sm["burden"]
    fluxes = _by_kg([(m, v) for m, v in burden.items() if grp(m) == "Flux"])
    ores = _by_kg([(m, v) for m, v in burden.items() if grp(m) in ("Iron_ore", "Minor")])
    ore_kg = sum(v for _m, v in ores)
    mix = float(sm["sinter_kg"]) + ore_kg
    return {
        "sinter_share": float(sm["sinter_share"]), "sinter_kg": float(sm["sinter_kg"]),
        "pci_kg": float(sm["pci_kg"]), "slag_kg": float(sm["slag_kg"]),
        "coke_kg": float(sm["coke_kg"]), "nut_kg": float(sm["nut_kg"]), "fuel_kg": float(sm["fuel_kg"]),
        "flux_kg": sum(v for _m, v in fluxes), "fluxes": fluxes,
        "ore_kg": ore_kg, "ore_pct": (100.0 * ore_kg / mix) if mix > 0 else 0.0, "ores": ores,
    }


# ------------------------------------------------------------------------------------------ quality limits
def parse_range(txt):
    """'52.5-54.5' -> (52.5, 54.5);  '<= 4.5' -> (None, 4.5);  '>= 2' -> (2, None)."""
    t = str(txt).replace(" ", "")
    m = re.match(r"^<=?([\d.]+)$", t)
    if m:
        return None, float(m.group(1))
    m = re.match(r"^>=?([\d.]+)$", t)
    if m:
        return float(m.group(1)), None
    m = re.match(r"^([\d.]+)-([\d.]+)$", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def limit_status(val, spec, tol=None):
    """'ok' inside the target; 'warn' outside it but inside the approved tolerance; 'bad' otherwise."""
    lo, hi = spec
    if (lo is None or val >= lo - EPS) and (hi is None or val <= hi + EPS):
        return "ok"
    if tol is not None:
        tlo, thi = tol
        if (tlo is None or val >= tlo - EPS) and (thi is None or val <= thi + EPS):
            return "warn"
    return "bad"


def _n(x, decimals):
    """A limit as short text: 2 -> '2.0' next to decimals (1.9 - 2.0), but 17 - 18.5 stays '17'."""
    t = f"{x:g}"
    return t + ".0" if decimals and "." not in t and "e" not in t else t


def _has_dec(*xs):
    return any(x is not None and float(x) != int(float(x)) for x in xs)


def _target_text(spec, tol=None):
    lo, hi = spec
    d = _has_dec(lo, hi, *(tol or ()))
    if lo is not None and hi is not None:
        return f"target {_n(lo, d)} \u2013 {_n(hi, d)}"
    if hi is not None:
        return f"max {_n(hi, d)}"
    if lo is not None:
        return f"min {_n(lo, d)}"
    return ""


def limit_card(label, val, spec, tol=None, unit="", dec=2):
    """One limit as a dict the page draws as a card."""
    status = limit_status(val, spec, tol)
    lo, hi = spec
    text = _target_text(spec, tol)
    if status == "warn":
        tlo, thi = tol
        d = _has_dec(lo, hi, tlo, thi)
        if hi is not None and val > hi + EPS and thi is not None:
            text += f" \u00b7 allowed up to {_n(thi, d)}"
        elif lo is not None and val < lo - EPS and tlo is not None:
            text += f" \u00b7 allowed down to {_n(tlo, d)}"
    elif status == "bad":
        text += " \u00b7 outside the limit" if tol is None else " \u00b7 outside the tolerance"
    return {"label": label, "value": f"{val:.{dec}f}{unit}", "text": text, "status": status}


def limit_cards(run, sm, spec_run=None):
    """{'sinter': [...], 'furnace': [...]}: the sinter spec goals and the furnace slag / sinter-share limits of this run.
    `spec_run`: judge the sinter values against THAT run's spec and approved tolerance (used when a recipe was found with wider windows,
    so the cards still show how much of the approved tolerance it uses)."""
    sin = []
    gt = run["sinter"]["report"].get("goal_table")
    gs = (spec_run or run)["sinter"]["report"].get("goal_table")
    if gt is not None and len(gt) and gs is not None and len(gs):
        for lab in ("Fe", "Basicity", "MgO", "Al2O3", "SiO2"):
            r, rs_ = gt[gt["Goal"] == lab], gs[gs["Goal"] == lab]
            if len(r) and len(rs_):
                r, rs_ = r.iloc[0], rs_.iloc[0]
                sin.append(limit_card(f"Sinter {'basicity' if lab == 'Basicity' else lab}", float(r["Achieved"]), parse_range(rs_["Spec"]),
                                      parse_range(rs_["Approved tolerance"]), "" if lab == "Basicity" else " %", 2))
    cfg = run["furnace"]["cfg"]
    fur = [limit_card("Slag basicity (B2)", sm["b2"], (cfg.basicity_min, cfg.basicity_max), None, "", 3),
           limit_card("Slag MgO", sm["mgo_pct"], (cfg.mgo_min_pct, cfg.mgo_max_pct), None, " %"),
           limit_card("Slag Al2O3", sm["al2o3_pct"], (cfg.al2o3_min_pct, cfg.al2o3_max_pct), None, " %"),
           limit_card("Sinter share", sm["sinter_share"], (cfg.sinter_min * 100, cfg.sinter_max * 100), None, " %", 1)]
    return {"sinter": sin, "furnace": fur}


def limit_summary(cards):
    """('ok'|'warn'|'bad', one sentence) over every card."""
    allc = cards["sinter"] + cards["furnace"]
    if not allc:
        return "ok", "No limits to show."
    n = len(allc)
    ok = sum(c["status"] == "ok" for c in allc)
    warn = [c["label"] for c in allc if c["status"] == "warn"]
    bad = [c["label"] for c in allc if c["status"] == "bad"]
    if not warn and not bad:
        return "ok", f"All {n} limits are inside their target."
    parts = [f"{ok} of {n} inside their target"]
    if warn:
        parts.append(", ".join(warn) + " outside the target but inside the approved tolerance")
    if bad:
        parts.append(", ".join(bad) + " outside the limit")
    return ("bad" if bad else "warn"), " \u00b7 ".join(parts) + "."


# ------------------------------------------------------------------------------------------ html (one line each: no blank lines or indents, so markdown never turns it into code)
def _tile(label, value, unit="", sub="", span=1):
    u = f"<small>{html.escape(unit)}</small>" if unit else ""
    s = f"<div class='s'>{sub}</div>" if sub else ""
    st_ = f" style='grid-column:span {span}'" if span > 1 else ""
    return f"<div class='kp-t'{st_}><div class='l'>{html.escape(label)}</div><div class='v'>{value}{u}</div>{s}</div>"


def _parts(pairs, namer, dec=1):
    return " \u00b7 ".join(f"{html.escape(namer(m))} {v:,.{dec}f}" for m, v in pairs) or "none"


def _chips(pairs, namer):
    return "".join(f"<span class='kp-chip'>{html.escape(namer(m))}<b>{v:,.1f}</b></span>" for m, v in pairs) or "<span class='kp-chip'>none</span>"


def _panel(title, sub, color, cols, tiles, ore_head, ore_line, chips):
    ore = (f"<div class='kp-ore'><div class='l'>{html.escape(ore_head)}</div><div class='ov'>{ore_line}</div><div class='kp-chips'>{chips}</div></div>"
           if ore_head else "")
    return (f"<div class='kp' style='--c:{color};--n:{cols}'><div class='kp-h'>{html.escape(title)}<small>{html.escape(sub)}</small></div>"
            f"<div class='kp-grid'>{''.join(tiles)}</div>{ore}</div>")


def sinter_panel_html(k, color, show_ore=True):
    """show_ore=False drops the iron-ore block (the Hot metal cost page does not show it for the sinter plant)."""
    tiles = [
        _tile("Mill scale used", f"{k['mill_kg']:,.1f}", "kg/t", f"{k['mill_pct']:.1f}% of burden \u00b7 {k['mill_t']:,.0f} t in all"),
        _tile("Fe achieved", f"{k['fe']:.2f}", "%"),
        _tile("Al2O3", f"{k['al2o3']:.2f}", "%"),
        _tile("Basicity", f"{k['basicity']:.2f}", "CaO/SiO2"),
        _tile("SiO2", f"{k['sio2']:.2f}", "%"),
        _tile("MgO", f"{k['mgo']:.2f}", "%"),
        _tile("Coke used", f"{k['coke_kg']:,.1f}", "kg/t", _parts(k["coke_parts"], _name_s) + f" \u00b7 {k['coke_pct']:.1f}% of burden", span=2),
    ]
    line = f"{k['ore_pct']:.1f}% of burden <span class='dim'>\u00b7 {k['ore_kg']:,.1f} kg per t sinter</span>"
    return _panel("Sinter plant", f"per tonne of sinter \u00b7 {k['tonnes']:,.0f} t made", color, 4, tiles,
                  "Iron ore used (mill scale included)" if show_ore else None, line, _chips(k["ores"], _name_s))


def furnace_panel_html(k, color, fuel_tiles=False):
    """fuel_tiles=True adds Coke used and Nut coke used (kg/tHM) after the four standard tiles (Hot metal cost page)."""
    tiles = [
        _tile("Sinter in the sinter + ore mix", f"{k['sinter_share']:.1f}", "%", f"{k['sinter_kg']:,.0f} kg sinter per tHM"),
        _tile("PCI used", f"{k['pci_kg']:,.0f}", "kg/tHM"),
        _tile("Slag volume", f"{k['slag_kg']:,.0f}", "kg/tHM"),
        _tile("Flux used", f"{k['flux_kg']:,.1f}", "kg/tHM", _parts(k["fluxes"], _name_f)),
    ]
    if fuel_tiles:
        tiles += [_tile("Coke used", f"{k['coke_kg']:,.1f}", "kg/tHM"), _tile("Nut coke used", f"{k['nut_kg']:,.1f}", "kg/tHM")]
    line = f"{k['ore_pct']:.1f}% of sinter + ore <span class='dim'>\u00b7 {k['ore_kg']:,.1f} kg per tHM</span>"
    return _panel("Blast furnace", "per tonne of hot metal", color, 2, tiles, "Iron ore used", line, _chips(k["ores"], _name_f))


def limits_html(cards):
    def grid(items):
        return "<div class='ql-grid'>" + "".join(
            f"<div class='ql {_CLS.get(c['status'], '')}'><div class='l'>{html.escape(c['label'])}</div>"
            f"<div class='v'>{c['value']}</div><div class='s'>{html.escape(c['text'])}</div></div>" for c in items) + "</div>"
    out = ""
    if cards["sinter"]:
        out += "<div class='ql-h'>Sinter</div>" + grid(cards["sinter"])
    if cards["furnace"]:
        out += "<div class='ql-h'>Blast furnace and slag</div>" + grid(cards["furnace"])
    return out
