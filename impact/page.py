"""
The "Sinter to hot metal impact" model's pages: change anything in the sinter making and see what it does to the cost of one tonne of
hot metal.  The combined run is the baseline and is never changed; every change is applied to copies of its inputs.
"""
import datetime as dt
import inspect
import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from combined import cooptimise as C
from combined import kpis as K
from combined import loop as L
from combined import optview as O
from combined.page import W, PLOT_W
from impact import engine as E
from shared.theme import PALETTE, stage_card, alert, chip
from sinter import optimizer as sopt

PAGES = ["Baseline & changes", "Impact", "Optimise sinter inputs", "What each condition costs", "Impact scenarios", "Export"]
UP, DOWN = "#E5675D", "#5CC48E"


def S():
    return st.session_state


def _init():
    S().setdefault("imp_scen", [])
    S().setdefault("imp_reset", 0)


def _tag(run):
    return f"{run['time']:%H%M%S%f}_{S()['imp_reset']}"


def _open_combined():
    S()["ws"] = "combined"
    S()["route_combined"] = "Hot metal cost"
    st.rerun()


def _need_run():
    run = S().get("cmb_run")
    if not run or not run.get("ok"):
        st.markdown(alert("check", "This model starts from the combined run. Run it first on <b>Combined cost model &rsaquo; Hot metal cost</b>; nothing in the three models is changed here."), unsafe_allow_html=True)
        if st.button("Open Hot metal cost", key="imp_open_cmb"):
            _open_combined()
        return None
    return run


def _waterfall(labels, deltas, start_label, start, end_label, end, height=360):
    """Cost bridge: start, one bar per step (red = dearer, green = cheaper), end.  The axis is zoomed on the movement."""
    x = [start_label] + list(labels) + [end_label]
    levels = [start]
    for d in deltas:
        levels.append(levels[-1] + float(d))
    lo, hi = min(levels + [end]), max(levels + [end])
    span = max(hi - lo, 40.0)
    fig = go.Figure(go.Waterfall(x=x, measure=["absolute"] + ["relative"] * len(deltas) + ["total"], y=[start] + [float(d) for d in deltas] + [0],
                                 text=[f"{start:,.0f}"] + [f"{d:+,.0f}" for d in deltas] + [f"{end:,.0f}"], textposition="outside",
                                 increasing=dict(marker=dict(color=UP)), decreasing=dict(marker=dict(color=DOWN)), totals=dict(marker=dict(color=PALETTE["hm"])),
                                 connector=dict(line=dict(color=PALETTE["line"]))))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False,
                      font=dict(color=PALETTE["text"]), yaxis=dict(range=[lo - span * 0.6, hi + span * 0.35], gridcolor=PALETTE["line"], title="Rs per tHM"))
    return fig



# ------------------------------------------------------------------------------------------ baseline and changes
def _collect(run):
    """The change set from the widgets on the Baseline & changes page.  Nothing is applied until Run impact."""
    inp, tag = run["inputs"], _tag(run)
    sdf, bv, ch = inp["sdf"], E.base_values(inp), {}
    t1, t2, t3, t4 = st.tabs(["Prices & stock", "O&M & return sinter", "Spec windows & tolerances", "Coke band & stock rule"])
    with t1:
        bfr = sopt._bfr_name(sdf)
        mats = [m for m in sdf.index if float(sdf.loc[m, "Available_Tonnes"]) > 0 and m != bfr]
        df = pd.DataFrame({"Material": mats, "Price now Rs/t": [float(sdf.loc[m, "Price_Rs_t"]) for m in mats], "Price change %": 0.0,
                           "Stock now t": [float(sdf.loc[m, "Available_Tonnes"]) for m in mats], "Stock change %": 0.0})
        ed = st.data_editor(df, key=f"imp_pe_{tag}", hide_index=True, disabled=["Material", "Price now Rs/t", "Stock now t"], **W,
                            column_config={"Price now Rs/t": st.column_config.NumberColumn(format="%.0f"), "Stock now t": st.column_config.NumberColumn(format="%.0f"),
                                           "Price change %": st.column_config.NumberColumn(min_value=-90.0, max_value=300.0, step=1.0, format="%.1f"),
                                           "Stock change %": st.column_config.NumberColumn(min_value=-100.0, max_value=900.0, step=5.0, format="%.1f")})
        ch["price_pct"] = {r["Material"]: float(r["Price change %"]) for _, r in ed.iterrows() if abs(float(r["Price change %"])) > 1e-12}
        ch["stock_pct"] = {r["Material"]: float(r["Stock change %"]) for _, r in ed.iterrows() if abs(float(r["Stock change %"])) > 1e-12}
        st.caption("Type a percentage change in the editable columns. BF returns are left out: they cost nothing and count in chemistry only.")
    with t2:
        a, a2, b, c = st.columns(4)
        om = a.number_input("Sinter O&M, Rs per t", value=float(bv["om"]), step=10.0, key=f"imp_om_{tag}")
        fom = a2.number_input("Furnace O&M, Rs per tHM", value=float(bv["furnace_om"]), step=50.0, min_value=0.0, max_value=100_000.0, key=f"imp_fom_{tag}")
        iol = b.number_input("IOL Fines, % of charged mix", value=float(bv["iol_pct"]), step=0.5, min_value=0.0, max_value=30.0, key=f"imp_iol_{tag}")
        bfr_pct = c.number_input("BFR, % of charged mix", value=float(bv["bfr_pct"]), step=0.5, min_value=0.0, max_value=40.0, key=f"imp_bfr_{tag}")
        if abs(om - bv["om"]) > 1e-9:
            ch["om"] = float(om)
        if abs(fom - bv["furnace_om"]) > 1e-9:
            ch["furnace_om"] = float(fom)
        if abs(iol - bv["iol_pct"]) > 1e-9:
            ch["iol_pct"] = float(iol)
        if abs(bfr_pct - bv["bfr_pct"]) > 1e-9:
            ch["bfr_pct"] = float(bfr_pct)
    with t3:
        c1, c2 = st.columns(2)
        sp = pd.DataFrame({"Spec": [k.replace("_", " ") for k in E.SPEC_KEYS], "Base": [float(inp["targets"][k]) for k in E.SPEC_KEYS]})
        sp["Changed"] = sp["Base"]
        bt = E.base_tolerances(inp)
        tl = pd.DataFrame({"Tolerance": [k.replace("_", " ") for k in E.TOL_KEYS], "Base": [float(bt[k]) for k in E.TOL_KEYS]})
        tl["Changed"] = tl["Base"]
        with c1:
            es = st.data_editor(sp, key=f"imp_sp_{tag}", hide_index=True, disabled=["Spec", "Base"], **W,
                                column_config={"Base": st.column_config.NumberColumn(format="%.3f"), "Changed": st.column_config.NumberColumn(format="%.3f", step=0.05)})
        with c2:
            et = st.data_editor(tl, key=f"imp_tl_{tag}", hide_index=True, disabled=["Tolerance", "Base"], **W,
                                column_config={"Base": st.column_config.NumberColumn(format="%.3f"), "Changed": st.column_config.NumberColumn(format="%.3f", step=0.05)})
        ch["targets"] = {k: float(r["Changed"]) for k, (_, r) in zip(E.SPEC_KEYS, es.iterrows()) if abs(float(r["Changed"]) - float(r["Base"])) > 1e-9}
        ch["tolerances"] = {k: float(r["Changed"]) for k, (_, r) in zip(E.TOL_KEYS, et.iterrows()) if abs(float(r["Changed"]) - float(r["Base"])) > 1e-9}
        st.caption("A tolerance is never tighter than its spec: the sinter model widens it to the spec if you cross. Fe has a fixed spec of 52.5 to 54.5.")
    with t4:
        a, b, c = st.columns(3)
        cmin = a.number_input("Coke minimum, kg per t sinter", value=float(bv["coke_min"]), step=1.0, key=f"imp_cmin_{tag}")
        cmax = b.number_input("Coke maximum, kg per t sinter", value=float(bv["coke_max"]), step=1.0, key=f"imp_cmax_{tag}")
        w = c.slider("Inventory weight (1 = ores held to their stock shares)", 0.0, 1.0, float(bv["inventory_weight"]), 0.05, key=f"imp_w_{tag}")
        if abs(cmin - bv["coke_min"]) > 1e-9:
            ch["coke_min"] = float(cmin)
        if abs(cmax - bv["coke_max"]) > 1e-9:
            ch["coke_max"] = float(cmax)
        if abs(w - bv["inventory_weight"]) > 1e-9:
            ch["inventory_weight"] = float(w)
    return {k: v for k, v in ch.items() if v not in ({}, None)}


def page_changes(sns=None):
    _init()
    st.markdown("<h1>Baseline &amp; changes</h1><div class='sub'>Set what changes in the sinter making, then press Run impact. The combined run is the baseline and stays as it is; "
                "your changes are applied to copies of its inputs.</div>", unsafe_allow_html=True)
    run = _need_run()
    if not run:
        return
    sm = L.summary(run)
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(stage_card("Baseline hot metal cost", PALETTE["hm"], f"{sm['hm_cost']:,.0f}", " Rs/tHM", [f"Run {run['time']:%I:%M %p}".replace(" 0", " ")]), unsafe_allow_html=True)
    c2.markdown(stage_card("Sinter price", PALETTE["sinter"], f"{sm['sinter_price']:,.0f}", " Rs/t", [f"Status {sm['sinter_status']}"]), unsafe_allow_html=True)
    c3.markdown(stage_card("Sinter used", PALETTE["furnace"], f"{sm['sinter_kg']:,.0f}", " kg/tHM", [f"Share of burden {sm['sinter_share']:.1f} %"]), unsafe_allow_html=True)
    c4.markdown(stage_card("Plan", PALETTE["grey1"], f"{sm['plan']:,.0f}", " tHM", [f"{sm['plan_cost_cr']:,.2f} crore"]), unsafe_allow_html=True)
    ch = _collect(run)
    S()["imp_ch"] = ch
    desc = E.describe(run["inputs"], ch)
    st.markdown(f"<div class='sect'>Changes set<small>{len(desc)} item(s) across {len(E.groups_changed(ch))} lever group(s)</small></div>", unsafe_allow_html=True)
    if len(desc):
        st.dataframe(desc, hide_index=True, **W)
    else:
        st.caption("Nothing changed yet.")
    a, b, _sp = st.columns([1.2, 1, 3])
    go_run = a.button("Run impact", type="primary", key="imp_run", **W)
    if b.button("Reset changes", key="imp_reset_btn", **W):
        S()["imp_reset"] += 1
        S().pop("imp_res", None)
        st.rerun()
    if go_run:
        if not ch:
            st.markdown(alert("check", "No change is set. Edit a price, a spec, the coke band or any other lever above, then run."), unsafe_allow_html=True)
            return
        bar = st.progress(0.0, text="Starting")
        res = E.run_impact(run, ch, progress=lambda f, msg: bar.progress(min(max(f, 0.0), 1.0), text=msg))
        bar.empty()
        res["tag_run"], res["time"] = run["time"], dt.datetime.now()
        S()["imp_res"] = res
        S()["route_impact"] = "Impact"
        st.rerun()


# ------------------------------------------------------------------------------------------ impact
def page_impact(sns=None):
    _init()
    st.markdown("<h1>Impact</h1><div class='sub'>What the changes do to the cost of one tonne of hot metal, and how the effect travels from sinter making to the furnace.</div>", unsafe_allow_html=True)
    run = _need_run()
    if not run:
        return
    res = S().get("imp_res")
    if not res:
        st.markdown(alert("check", "No impact run yet. Set changes on <b>Baseline &amp; changes</b> and press <b>Run impact</b>."), unsafe_allow_html=True)
        if st.button("Open Baseline & changes", key="imp_open_chg"):
            S()["route_impact"] = "Baseline & changes"
            st.rerun()
        return
    if res.get("tag_run") != run["time"]:
        st.markdown(alert("check", "This result belongs to an earlier combined run. Press <b>Run impact</b> again on Baseline &amp; changes."), unsafe_allow_html=True)
        return
    st.dataframe(res["describe"], hide_index=True, **W)
    if not res["ok"]:
        st.markdown(alert("short", res["message"]), unsafe_allow_html=True)
        return
    base, new, total = res["base"], res["new"], res["total"]
    hb, hn = C.hm(base), C.hm(new)
    plan = float(base["plan"])
    sk = {"Optimal": "ok", "Relaxed": "check"}.get(new["sinter"]["status"], "short")
    lk = "ok" if new["converged"] and not new.get("jump") else "check"
    st.markdown(chip(sk, f"Sinter: {new['sinter']['status']}") + chip(lk, "Loop: " + ("converged" if new["converged"] and not new.get("jump") else "knife-edge" if new.get("jump") else "not converged")), unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    c1.markdown(stage_card("Baseline", PALETTE["grey1"], f"{hb:,.0f}", " Rs/tHM", [f"Plan cost {hb * plan / 1e7:,.2f} crore"]), unsafe_allow_html=True)
    c2.markdown(stage_card("With the changes", PALETTE["hm"], f"{hn:,.0f}", " Rs/tHM", [f"Plan cost {hn * plan / 1e7:,.2f} crore"], hero=True), unsafe_allow_html=True)
    c3.markdown(stage_card("Change", UP if total > 0 else DOWN, f"{total:+,.0f}", " Rs/tHM", [f"{100 * total / hb:+.2f} % · {total * plan / 1e7:+,.2f} crore over the plan"]), unsafe_allow_html=True)
    if abs(total) < 50:
        st.markdown(alert("check", "A change of about Rs 50 per tHM or less can come from the loop's own tolerance, so read it as no measurable effect."), unsafe_allow_html=True)

    st.markdown("<div class='sect'>Which change did it<small>levers applied one after another, in this order</small></div>", unsafe_allow_html=True)
    lv = res["levers"]
    if lv is not None and len(lv):
        d = lv.dropna(subset=["Change Rs/tHM"])
        st.plotly_chart(_waterfall(list(d["Lever"]), list(d["Change Rs/tHM"]), "Baseline", hb, "With the changes", hn), key="imp_wf_lev", **PLOT_W)
        st.dataframe(lv.round(1), hide_index=True, **W)
        st.caption("Each lever is added on top of the ones above it and the combined loop re-run, so the slices add up to the total. A different order gives different slices when levers interact (a price rise matters more once stock is short); the total never changes. "
                   + (res["lever_note"] or ""))
    else:
        g = res["groups"][0] if res["groups"] else ""
        st.caption(f"One lever group changed ({E.LABEL.get(g, g)}), so it accounts for the whole change.")

    st.markdown("<div class='sect'>How it travels from sinter making to the furnace<small>the furnace's Sinter row moved one field at a time</small></div>", unsafe_allow_html=True)
    ch_ = res["chain"]
    d = ch_[ch_["Change Rs/tHM"].abs() > 0.05]
    if len(d):
        st.plotly_chart(_waterfall(list(d["Step"]), list(d["Change Rs/tHM"]), "Baseline", hb, "With the changes", hn), key="imp_wf_chain", **PLOT_W)
    st.dataframe(ch_.round(2), hide_index=True, column_config={"Base": st.column_config.NumberColumn(format="%.2f"), "Changed": st.column_config.NumberColumn(format="%.2f")}, **W)
    st.caption("Price first (what the sinter costs per tonne), then the chemistry the furnace sees, then how much sinter the plant can supply. Each step re-solves the furnace, "
               "so a step's value includes everything the furnace does about it (more or less coke, slag and flux). The order is fixed; the total does not depend on it.")

    st.markdown("<div class='sect'>What the furnace did about it<small>per tonne of hot metal</small></div>", unsafe_allow_html=True)
    st.dataframe(res["head"].round(2), hide_index=True, column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ("Now", "Optimised", "Change")}, **W)
    st.markdown("<div class='sect'>Sinter inputs<small>baseline and with the changes</small></div>", unsafe_allow_html=True)
    chem = res["chem"].rename(columns={"Now": "Baseline", "Optimised": "With changes"})
    st.dataframe(chem, hide_index=True, column_config={c: st.column_config.NumberColumn(format="%.3f") for c in ("Baseline", "With changes", "Change")}, **W)
    sin = res["sin"].rename(columns={c: c.replace("Now", "Baseline").replace("Optimised", "With changes") for c in res["sin"].columns})
    st.dataframe(sin, hide_index=True, column_config={c: st.column_config.NumberColumn(format="%.1f") for c in sin.columns if c != "Material"}, **W)
    with st.expander("Furnace burden, per tonne of hot metal"):
        fur = res["fur"].rename(columns={c: c.replace("Now", "Baseline").replace("Optimised", "With changes") for c in res["fur"].columns})
        st.dataframe(fur, hide_index=True, column_config={c: st.column_config.NumberColumn(format="%.1f") for c in fur.columns if c != "Material"}, **W)
    st.caption("The numbers rest on the furnace's plant thumb rules and on the sinter tolerances, which are placeholders except SiO2 6.2. "
               "The model has no sinter strength or reducibility terms.")



# ------------------------------------------------------------------------------------------ optimise sinter inputs
def _opt_xlsx(res, head, chem, sin, fur, vals_tbl, trail):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        head.to_excel(xw, sheet_name="Summary", index=False)
        chem.to_excel(xw, sheet_name="Sinter chemistry", index=False)
        sin.to_excel(xw, sheet_name="Sinter inputs", index=False)
        fur.to_excel(xw, sheet_name="Furnace burden", index=False)
        vals_tbl.to_excel(xw, sheet_name="Furnace value of chemistry", index=False)
        trail.to_excel(xw, sheet_name="Search trail", index=False)
        pd.DataFrame({"Note": [res["message"]]}).to_excel(xw, sheet_name="Result", index=False)
    return buf.getvalue()


def _dec(item, unit):
    """Decimals for one row of a results table."""
    if unit.startswith("Rs") or unit == "t":
        return 0
    if unit.startswith("kg") or unit == "% of burden" or item.startswith("Sinter in the"):
        return 1
    if item == "Hot metal S" or item.startswith("Slag B2"):
        return 3
    return 2


def _fmt(v, d, sign=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if sign:
        return "" if abs(v) < 0.5 * 10 ** -d else f"{v:+,.{d}f}"
    return f"{v:,.{d}f}"


def _cost_colour(v):
    return f"color:{UP}" if isinstance(v, (int, float)) and v > 0.5 else (f"color:{DOWN}" if isinstance(v, (int, float)) and v < -0.5 else "")


def _burden_view(df):
    """A burden table as a styled frame: kg with one decimal, Rs with none, changes signed, zero changes blank, Rs changes red up / green down."""
    fm = {}
    for c in df.columns:
        if df[c].dtype.kind == "f":
            d = 0 if "Rs" in c else 1
            fm[c] = (lambda v, d=d: _fmt(v, d, sign=True)) if c.startswith("Change") else (lambda v, d=d: _fmt(v, d))
    sty = df.style.format(fm)
    for c in df.columns:
        if c.startswith("Change") and "Rs" in c:
            sty = sty.map(_cost_colour, subset=[c])
    return sty


def _results_view(df):
    sty = df.style
    cols = [c for c in ("Now", "Optimised", "Change") if c in df.columns]
    for i, r in df.iterrows():
        d = _dec(r["Item"], r["Unit"])
        for c in cols:
            sty = sty.format((lambda v, d=d, c=c: _fmt(v, d, sign=(c == "Change"))), subset=pd.IndexSlice[[i], [c]])
    return sty


def _plants(run, new, now_only, spec_run=None):
    """The sinter plant and the blast furnace, each with its burden and its results: today, and optimised when there is a saving."""
    nw = None if now_only else new
    both = nw is not None
    show = nw if both else run
    sm = L.summary(show)
    cards = K.limit_cards(show, sm, spec_run=spec_run if both else None)
    s_stat, f_stat = O.status_line(run, nw)
    tag = "Now | Optimised" if both else "today"
    if now_only:
        st.markdown("<div class='sect'>Today's recipe<small>run the search to compare it with an optimised one</small></div>", unsafe_allow_html=True)

    st.markdown(f"<div class='sect' style='color:{PALETTE['sinter']}'>Sinter plant<small>burden and results, {tag}</small></div>", unsafe_allow_html=True)
    st.markdown("<div class='small'>Burden: dry kg and Rs per tonne of sinter. BF returns are poured in at zero cost, so they do not count in the total.</div>", unsafe_allow_html=True)
    st.dataframe(_burden_view(O.sinter_burden(run, nw)), hide_index=True, **W)
    r1, r2 = st.columns([1.35, 1])
    with r1:
        st.markdown("<div class='small'>Results</div>", unsafe_allow_html=True)
        st.dataframe(_results_view(O.sinter_results(run, nw)), hide_index=True, **W)
        st.caption(f"Sinter status: {s_stat}.")
    with r2:
        st.markdown(f"<div class='small'>Quality limits, {'optimised recipe' if both else 'today'}</div>", unsafe_allow_html=True)
        st.markdown(K.limits_html({"sinter": cards["sinter"], "furnace": []}), unsafe_allow_html=True)

    st.markdown(f"<div class='sect' style='color:{PALETTE['furnace']}'>Blast furnace<small>burden and results, {tag}</small></div>", unsafe_allow_html=True)
    st.markdown("<div class='small'>Burden: dry kg and Rs per tonne of hot metal, from the furnace's own materials. The total is the hot metal cost.</div>", unsafe_allow_html=True)
    st.dataframe(_burden_view(O.furnace_burden(run, nw)), hide_index=True, **W)
    f1, f2 = st.columns([1.35, 1])
    with f1:
        st.markdown("<div class='small'>Results</div>", unsafe_allow_html=True)
        st.dataframe(_results_view(O.furnace_results(run, nw)), hide_index=True, **W)
        st.caption(f"Furnace status: {f_stat}.")
    with f2:
        st.markdown(f"<div class='small'>Quality limits, {'optimised recipe' if both else 'today'}</div>", unsafe_allow_html=True)
        st.markdown(K.limits_html({"sinter": [], "furnace": cards["furnace"]}), unsafe_allow_html=True)

    if both:
        rows = [(lab, d) for lab, d in O.cost_bridge(run, nw) if abs(d) >= 0.5]
        st.markdown("<div class='sect'>What moved the hot metal cost<small>change in Rs per tHM by cost group; the bars add up to the total change</small></div>", unsafe_allow_html=True)
        if rows:
            fig = go.Figure(go.Bar(x=[d for _l, d in rows], y=[lab for lab, _d in rows], orientation="h", marker_color=[UP if d > 0 else DOWN for _l, d in rows],
                                   text=[f"{d:+,.0f}" for _l, d in rows], textposition="outside", cliponaxis=False,
                                   hovertemplate="%{y}: %{x:+,.0f} Rs/tHM<extra></extra>"))
            fig.update_layout(height=60 + 42 * len(rows), margin=dict(l=0, r=40, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              font=dict(color=PALETTE["text"], family="Source Sans 3, sans-serif"), yaxis=dict(autorange="reversed"), xaxis=dict(zeroline=True, zerolinecolor="#3A4854", showgrid=False))
            st.plotly_chart(fig, **PLOT_W)
        total = C.hm(nw) - C.hm(run)
        st.caption(f"Total change {total:+,.0f} Rs per tHM. A costlier sinter is fine when the furnace saves more than it costs.")



def _result_block(run, res):
    """The message and the Now / Optimised / Saving cards for a search result.  Returns (new run or None, head, chem, sin, fur, result usable)."""
    fresh_res = bool(res and res.get("fp") == run["fp"] and res.get("run_time") == run["time"])
    ok_res = bool(fresh_res and res["ok"])
    new = head = chem = sin = fur = None
    if res and not fresh_res:
        st.markdown(alert("check", "This result belongs to an earlier combined run. Run the search again to start from the current one. "
                                   "Below is the current run as it stands today."), unsafe_allow_html=True)
    elif res and not res["ok"]:
        st.markdown(alert("short", res["message"]), unsafe_allow_html=True)
    elif res:
        st.markdown(alert("ok" if res["accepted"] and not res["conditions_changed"] else "check", res["message"]), unsafe_allow_html=True)
        if res["accepted"]:
            new = res["best"]
            head, sin, chem, fur = C.compare_tables(run, new, run["inputs"]["targets"])      # kept for the Excel download
            if res.get("tolerance_used"):
                st.markdown(f"<div class='notice w'>{C.tolerance_note(run, new)} Today's spec windows were opened to the approved tolerance for this search, which is a change of condition.</div>", unsafe_allow_html=True)
        plan = run["plan"]
        hb = C.hm(run)
        hn = C.hm(new) if new is not None else hb
        gain = hb - hn if new is not None else 0.0
        cand = res.get("gain")
        c1, c2, c3 = st.columns(3)
        c1.markdown(stage_card("Hot metal cost now", PALETTE["hm"], f"{hb:,.0f}", " Rs/tHM", [f"Plan cost {hb * plan / 1e7:,.2f} crore for {plan:,.0f} tHM"]), unsafe_allow_html=True)
        c2.markdown(stage_card("Optimised", PALETTE["sinter"], f"{hn:,.0f}", " Rs/tHM",
                               [f"Plan cost {hn * plan / 1e7:,.2f} crore" if new is not None else "Today's recipe stands"], hero=True), unsafe_allow_html=True)
        c3.markdown(stage_card("Saving", PALETTE["furnace"], f"{gain:,.0f}", " Rs/tHM",
                               [f"{100 * gain / hb:.2f} % · {gain * plan / 1e7:,.2f} crore over the plan" if new is not None
                                else ("No saving accepted" + (f" (best candidate Rs {cand:,.0f})" if cand is not None else ""))]), unsafe_allow_html=True)
    return new, head, chem, sin, fur, ok_res


def page_optimise(sns=None):
    _init()
    st.markdown("<h1>Optimise sinter inputs</h1><div class='sub'>Find the sinter recipe that gives the lowest cost of one tonne of hot metal, from the materials available. "
                "Every sinter spec, tolerance, stock rule and furnace rule stays as set. Only the last, cost step of the sinter model learns what the furnace pays for Fe, MgO and Al2O3.</div>",
                unsafe_allow_html=True)
    run = _need_run()
    if not run:
        return
    a, b = st.columns(2)
    min_gain = a.number_input("Smallest saving to accept, Rs per tHM", 0.0, 1000.0, 50.0, 10.0, key="cmb_op_gain",
                              help="Below this the result is treated as the loop's own noise and today's recipe is kept.")
    rounds = b.slider("Search rounds", 1, 4, 3, key="cmb_op_rounds", help="Each round re-measures what the furnace pays and tries again from the best recipe so far.")
    relax = st.checkbox("Also test holding the ores less strictly to their stock shares (this changes a condition)", key="cmb_op_relax",
                        help="Off by default, so every condition stays as set. On, the search also tries inventory weights 0.5 and 0 and reports what that rule costs; "
                             "a result that needs it says so and leaves the decision to you.")
    n_runs = rounds * (4 if not relax else 10) + 2
    st.caption(f"Up to about {n_runs} runs of the sinter-furnace loop, roughly {max(1, n_runs * 2 // 60)}-{max(2, n_runs * 4 // 60 + 1)} minute(s). It starts from the run on the Hot metal cost page.")
    if st.button("Find the lowest hot metal cost", type="primary", key="cmb_op_run"):
        bar = st.progress(0.0, text="Starting")
        res = C.search(run, rounds=int(rounds), min_gain=float(min_gain), ratio_levels=(0.5, 0.0) if relax else (), progress=lambda f, msg: bar.progress(f, text=msg))
        bar.empty()
        res["fp"], res["run_time"] = run["fp"], run["time"]
        S()["cmb_opt"] = res
    res = S().get("cmb_opt")
    new, head, chem, sin, fur, ok_res = _result_block(run, res)
    _plants(run, new, now_only=not ok_res, spec_run=run if ok_res and res.get("tolerance_used") else None)
    if not ok_res:
        return
    _details(run, res, head, chem, sin, fur)


def _details(run, res, head, chem, sin, fur):
    """What the furnace pays, the search trail, the Excel of an accepted result and the closing cautions."""
    st.markdown("<div class='sect'>What the furnace pays for sinter chemistry<small>measured at today's sinter, one point at a time</small></div>", unsafe_allow_html=True)
    vt = C.values_table(res["values"])
    if len(vt):
        st.dataframe(vt.round(1), hide_index=True, **W)
    st.caption("Rs per tHM saved for each extra point of that chemistry in the sinter. Fe, MgO and Al2O3 are credited in the sinter model's cost step; SiO2 and CaO are shown only, "
               "because the basicity goal ties them. Lopsided means the furnace pays differently above and below today's value, because a slag limit binds there.")
    with st.expander("Search trail"):
        st.dataframe(res["trail"].round(2), hide_index=True, **W)
        st.caption("Each row is a quick loop run (0.5 % tolerance). Strength is the share of the furnace's value given to the sinter model as a credit; weight is the inventory weight "
                   "(1 holds the ores to their stock shares as strictly as the sinter model allows). The best row is re-run at full precision before it is accepted.")
    if res["accepted"]:
        if st.button("Prepare Excel of this result", key="cmb_op_xl"):
            S()["cmb_op_xlsx"] = (_opt_xlsx(res, head, chem, sin, fur, vt, res["trail"]), f"Optimised_sinter_inputs_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
        ex = S().get("cmb_op_xlsx")
        if ex:
            st.download_button("Download workbook", ex[0], ex[1], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="cmb_op_dl")
    st.caption("The model has no sinter strength or reducibility terms, so check a recommended recipe against plant experience before using it. "
               "A saving of about Rs 50 per tHM or less can come from the loop's own tolerance, and near the sinter plant's ceiling the furnace's demand can flip between two burdens.")




# ------------------------------------------------------------------------------------------ what each condition costs
def _verdict(v):
    """One plain word for a hot metal change in Rs per tHM (the loop's own noise is about Rs 50)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "Not measured"
    if v < -50:
        return "Saves money"
    if v > 50:
        return "Makes it dearer"
    return "No effect" if abs(v) < 0.5 else "Within noise"


def page_conditions(sns=None):
    _init()
    st.markdown("<h1>What each condition costs</h1><div class='sub'>Relax ONE rule at a time, everything else as set, and see what it would be worth in hot metal cost. "
                "It prices the rules; it does not tell you which are safe to relax.</div>", unsafe_allow_html=True)
    with st.expander("How to read this page", expanded=True):
        st.markdown(
            "<div class='small' style='line-height:1.65'>"
            "<b>What it does.</b> Your sinter model has rules (basicity 1.9&ndash;2.0, MgO 2.2&ndash;2.4, SiO2 at most 5.8 and so on). Each row below loosens <b>one</b> rule by the Amount shown, "
            "re-runs the sinter and furnace models, and reports what happens to the cost of one tonne of hot metal. Everything else stays as it is.<br>"
            "<b>The number that matters.</b> <i>Hot metal change Rs/tHM</i>: <b>negative</b> = relaxing the rule makes hot metal cheaper; <b>positive</b> = dearer; "
            "<b>about zero</b> = your recipe never touches that limit, so loosening it changes nothing. Anything within &plusmn;50 is the loop's own noise.<br>"
            "<b>Why it can come out dearer.</b> The sinter model picks the recipe that is cheapest for the <i>sinter plant</i>, not for the furnace. When a rule is wider it may switch to a different recipe "
            "(look at the sinter price and chemistry columns), and that recipe can cost the furnace more.<br>"
            "<b>Verdict column.</b> <i>No effect</i>, <i>Within noise</i>, <i>Saves money</i> (more than Rs 50 cheaper) or <i>Makes it dearer</i> (more than Rs 50 dearer)."
            "</div>", unsafe_allow_html=True)
    run = _need_run()
    if not run:
        return
    inp, tag = run["inputs"], _tag(run)
    conds = E.default_conditions(inp)
    df = pd.DataFrame({"Include": True, "Condition": [c["name"] for c in conds], "How": [c["unit"] for c in conds], "Amount": [c["step"] for c in conds]})
    ed = st.data_editor(df, key=f"imp_cond_{tag}", hide_index=True, disabled=["Condition", "How"], **W,
                        column_config={"Amount": st.column_config.NumberColumn(format="%.2f", min_value=0.0, step=0.05)})
    chosen = [dict(c, step=float(r["Amount"])) for c, (_, r) in zip(conds, ed.iterrows()) if bool(r["Include"])]
    st.caption(f"{len(chosen)} runs of the sinter-furnace loop in quick mode, about {max(1, len(chosen) * 2 // 60 + 1)} minute(s). Amounts are editable; for the stock rule the amount is the inventory weight to test (1 = held as strictly as today).")
    if st.button("Measure what each condition costs", type="primary", key="imp_cond_run") and chosen:
        bar = st.progress(0.0, text="Starting")
        tbl, bh = E.condition_sweep(run, chosen, progress=lambda f, msg: bar.progress(min(max(f, 0.0), 1.0), text=msg))
        bar.empty()
        S()["imp_sweep"] = {"tbl": tbl, "base_hm": bh, "run_time": run["time"], "time": dt.datetime.now()}
    sw = S().get("imp_sweep")
    if not sw:
        return
    if sw["run_time"] != run["time"]:
        st.markdown(alert("check", "This table belongs to an earlier combined run. Press the button again."), unsafe_allow_html=True)
        return
    tbl = sw["tbl"]
    if tbl is None or not len(tbl):
        st.markdown(alert("short", "The quick re-run of the baseline failed, so nothing could be measured."), unsafe_allow_html=True)
        return
    col = "Hot metal change Rs/tHM"
    good = tbl[tbl[col] < -50]
    if len(good):
        top = good.iloc[0]
        st.markdown(alert("ok", f"Keeping <b>{top['Condition']}</b> costs about Rs {-top[col]:,.0f} per tHM ({top['Relaxed']}). "
                                f"{len(good)} condition(s) are worth more than Rs 50 per tHM; the rest cost nothing measurable here."), unsafe_allow_html=True)
    else:
        st.markdown(alert("check", "No single condition is worth more than Rs 50 per tHM to relax on these inputs: the recipe is set by stock and price, not by the rules."), unsafe_allow_html=True)
    worse = tbl[tbl[col] > 50]
    if len(worse):
        st.markdown(alert("check", "Relaxing <b>" + ", ".join(worse["Condition"]) + "</b> raises the hot metal cost: with the wider rule the sinter model chooses a different recipe, "
                                   "cheapest for the sinter plant but dearer for the furnace. Keep these rules as they are."), unsafe_allow_html=True)
    d = tbl.dropna(subset=[col]).iloc[::-1]
    fig = go.Figure(go.Bar(x=d[col], y=d["Condition"], orientation="h", marker_color=[DOWN if v < -50 else (UP if v > 50 else PALETTE["grey2"]) for v in d[col]]))
    fig.update_layout(height=max(280, 34 * len(d)), margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=PALETTE["text"]), xaxis=dict(title="Change in hot metal cost, Rs per tHM (negative = relaxing it saves money)", gridcolor=PALETTE["line"]))
    st.plotly_chart(fig, key="imp_cond_fig", **PLOT_W)
    shown = tbl.round(2).copy()
    shown.insert(2, "Verdict", [_verdict(v) for v in tbl[col]])
    st.dataframe(shown, hide_index=True, **W)
    st.caption("Each row is a quick run (0.5 % tolerance), so differences of about Rs 50 per tHM or less are noise. Relaxing a rule only shows the price of keeping it: a spec the furnace or the sinter plant really needs stays. "
               "Your sinter tolerances are placeholders except SiO2 6.2, so a saving that depends on one of them needs the plant's confirmation. The sinter's chemistry columns show what it becomes when that rule is relaxed.")


# ------------------------------------------------------------------------------------------ scenarios
def page_scenarios(sns=None):
    _init()
    st.markdown("<h1>Impact scenarios</h1><div class='sub'>Keep an impact result, set different changes, run again, compare. Up to 8 per session.</div>", unsafe_allow_html=True)
    res, scen = S().get("imp_res"), S()["imp_scen"]
    run = S().get("cmb_run")
    if res and res.get("ok") and run and res.get("tag_run") == run.get("time"):
        a, b = st.columns([3, 1], vertical_alignment="bottom") if "vertical_alignment" in inspect.signature(st.columns).parameters else st.columns([3, 1])
        name = a.text_input("Name for this result", value=f"Impact {len(scen) + 1}", key=f"imp_sc_name_{len(scen)}")
        if b.button("Save this result", type="primary", key="imp_sc_save", **W) and len(scen) < 8:
            scen.append({"name": name, "time": dt.datetime.now().strftime("%H:%M"), "hm_base": C.hm(res["base"]), "hm_new": C.hm(res["new"]), "total": res["total"],
                         "describe": res["describe"], "head": res["head"], "levers": res["levers"]})
            st.rerun()
    else:
        st.markdown(alert("check", "Run an impact first (Baseline &amp; changes), then save it here."), unsafe_allow_html=True)
    if not scen:
        st.caption("No impact results saved yet.")
        return
    names = [s["name"] for s in scen]
    pick = st.multiselect("Compare (up to 3)", names, default=names[-3:], max_selections=3, key="imp_sc_pick")
    sel = [s for s in scen if s["name"] in pick]
    if sel:
        tbl = pd.DataFrame({s["name"]: [f"{s['hm_base']:,.0f}", f"{s['hm_new']:,.0f}", f"{s['total']:+,.0f}", f"{100 * s['total'] / s['hm_base']:+.2f} %",
                                        f"{len(s['describe'])} item(s)"] for s in sel},
                           index=["Baseline Rs/tHM", "With changes Rs/tHM", "Change Rs/tHM", "Change %", "Changes"])
        st.dataframe(tbl, **W)
        for s in sel:
            with st.expander(f"{s['name']}: what was changed"):
                st.dataframe(s["describe"], hide_index=True, **W)
    if st.button("Clear all saved results", key="imp_sc_clear"):
        scen.clear()
        st.rerun()


# ------------------------------------------------------------------------------------------ export
def page_export(sns=None):
    _init()
    st.markdown("<h1>Export</h1><div class='sub'>One workbook with the changes, the impact, both bridges and, when you have run them, the condition prices and saved results. Nothing downloads on its own.</div>", unsafe_allow_html=True)
    res, run = S().get("imp_res"), S().get("cmb_run")
    sw, scen = S().get("imp_sweep"), S()["imp_scen"]
    have_res = bool(res and res.get("ok") and run and res.get("tag_run") == run.get("time"))
    if not have_res and not (sw and sw.get("tbl") is not None and len(sw["tbl"])):
        st.markdown(alert("check", "Run an impact (Baseline &amp; changes) or the condition prices first."), unsafe_allow_html=True)
        return
    if st.button("Prepare workbook", type="primary", key="imp_exp"):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            if have_res:
                pd.DataFrame([("Baseline hot metal cost, Rs/tHM", C.hm(res["base"])), ("With changes, Rs/tHM", C.hm(res["new"])), ("Change, Rs/tHM", res["total"]),
                              ("Change, %", 100 * res["total"] / C.hm(res["base"])), ("Plan, tHM", res["base"]["plan"]), ("Plan cost change, crore Rs", res["total"] * res["base"]["plan"] / 1e7)],
                             columns=["Item", "Value"]).to_excel(xw, sheet_name="Summary", index=False)
                res["describe"].to_excel(xw, sheet_name="Changes", index=False)
                if res["levers"] is not None:
                    res["levers"].to_excel(xw, sheet_name="By lever", index=False)
                res["chain"].to_excel(xw, sheet_name="By path", index=False)
                res["head"].to_excel(xw, sheet_name="Furnace response", index=False)
                res["chem"].to_excel(xw, sheet_name="Sinter chemistry", index=False)
                res["sin"].to_excel(xw, sheet_name="Sinter inputs", index=False)
                res["fur"].to_excel(xw, sheet_name="Furnace burden", index=False)
            if sw and sw.get("tbl") is not None and len(sw["tbl"]):
                sw["tbl"].to_excel(xw, sheet_name="Condition prices", index=False)
            if scen:
                pd.DataFrame([{"Result": s["name"], "Baseline Rs/tHM": s["hm_base"], "With changes Rs/tHM": s["hm_new"], "Change Rs/tHM": s["total"],
                               "Changes": "; ".join(f"{r.Item} {r.Changed}" for r in s["describe"].itertuples())[:250]} for s in scen]).to_excel(xw, sheet_name="Saved results", index=False)
        S()["imp_export"] = (buf.getvalue(), f"Sinter_to_hot_metal_impact_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
    ex = S().get("imp_export")
    if ex:
        st.download_button("Download workbook", ex[0], ex[1], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="imp_dl")
        st.caption(f"{ex[1]} · {len(ex[0]) / 1024:,.0f} KB")


def render(page, sns, mns):
    {"Baseline & changes": lambda: page_changes(sns), "Impact": lambda: page_impact(sns), "Optimise sinter inputs": lambda: page_optimise(sns),
     "What each condition costs": lambda: page_conditions(sns), "Impact scenarios": lambda: page_scenarios(sns), "Export": lambda: page_export(sns)}.get(page, lambda: page_changes(sns))()
