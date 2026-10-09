"""
The combined cost model's pages.  Landing page = "Hot metal cost".  All numbers come from combined.loop.run_loop.
"""
import copy
import dataclasses
import datetime as dt
import inspect
import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from shared import nsrun, editor
from shared.theme import PALETTE, stage_card, arrow_cell, alert, chip
from combined import loop as L
from combined import handoff as H
from combined import kpis as K
from sinter import optimizer as sopt
from mbf import optimiser as mopt
from mbf import analytics as an

PAGES = ["Hot metal cost", "Uploads & shared settings", "Basicity scan", "Price drivers", "Saved scenarios", "Export", "Glossary"]
W = {"width": "stretch"} if "width" in inspect.signature(st.button).parameters else {"use_container_width": True}
PLOT_W = {"width": "stretch"} if "width" in inspect.signature(st.plotly_chart).parameters else {"use_container_width": True}
GROUP_COLOR = {"Sinter": PALETTE["sinter"], "Iron_ore": PALETTE["grey1"], "Fuel_Coke": PALETTE["grey3"], "Fuel_NutCoke": PALETTE["grey2"],
               "Fuel_PCI": PALETTE["hm"], "Flux": PALETTE["flux"], "Minor": "#B79CE6", "O&M": "#7B8A96"}
GROUP_LABEL = {"Sinter": "Sinter (from sinter model)", "Iron_ore": "Iron ore", "Fuel_Coke": "Coke", "Fuel_NutCoke": "Nut coke",
               "Fuel_PCI": "PCI", "Flux": "Flux (limestone, dolomite, quartzite)", "Minor": "Minor materials", "O&M": "Furnace O&M"}
GROUP_ORDER = ["Sinter", "Iron_ore", "Fuel_Coke", "Fuel_NutCoke", "Fuel_PCI", "Flux", "Minor", "O&M"]


def rs(x, d=0):
    return f"₹{x:,.{d}f}"


def S():
    return st.session_state


def _init():
    for k, v in (("cmb_plan", 9000.0), ("cmb_tol", 0.1), ("cmb_maxpass", 8), ("cmb_view", "Engineer view"),
                 ("cmb_scen", []), ("cmb_actual", 0.0), ("cmb_budget", 0.0)):
        S().setdefault(k, v)


# ------------------------------------------------------------------------------------------ running
def execute_run(sns, label="Run both models"):
    """Run the combined loop on the dashboards' current inputs and keep the result."""
    problems = H.sinter_setup_problems(sns)
    if problems:
        st.markdown(alert("short", "The sinter model is not set up yet: " + "; ".join(problems)), unsafe_allow_html=True)
        return None
    sdf, kw, targets, om = H.sinter_inputs(sns)
    ms = nsrun.state("mbf")
    mdf, mcfg = ms.df.copy(), ms.cfg
    plan = float(S()["cmb_plan"])
    bar = st.progress(0.0, text="Starting")

    def prog(n, msg):
        bar.progress(min(n / max(int(S()["cmb_maxpass"]), 1), 0.99), text=msg)

    run = L.run_loop(sdf, kw, targets, om, mdf, mcfg, plan, tol=S()["cmb_tol"] / 100.0, max_pass=int(S()["cmb_maxpass"]), progress=prog)
    bar.empty()
    run["inputs"] = H.inputs_snapshot(sdf, kw, targets, om, mdf, mcfg, plan)
    run["fp"] = (H.fp_sinter(sdf, kw, targets, om), H.fp_mbf(mdf, mcfg), plan)
    run["time"] = dt.datetime.now()
    prev = S().get("cmb_run")
    if prev and prev.get("ok"):
        S()["cmb_prev"] = prev
    S()["cmb_run"] = run
    return run


def current_fp(sns):
    sdf, kw, targets, om = H.sinter_inputs(sns)
    ms = nsrun.state("mbf")
    return (H.fp_sinter(sdf, kw, targets, om), H.fp_mbf(ms.df, ms.cfg), float(S()["cmb_plan"]))


# ------------------------------------------------------------------------------------------ small renderers
def kv_card(title, color, pairs):
    rows = "".join(f"<div class='mrow' style='grid-template-columns:1fr auto'><span>{k}</span><span>{v}</span></div>" for k, v in pairs)
    return (f"<div class='stage' style='--c:{color};min-height:0'><div class='nm'>{title}</div>{rows}</div>")


def summary_tables(run, sm):
    """Group table for 'Where the money goes' (kg and Rs per tHM by group)."""
    rows, tot_kg, tot_rs = [], 0.0, 0.0
    for g in GROUP_ORDER:
        if g in sm["groups"]:
            kg, r = sm["groups"][g]["kg"], sm["groups"][g]["rs"]
            rows.append((g, kg, r))
            tot_kg += kg
            tot_rs += r
    return rows, tot_kg, tot_rs


def _comp_table(df, kg_col, rs_col, total_label, total_rs, total_kg=None, note=None):
    """One burden table: kg, share of the burden and money, with a total row that ties to the model's cost."""
    d = df.copy()
    tot = {"Material": total_label, "Group": "", kg_col: total_kg if total_kg is not None else d[kg_col].sum(skipna=True),
           "% of burden": 100.0, rs_col: total_rs, "Note": ""}
    d = pd.concat([d, pd.DataFrame([tot])], ignore_index=True)
    d["Group"] = d["Group"].map(lambda g: GROUP_LABEL.get(g, str(g).replace("_", " ")))
    cc = {kg_col: st.column_config.NumberColumn(format="%.1f"), "% of burden": st.column_config.NumberColumn("% of burden", format="%.1f"),
          rs_col: st.column_config.NumberColumn(format="%.0f")}
    st.dataframe(d, hide_index=True, column_config=cc, **W)
    if note:
        st.caption(note)


def composition_section(run):
    """The two burdens of the run: what goes into one tonne of sinter and what goes into one tonne of hot metal."""
    sin, fur = L.compositions(run)
    if sin is None:
        return
    sm = L.summary(run)
    st.markdown("<div class='sect'>Burden composition<small>sinter plant and blast furnace, dry kg of each material</small></div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown(f"<div class='small'><b style='color:{PALETTE['sinter']}'>Sinter burden</b> per tonne of sinter · {sm['sinter_status']} · made at {sm['sinter_t']:,.0f} t</div>", unsafe_allow_html=True)
        fresh = float(sin.loc[sin["Note"] == "", "kg per t sinter"].sum())
        _comp_table(sin, "kg per t sinter", "Rs per t sinter", "Raw-material cost", sm["sinter_raw"], total_kg=float(sin["kg per t sinter"].sum()),
                    note=f"Fresh burden {fresh:,.0f} kg per t sinter. BF returns are poured in at zero cost and count in chemistry only. "
                         f"Raw cost ₹{sm['sinter_raw']:,.0f} + O&M ₹{sm['sinter_om']:,.0f} = ₹{sm['sinter_price']:,.0f} per t, the price the furnace pays.")
    with c2:
        st.markdown(f"<div class='small'><b style='color:{PALETTE['furnace']}'>Blast furnace burden</b> per tonne of hot metal · {sm['furnace_status']}</div>", unsafe_allow_html=True)
        _comp_table(fur, "kg per tHM", "Rs per tHM", "Hot metal cost", sm["hm_cost"], total_kg=float(fur["kg per tHM"].sum(skipna=True)),
                    note=("Raw materials ₹%s + O&M ₹%s = ₹%s per tHM. " % (f"{sm['hm_raw']:,.0f}", f"{sm['hm_om']:,.0f}", f"{sm['hm_cost']:,.0f}") if sm["hm_om"] > 0
                          else "Furnace O&M is 0: set it under Uploads & shared settings or MBF > Inputs. ") + "Sinter is the row from the sinter model above.")


# ------------------------------------------------------------------------------------------ landing page
def page_hot_metal(sns):
    _init()
    run = S().get("cmb_run")
    stale = bool(run and run.get("ok") and run["fp"] != current_fp(sns))
    c = st.columns([2.9, 1.15, 1.6, 1.0, 1.0, 1.25], vertical_alignment="center") if "vertical_alignment" in inspect.signature(st.columns).parameters else st.columns([2.9, 1.15, 1.6, 1.0, 1.0, 1.25])
    c[0].markdown("<div class='hdr-title'>Combined result</div><div class='sub' style='margin:0'>Sinter plant feeds the blast furnace</div>", unsafe_allow_html=True)
    c[1].number_input("Plan, tHM", min_value=100.0, max_value=1_000_000.0, step=500.0, format="%.0f", key="cmb_plan", help="Hot metal planned over the sinter horizon. Sets the sinter tonnage the furnace needs and the stock caps.")
    c[2].radio("View", ["Management view", "Engineer view"], key="cmb_view", horizontal=True, label_visibility="collapsed")
    last_run_slot = c[3].empty()
    if c[4].button("Export results", **W):
        S()["route_combined"] = "Export"
        st.rerun()
    go_run = c[5].button("Run both models", type="primary", **W)
    st.markdown("<div class='sub'>Change any sinter input and run: the new sinter cost and chemistry go straight into the furnace model, and the cost of one tonne of hot metal updates.</div>", unsafe_allow_html=True)
    if go_run:
        run = execute_run(sns)
        stale = False
    last_run_slot.markdown(f"<div class='small'>{'Last run ' + run['time'].strftime('%I:%M %p').lstrip('0').lower() if run else 'Not run yet'}</div>", unsafe_allow_html=True)
    if not run:
        st.markdown(alert("check", "No run yet. Press <b>Run both models</b>. The sinter model runs first, its result replaces the furnace table's Sinter row, "
                                   "and the loop repeats until the sinter tonnage the furnace needs settles."), unsafe_allow_html=True)
        return
    if not run["ok"]:
        st.markdown(alert("short", run["message"]), unsafe_allow_html=True)
        if run["passes"]:
            with st.expander("What happened, pass by pass", expanded=True):
                st.dataframe(pd.DataFrame(run["passes"]), hide_index=True, **W)
        return
    sm = L.summary(run)
    sdf = run["inputs"]["sdf"]

    # chips
    sk = {"Optimal": "ok", "Relaxed": "check"}.get(sm["sinter_status"], "short")
    lk = "ok" if run["converged"] and not run.get("jump") else "check"
    st.markdown(chip(sk, f"Sinter: {sm['sinter_status']}") + chip("ok", f"Furnace: {sm['furnace_status']}")
                + chip(lk, f"Loop: {'converged' if run['converged'] and not run.get('jump') else ('on a knife-edge' if run.get('jump') else 'not converged')} in {sm['passes']} passes")
                + (chip("check", "Inputs changed since this run: press Run") if stale else ""), unsafe_allow_html=True)

    blend = sm["sinter_blend"]
    bfr = sopt._bfr_name(sdf)
    fresh = sum(v for m, v in blend.items() if m != bfr)
    base = S().get("cmb_baseline")
    hm_lines = ["per tonne of hot metal", f"Plan cost {rs(sm['plan_cost_cr'], 2)} crore for {sm['plan']:,.0f} tHM"]
    if base and abs(sm["hm_cost"] - base["hm_cost"]) > 0.5:
        hm_lines.append(f"{sm['hm_cost'] - base['hm_cost']:+,.0f} Rs/tHM vs baseline run")
    a1, ar1, a2, ar2, a3 = st.columns([3, 1.1, 3, 1.1, 3.4])
    a1.markdown(stage_card("Sinter plant", PALETTE["sinter"], rs(sm["sinter_price"]), "per t sinter",
                           [f"Burden {fresh:,.0f} kg per t sinter", f"{sm['sinter_t']:,.0f} t made"]), unsafe_allow_html=True)
    ar1.markdown(arrow_cell(f"{sm['sinter_kg']:,.0f} kg sinter<br>per tHM"), unsafe_allow_html=True)
    a2.markdown(stage_card("Blast furnace", PALETTE["furnace"], f"{sm['fuel_kg']:,.0f}", "kg per tHM \u00b7 fuel rate",
                           [f"Coke {sm['coke_kg']:,.0f} \u00b7 Nut coke {sm['nut_kg']:,.0f} \u00b7 PCI {sm['pci_kg']:,.0f}"]), unsafe_allow_html=True)
    ar2.markdown(arrow_cell("Burden and fuel<br>costed"), unsafe_allow_html=True)
    a3.markdown(stage_card("Hot metal", PALETTE["hm"], rs(sm["hm_cost"]), "", hm_lines, hero=True), unsafe_allow_html=True)

    # KPI panels (replace the old baseline row)
    kpi_s = K.sinter_kpis(sm, sdf)
    kpi_f = K.furnace_kpis(sm, run["furnace"]["df"])
    p1, p2 = st.columns([1.35, 1])
    p1.markdown(K.sinter_panel_html(kpi_s, PALETTE["sinter"], show_ore=False), unsafe_allow_html=True)
    p2.markdown(K.furnace_panel_html(kpi_f, PALETTE["furnace"], fuel_tiles=True), unsafe_allow_html=True)

    # compare with a baseline run, last month's actual or the budget (folded: the cards above are the page)
    prev = S().get("cmb_prev")
    with st.expander("Compare with a baseline run, last month or budget"):
        d_base = f"{sm['hm_cost'] - base['hm_cost']:+,.0f} Rs/tHM" if base else "no baseline set"
        s1, s3, s4, s5 = st.columns([1.3, 1.2, 1.2, 1.2], vertical_alignment="bottom") if "vertical_alignment" in inspect.signature(st.columns).parameters else st.columns([1.3, 1.2, 1.2, 1.2])
        s1.markdown(f"<div class='statrow'><span>Change vs baseline run<b>{d_base if not base or abs(sm['hm_cost'] - base['hm_cost']) > 0.5 else 'No change'}</b></span></div>", unsafe_allow_html=True)
        s3.number_input("Last month actual, Rs/tHM", min_value=0.0, step=100.0, key="cmb_actual", help="Optional. Shows the difference to this run.")
        s4.number_input("Budget, Rs/tHM", min_value=0.0, step=100.0, key="cmb_budget", help="Optional. Shows the difference to this run.")
        if s5.button("Use this run as baseline", **W):
            S()["cmb_baseline"] = sm
            st.rerun()
        if S()["cmb_actual"] > 0 or S()["cmb_budget"] > 0:
            st.markdown("<div class='statrow'>" + (f"<span>vs last month actual<b>{sm['hm_cost'] - S()['cmb_actual']:+,.0f} Rs/tHM</b></span>" if S()["cmb_actual"] > 0 else "")
                        + (f"<span>vs budget<b>{sm['hm_cost'] - S()['cmb_budget']:+,.0f} Rs/tHM</b></span>" if S()["cmb_budget"] > 0 else "") + "</div>", unsafe_allow_html=True)

    # money table + alerts
    m1, m2 = st.columns([1.55, 1])
    with m1:
        st.markdown("<div class='sect'>Where the money goes<small>per tonne of hot metal</small></div>", unsafe_allow_html=True)
        rows, tkg, trs = summary_tables(run, sm)
        bar = "".join(f"<div style='width:{r / trs * 100:.2f}%;background:{GROUP_COLOR.get(g, '#888')}' title='{GROUP_LABEL.get(g, g)}'></div>" for g, kg, r in rows)
        st.markdown(f"<div class='propbar'>{bar}</div>", unsafe_allow_html=True)
        html = "<div class='mrow hd'><span>Item</span><span>kg per tHM</span><span>₹ per tHM</span><span>Share</span></div>"
        for g, kg, r in rows:
            html += (f"<div class='mrow'><span><i class='sw' style='background:{GROUP_COLOR.get(g, '#888')}'></i>{GROUP_LABEL.get(g, g)}</span>"
                     f"<span>{kg:,.0f}</span><span>{r:,.0f}</span><span>{r / trs * 100:.1f}%</span></div>")
        html += f"<div class='mrow tot'><span>Total</span><span></span><span>{trs:,.0f}</span><span>100%</span></div>"
        st.markdown(html, unsafe_allow_html=True)
    with m2:
        st.markdown("<div class='sect'>Alerts</div>", unsafe_allow_html=True)
        st.markdown(alerts_html(sns, run, sm, stale), unsafe_allow_html=True)

    # quality limits: one small card per limit, coloured by where the value sits
    cards = K.limit_cards(run, sm)
    kind, msg = K.limit_summary(cards)
    cls = {"ok": "g", "warn": "w", "bad": "r"}[kind]
    st.markdown("<div class='sect'>Quality limits<small>green = inside the target &middot; amber = outside it but inside the approved tolerance &middot; red = outside</small></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='notice {cls}'>{msg}</div>", unsafe_allow_html=True)
    st.markdown(K.limits_html(cards), unsafe_allow_html=True)

    composition_section(run)

    if S()["cmb_view"] != "Engineer view":
        return

    # ---- Engineer view: the cost split, what changed since the last run, and the workings
    with st.expander("More values from the two models"):
        sp = [("Sinter cost", f"{rs(sm['sinter_price'])} per t (raw {sm['sinter_raw']:,.0f} + O&M {sm['sinter_om']:,.0f})"),
              ("Sinter made", f"{sm['sinter_t']:,.0f} t at the final pass"),
              ("Sinter used by the furnace", f"{sm['sinter_kg']:,.1f} kg/tHM")]
        fp = [("Hot metal cost", f"{rs(sm['hm_cost'])} per tHM" + (f" (raw materials {sm['hm_raw']:,.0f} + O&M {sm['hm_om']:,.0f})" if sm["hm_om"] > 0 else "")),
              ("Fuel rate", f"{sm['fuel_kg']:,.1f} kg/tHM (coke {sm['coke_kg']:,.1f} + nut coke {sm['nut_kg']:,.1f} + PCI {sm['pci_kg']:,.1f})"),
              ("Hot metal S", f"{sm['hm_s']:.3f} %")]
        e1, _e, e2 = st.columns([1, 0.02, 1])
        e1.markdown(kv_card("Sinter plant", PALETTE["sinter"], sp), unsafe_allow_html=True)
        e2.markdown(kv_card("Blast furnace", PALETTE["furnace"], fp), unsafe_allow_html=True)

    l1, l3 = st.columns([1, 1.3])
    with l1:
        st.markdown("<div class='sect'>Cost breakdown</div>", unsafe_allow_html=True)
        fig = go.Figure(go.Pie(labels=[GROUP_LABEL.get(g, g).split(" (")[0] for g, _k, _r in rows], values=[r for _g, _k, r in rows], hole=0.62, sort=False,
                               marker=dict(colors=[GROUP_COLOR.get(g, "#888") for g, _k, _r in rows], line=dict(color=PALETTE["bg"], width=2)),
                               textinfo="none", hovertemplate="%{label}: ₹%{value:,.0f}/tHM (%{percent})<extra></extra>"))
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)", showlegend=True,
                          font=dict(color=PALETTE["text"], family="Source Sans 3, sans-serif"), legend=dict(font=dict(size=11)),
                          annotations=[dict(text=f"₹{sm['hm_cost']:,.0f}", x=0.5, y=0.5, showarrow=False, font=dict(size=20))])
        st.plotly_chart(fig, **PLOT_W)
    with l3:
        st.markdown("<div class='sect'>Change since last run</div>", unsafe_allow_html=True)
        if prev and prev.get("ok"):
            ps = L.summary(prev)
            chg = H.diff_inputs(prev["inputs"], run["inputs"])
            st.markdown(f"<div class='statrow'><span>Hot metal<b>{sm['hm_cost'] - ps['hm_cost']:+,.0f} Rs/tHM</b></span>"
                        f"<span>Sinter price<b>{sm['sinter_price'] - ps['sinter_price']:+,.0f} Rs/t</b></span>"
                        f"<span>Sinter used<b>{sm['sinter_kg'] - ps['sinter_kg']:+,.0f} kg/tHM</b></span></div>", unsafe_allow_html=True)
            st.markdown("".join(f"<div class='note-line nt'>{x}</div>" for x in chg) or "<div class='small'>Same inputs as the run before.</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='small'>Run twice to see what changed and how it moved the cost.</div>", unsafe_allow_html=True)

    with st.expander("How this number was built"):
        st.markdown(f"<div class='small'>Pass by pass: the sinter model runs at a tonnage, its result goes into the furnace's Sinter row, the furnace says how much sinter it uses, "
                    f"and that becomes the next tonnage. Tolerance {run['tol'] * 100:.2f}% on sinter tonnage, up to {run['max_pass']} passes. "
                    f"Took {run.get('seconds', 0):.0f} s.</div>", unsafe_allow_html=True)
        pa = pd.DataFrame(run["passes"])
        st.dataframe(pa, hide_index=True, column_config={"Sinter t": st.column_config.NumberColumn(format="%.0f"), "Sinter Rs/t": st.column_config.NumberColumn(format="%.0f"),
                     "Furnace Rs/tHM": st.column_config.NumberColumn(format="%.0f"), "Sinter kg/tHM": st.column_config.NumberColumn(format="%.1f"),
                     "Next t": st.column_config.NumberColumn(format="%.0f")}, **W)
        if run.get("message"):
            st.markdown(f"<div class='notice w'>{run['message']}</div>", unsafe_allow_html=True)
        for x in run["furnace"]["res"][4][:6]:
            st.markdown(f"<div class='note-line nt'>{x}</div>", unsafe_allow_html=True)
    with st.expander("Inputs used in this run"):
        inp = run["inputs"]
        st.markdown(f"<div class='small'>Plan {inp['plan']:,.0f} tHM · sinter horizon {inp['kw'].get('horizon_days', 7):g} days · O&M ₹{inp['om']:,.0f}/t · "
                    f"furnace O&M ₹{inp['mcfg'].om_rs_thm:,.0f}/tHM · IOL {inp['kw']['iol_nominal'] * 100:.1f}% · BF returns {inp['kw']['bf_nominal'] * 100:.1f}% of the charged mix · furnace {inp['mcfg'].furnace_name}</div>", unsafe_allow_html=True)
        st.markdown("<div class='small'>Sinter row handed to the furnace</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([run["sinter"]["vals"]], index=[run["row"]]).round(3), **W)
        st.markdown("<div class='small'>Sinter materials (own file, own stock)</div>", unsafe_allow_html=True)
        st.dataframe(inp["sdf"][inp["sdf"]["Available_Tonnes"] > 0][["Group", "Fe", "SiO2", "Al2O3", "CaO", "MgO", "Available_Tonnes", "Price_Rs_t"]].round(3), **W)
        st.markdown("<div class='small'>Furnace materials (own file, own stock)</div>", unsafe_allow_html=True)
        d = mopt.ensure_columns(inp["mdf"])
        st.dataframe(d[d["Available"]][["Group", "Fe", "CaO", "SiO2", "Al2O3", "Moisture_Pct", "RM_Stock", "Price_Rs_t"]].round(3), **W)
    if st.button("Save this run as a scenario", key="cmb_save_here"):
        S()["route_combined"] = "Saved scenarios"
        st.rerun()


def alerts_html(sns, run, sm, stale):
    out = []
    if sm["sinter_status"] == "Production_Risk":
        out.append(alert("short", "The sinter recipe is <b>outside the approved tolerance</b>. Read the cost as an estimate only."))
    elif sm["sinter_status"] == "Relaxed":
        msgs = run["sinter"]["report"].get("messages") or []
        out.append(alert("check", "Sinter result is <b>Relaxed</b>: " + (str(msgs[0]) if msgs else "a spec is missed but inside the approved tolerance") + " Read the cost per tHM as an estimate."))
    if run.get("jump"):
        out.append(alert("check", run["message"]))
    elif not run["converged"]:
        out.append(alert("check", run["message"]))
    if run["capped"]:
        out.append(alert("check", f"Sinter is limited to <b>{run['cap_t']:,.0f} t</b>, the most the plant can make within tolerance; the furnace takes more ore for the rest."))
    ssk, fsk = H.stock_checks(run)
    for nm, d in (("Sinter plant", ssk), ("Blast furnace", fsk)):
        if d is not None and not d.empty:
            short = d[d["Cover %"] < 99.5]
            tight = d[(d["Cover %"] >= 99.5) & (d["Cover %"] < 101.0)]
            if len(short):
                out.append(alert("short", f"{nm}: " + "; ".join(f"{r.Material} short by {r['Needed t'] - r['Stock t']:,.0f} t" for _, r in short.iterrows()) + "."))
            elif len(tight):
                out.append(alert("check", f"{nm}: " + ", ".join(tight["Material"]) + " use all of their stock (nothing spare)."))
            else:
                low = d.sort_values("Cover %").iloc[0]
                out.append(alert("ok", f"{nm} stock covers every material; tightest is {low['Material']} at {min(low['Cover %'], 9999):,.0f}%."))
    cfg = run["furnace"]["cfg"]
    if sm["sinter_share"] >= cfg.sinter_max * 100 - 3:
        out.append(alert("check", f"Sinter share {sm['sinter_share']:.1f}% is close to the top of its {cfg.sinter_min * 100:.0f}-{cfg.sinter_max * 100:.0f}% guard rails."))
    if sm["al2o3_pct"] >= cfg.al2o3_max_pct - 0.01:
        out.append(alert("check", f"Slag Al2O3 is at its {cfg.al2o3_max_pct:g}% limit."))
    try:
        mraw = S()["sinter__master_df"].copy()
        mraw.loc[[m for m in mraw.index if not S()["sinter__available"].get(m, True)], "Available_Tonnes"] = 0.0
        for w in sns["master_data_warnings"](mraw)[:3]:
            out.append(alert("check", w.split(" — ")[0] + " (sinter file)."))
    except Exception:
        pass
    if stale:
        out.append(alert("check", "Inputs have changed since this run. Press <b>Run both models</b> to refresh."))
    return "".join(out)


# ------------------------------------------------------------------------------------------ uploads hub
def page_settings(sns, mns):
    _init()
    st.markdown("<h1>Uploads &amp; shared settings</h1><div class='sub'>Two separate Excel files feed their own models only. Sinter and furnace materials, stock and stock checks are never mixed, even when names match.</div>", unsafe_allow_html=True)
    ms = nsrun.state("mbf")
    c1, c2, c3 = st.columns(3)
    prob = H.sinter_setup_problems(sns)
    c1.markdown(stage_card("Sinter master", PALETTE["sinter"], f"{len(S()['sinter__master_df'])}", "materials",
                           [f"File: {S()['sinter__source']}", ("Setup complete" if not prob else f"{len(prob)} item(s) to set under Sinter > Inputs")]), unsafe_allow_html=True)
    c2.markdown(stage_card("Furnace materials", PALETTE["furnace"], f"{len(ms.df)}", "rows",
                           [f"File: {ms.source}", f"{int(ms.df['Available'].sum())} switched on" + (" · demo values" if ms.demo else "")]), unsafe_allow_html=True)
    c3.markdown(stage_card("Plan", PALETTE["hm"], f"{S()['cmb_plan']:,.0f}", "tHM", [f"{S()['sinter__horizon_days']:g}-day sinter horizon"]), unsafe_allow_html=True)
    st.markdown("<div class='sect'>Shared settings</div>", unsafe_allow_html=True)
    a, b, c, d = st.columns(4, vertical_alignment="bottom")
    a.number_input("Plan, tHM", min_value=100.0, max_value=1_000_000.0, step=500.0, key="cmb_plan")
    om_now = float(ms.cfg.om_rs_thm)
    om_new = b.number_input("Furnace O&M, Rs per tHM", min_value=0.0, max_value=100_000.0, value=om_now, step=50.0, format="%.0f", key=f"cmb_om_{ms.cfg_ver}",
                            help="Operations and maintenance cost of the furnace, on top of the raw materials. It is part of the hot metal cost. The same setting is under MBF > Inputs.")
    if abs(om_new - om_now) > 1e-9:
        ms.cfg.om_rs_thm = float(om_new)
        ms.changed, ms.changed_source = True, "O&M cost"
        ms.cfg_ver += 1
        st.rerun()
    c.number_input("Loop tolerance, % of sinter tonnage", min_value=0.02, max_value=5.0, step=0.02, format="%.2f", key="cmb_tol", help="The loop stops when the sinter tonnage changes by less than this between passes.")
    d.number_input("Maximum passes", min_value=2, max_value=20, step=1, key="cmb_maxpass")
    st.caption("Sinter O&M (Rs per tonne of sinter) is set on the sinter model's Inputs page and is already inside the sinter price the furnace pays.")
    st.markdown("<div class='sect'>Upload the two files, then edit and confirm<small>each file feeds its own model only</small></div>", unsafe_allow_html=True)
    t1, t2 = st.tabs(["Sinter: MASTER_Chemistry.xlsx", "Furnace: Input_chemistry.xlsx"])
    with t1:
        sns["settings"]()                       # the sinter Upload & Settings page, which carries the edit-and-confirm table
    with t2:
        st.download_button("Download the furnace template (.xlsx)", mopt.template_bytes(), "MBF_Input_Template.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="cmb_mbf_tpl")
        mns["master_uploader"]("hub")
        editor.furnace_editor(mns)
    if prob:
        st.markdown(alert("check", "Sinter model still needs: " + "; ".join(prob)), unsafe_allow_html=True)


# ------------------------------------------------------------------------------------------ scans
def _need_run():
    run = S().get("cmb_run")
    if not run or not run.get("ok"):
        st.markdown(alert("check", "Run the combined model on the <b>Hot metal cost</b> page first. The scans start from that run."), unsafe_allow_html=True)
        return None
    return run


def _quick(run, sdf, kw, targets, om, mdf):
    return L.run_loop(sdf, kw, targets, om, mdf, run["inputs"]["mcfg"], run["plan"], tol=0.005, max_pass=4, refine_steps=0,
                      start_t=run["sinter"]["t"], cap_t0=run["cap_t"], explain_final=False)


def page_basicity(sns):
    _init()
    st.markdown("<h1>Basicity scan</h1><div class='sub'>Move the sinter basicity spec and see what it does to the cost of one tonne of hot metal.</div>", unsafe_allow_html=True)
    run = _need_run()
    if not run:
        return
    inp = run["inputs"]
    t0 = inp["targets"]
    width = t0["Basicity_max"] - t0["Basicity_min"]
    a, b, c = st.columns(3)
    lo = a.number_input("From (window centre)", 1.4, 2.6, 1.7, 0.05, key="cmb_bs_lo")
    hi = b.number_input("To", 1.4, 2.6, 2.2, 0.05, key="cmb_bs_hi")
    step = c.number_input("Step", 0.05, 0.5, 0.1, 0.05, key="cmb_bs_step")
    st.caption(f"The spec window keeps today's width of {width:.2f} (now {t0['Basicity_min']:.2f}-{t0['Basicity_max']:.2f}). Each point is a full sinter-furnace loop, so a scan takes a minute or so.")
    if st.button("Run scan", type="primary", key="cmb_bs_run"):
        pts = [round(x, 4) for x in np.arange(lo, hi + 1e-9, step)]
        bar, rows = st.progress(0.0), []
        for i, bc in enumerate(pts):
            bar.progress((i + 1) / len(pts), text=f"Basicity {bc:.2f}")
            tg = dict(inp["targets"]); tg["Basicity_min"], tg["Basicity_max"] = bc - width / 2, bc + width / 2
            r = _quick(run, inp["sdf"], inp["kw"], tg, inp["om"], inp["mdf"])
            if r["ok"]:
                sm = L.summary(r)
                rows.append({"Basicity (centre)": bc, "Hot metal Rs/tHM": sm["hm_cost"], "Sinter Rs/t": sm["sinter_price"], "Sinter status": sm["sinter_status"],
                             "Sinter kg/tHM": sm["sinter_kg"], "Sinter share %": sm["sinter_share"], "Coke kg/tHM": sm["coke_kg"]})
            else:
                rows.append({"Basicity (centre)": bc, "Hot metal Rs/tHM": np.nan, "Sinter status": "no recipe"})
        bar.empty()
        S()["cmb_bs"] = pd.DataFrame(rows)
    df = S().get("cmb_bs")
    if df is not None and len(df):
        fig = go.Figure(go.Scatter(x=df["Basicity (centre)"], y=df["Hot metal Rs/tHM"], mode="lines+markers", line=dict(color=PALETTE["hm"], width=3), name="Hot metal"))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color=PALETTE["text"]),
                          xaxis_title="Sinter basicity (CaO/SiO2), spec window centre", yaxis_title="Rs per tHM", yaxis=dict(gridcolor=PALETTE["line"]), xaxis=dict(gridcolor=PALETTE["line"]))
        st.plotly_chart(fig, **PLOT_W)
        st.dataframe(df.round(2), hide_index=True, **W)
        st.caption("Basicity affects only the sinter chemistry and price here. The model does not describe sinter strength or reducibility. "
                   "Near the sinter plant's ceiling the furnace's demand can jump between two burdens, so differences of about ₹100 per tHM or less are within the loop's own resolution.")


def page_drivers(sns):
    _init()
    st.markdown("<h1>Price drivers</h1><div class='sub'>What a price rise on each material used does to the cost of one tonne of hot metal, through both models.</div>", unsafe_allow_html=True)
    run = _need_run()
    if not run:
        return
    inp, sm = run["inputs"], L.summary(run)
    pct = st.slider("Price rise tested, %", 2, 25, 10, key="cmb_pd_pct")
    n = st.slider("Materials per model", 3, 10, 4, key="cmb_pd_n", help="The ones with the largest spend in this run are tested.")
    s_items = sorted(sm["sinter_blend"].items(), key=lambda kv: -kv[1] * float(inp["sdf"].loc[kv[0], "Price_Rs_t"]) if kv[0] in inp["sdf"].index else 0)
    s_items = [m for m, v in s_items if m in inp["sdf"].index and float(inp["sdf"].loc[m, "Price_Rs_t"]) > 0][:n]
    f_items = sorted(sm["burden"].items(), key=lambda kv: -kv[1] * float(run["furnace"]["df"].loc[kv[0], "Price_Rs_t"]))
    f_items = [m for m, v in f_items if m != run["row"]][:n]
    st.caption(f"{len(s_items) + len(f_items) + 1} runs of the full loop, roughly {(len(s_items) + len(f_items) + 1) * 6 // 60 + 1} minute(s) in all.")
    if st.button("Run price drivers", type="primary", key="cmb_pd_run"):
        bar, rows, k = st.progress(0.0), [], 0
        tot = len(s_items) + len(f_items) + 1
        f = 1 + pct / 100.0

        def one(label, model, price_now, sdf, om, mdf):
            nonlocal k
            k += 1
            bar.progress(k / tot, text=f"{label}")
            r = _quick(run, sdf, inp["kw"], inp["targets"], om, mdf)
            if r["ok"]:
                rows.append({"Item": label, "Model": model, "Price now Rs/t": price_now, f"Hot metal change for +{pct}% (Rs/tHM)": L.summary(r)["hm_cost"] - sm["hm_cost"]})
        for m in s_items:
            d = inp["sdf"].copy(); d["Price_Rs_t"] = d["Price_Rs_t"].astype(float); d.loc[m, "Price_Rs_t"] *= f
            one(m.title(), "Sinter", float(inp["sdf"].loc[m, "Price_Rs_t"]), d, inp["om"], inp["mdf"])
        one("Sinter O&M", "Sinter", inp["om"], inp["sdf"], inp["om"] * f, inp["mdf"])
        for m in f_items:
            d = inp["mdf"].copy(); d["Price_Rs_t"] = d["Price_Rs_t"].astype(float); d.loc[m, "Price_Rs_t"] = float(d.loc[m, "Price_Rs_t"]) * f
            one(m.replace("_", " "), "Furnace", float(inp["mdf"].loc[m, "Price_Rs_t"]), inp["sdf"], inp["om"], d)
        bar.empty()
        S()["cmb_pd"] = pd.DataFrame(rows)
    df = S().get("cmb_pd")
    if df is not None and len(df):
        col = [c for c in df.columns if c.startswith("Hot metal change")][0]
        d = df.sort_values(col)
        fig = go.Figure(go.Bar(x=d[col], y=d["Item"] + "  (" + d["Model"] + ")", orientation="h",
                               marker_color=[PALETTE["sinter"] if m == "Sinter" else PALETTE["furnace"] for m in d["Model"]]))
        fig.update_layout(height=max(280, 34 * len(d)), margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font=dict(color=PALETTE["text"]), xaxis_title="Change in Rs per tHM", xaxis=dict(gridcolor=PALETTE["line"]))
        st.plotly_chart(fig, **PLOT_W)
        st.dataframe(df.sort_values(col, ascending=False).round(1), hide_index=True, **W)
        st.caption("Each test re-runs the whole loop with only that one price changed. Differences of about Rs 50 per tHM or less can come from the loop's own tolerance (0.5% here, to keep the scan quick).")


# ------------------------------------------------------------------------------------------ scenarios
def page_scenarios(sns):
    _init()
    st.markdown("<h1>Saved scenarios</h1><div class='sub'>Keep a run, change something, run again, compare. Up to 8 per session.</div>", unsafe_allow_html=True)
    run = S().get("cmb_run")
    scen = S()["cmb_scen"]
    if run and run.get("ok"):
        a, b = st.columns([3, 1], vertical_alignment="bottom") if "vertical_alignment" in inspect.signature(st.columns).parameters else st.columns([3, 1])
        name = a.text_input("Name for this run", value=f"Scenario {len(scen) + 1}", key=f"cmb_sc_name_{len(scen)}")
        if b.button("Save this run", type="primary", **W) and len(scen) < 8:
            sm = L.summary(run)
            base = S().get("cmb_baseline")
            scen.append({"name": name, "time": dt.datetime.now().strftime("%H:%M"), "sm": sm, "inputs": run["inputs"],
                         "changes": H.diff_inputs(scen[0]["inputs"], run["inputs"]) if scen else []})
            st.rerun()
    else:
        st.markdown(alert("check", "Run the combined model first, then save the run here."), unsafe_allow_html=True)
    if not scen:
        st.caption("No scenarios saved yet.")
        return
    names = [s["name"] for s in scen]
    pick = st.multiselect("Compare (up to 3)", names, default=names[-3:], max_selections=3, key="cmb_sc_pick")
    sel = [s for s in scen if s["name"] in pick]
    if sel:
        rows = [("Hot metal Rs/tHM", "hm_cost", ",.0f"), ("Sinter price Rs/t", "sinter_price", ",.0f"), ("Sinter used kg/tHM", "sinter_kg", ",.0f"), ("Sinter share %", "sinter_share", ".1f"),
                ("Coke kg/tHM", "coke_kg", ",.1f"), ("Fuel kg/tHM", "fuel_kg", ",.1f"), ("Slag kg/tHM", "slag_kg", ",.0f"), ("Plan cost, crore", "plan_cost_cr", ".2f")]
        tbl = pd.DataFrame({s["name"]: [f"{s['sm'][k]:{f}}" for _l, k, f in rows] for s in sel}, index=[r[0] for r in rows])
        st.dataframe(tbl, **W)
        if len(sel) > 1:
            d = sel[1:]
            st.markdown("".join(f"<div class='note-line nt'><b>{s['name']}</b> vs {sel[0]['name']}: {s['sm']['hm_cost'] - sel[0]['sm']['hm_cost']:+,.0f} Rs/tHM"
                                f" ({'; '.join(H.diff_inputs(sel[0]['inputs'], s['inputs'])[:6]) or 'same inputs'})</div>" for s in d), unsafe_allow_html=True)
    if st.button("Clear all scenarios", key="cmb_sc_clear"):
        scen.clear()
        st.rerun()


# ------------------------------------------------------------------------------------------ export
def _extra_items(run, sm):
    items = []
    items.append(("Combined Summary", "Combined sinter + furnace result", pd.DataFrame([
        ("Hot metal cost, Rs/tHM", sm["hm_cost"]), ("  of which raw materials, Rs/tHM", sm["hm_raw"]), ("  of which furnace O&M, Rs/tHM", sm["hm_om"]), ("Plan, tHM", sm["plan"]), ("Plan cost, crore Rs", sm["plan_cost_cr"]), ("Sinter price, Rs/t (raw + O&M)", sm["sinter_price"]),
        ("Sinter raw-material cost, Rs/t", sm["sinter_raw"]), ("Sinter O&M, Rs/t", sm["sinter_om"]), ("Sinter used, kg/tHM", sm["sinter_kg"]), ("Sinter tonnes (final pass)", sm["sinter_t"]),
        ("Sinter status", sm["sinter_status"]), ("Furnace status", sm["furnace_status"]), ("Loop converged", "yes" if sm["converged"] else "no"), ("Passes", sm["passes"]),
        ("Sinter limited to (t)", sm["cap_t"] if sm["capped"] else "not limited"), ("Sinter share, %", sm["sinter_share"]), ("Fuel, kg/tHM", sm["fuel_kg"]), ("Slag, kg/tHM", sm["slag_kg"])],
        columns=["Item", "Value"]), run.get("message", "")))
    items.append(("Sinter Recipe", "Sinter recipe at the final pass, dry kg per t sinter",
                  pd.DataFrame([(m, v) for m, v in sm["sinter_blend"].items()], columns=["Material", "kg per t sinter"]), ""))
    sin_c, fur_c = L.compositions(run)
    items.append(("Sinter Burden", "Sinter burden composition, per tonne of sinter", sin_c, "BF returns: zero cost, chemistry only"))
    items.append(("Furnace Burden", "Blast furnace burden composition, per tonne of hot metal (O&M included)", fur_c, ""))
    items.append(("Sinter Chemistry", "Sinter chemistry handed to the furnace", pd.DataFrame([run["sinter"]["vals"]]).round(4), ""))
    items.append(("Loop Passes", "Pass by pass", pd.DataFrame(run["passes"]), run.get("message", "")))
    ssk, fsk = H.stock_checks(run)
    items.append(("Stock Check Sinter", "Sinter plant stock check (own stock)", ssk, ""))
    items.append(("Stock Check Furnace", "Furnace stock check (own stock)", fsk, ""))
    if S()["cmb_scen"]:
        items.append(("Combined Scenarios", "Saved combined scenarios", pd.DataFrame([{"Scenario": s["name"], "Hot metal Rs/tHM": s["sm"]["hm_cost"], "Sinter Rs/t": s["sm"]["sinter_price"],
                      "Sinter kg/tHM": s["sm"]["sinter_kg"], "Changes vs first": "; ".join(s["changes"][:8])} for s in S()["cmb_scen"]]), ""))
    return items


def page_export(sns):
    _init()
    st.markdown("<h1>Export</h1><div class='sub'>One workbook: the furnace engine's own formatted sheets for the final run, plus the combined sheets. Nothing downloads on its own.</div>", unsafe_allow_html=True)
    run = S().get("cmb_run")
    if not run or not run.get("ok"):
        st.markdown(alert("check", "Run the combined model first."), unsafe_allow_html=True)
        return
    sm = L.summary(run)
    if st.button("Export combined results", type="primary", key="cmb_exp"):
        with st.spinner("Building the workbook"):
            f = run["furnace"]
            bundle = mopt.make_bundle(f["df"], f["res"], f["cfg"], with_curve=False)
            data = mopt.export_bytes(bundle, {}, extra=an.extra_sheets(_extra_items(run, sm)))
        S()["cmb_export"] = (data, f"Hot_metal_cost_{dt.datetime.now():%Y%m%d_%H%M}.xlsx")
    ex = S().get("cmb_export")
    if ex:
        st.download_button("Download workbook", ex[0], ex[1], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="cmb_dl")
        st.caption(f"{ex[1]} · {len(ex[0]) / 1024:,.0f} KB. The sinter dashboard and the furnace dashboard keep their own exports under their Reports pages.")


def page_glossary():
    st.markdown("<h1>Glossary</h1>", unsafe_allow_html=True)
    st.markdown("""
- **tHM**: tonne of hot metal. **B2**: slag basicity, CaO/SiO2. **B4**: (CaO + MgO)/(SiO2 + Al2O3).
- **Fe/C**: iron required per tonne of hot metal over fixed carbon charged. **DRR**: direct reduction rate.
- **BFR**: blast furnace returns, poured into the sinter mix as a recycled material at zero cost and counted in chemistry only. **IOL fines**: counted in the burden and the cost.
- **Optimal / Relaxed / Production_Risk (sinter)**: every spec met / a spec missed but inside the approved tolerance / outside tolerance. A Relaxed sinter makes the hot metal cost an estimate.
- **Loop**: the sinter model runs, its result replaces the furnace's Sinter row, the furnace says how much sinter it uses, and the sinter model runs again at that tonnage until it settles.
- **Sinter limited**: the sinter plant cannot make more than a certain tonnage within the approved tolerance, so the furnace is given that tonnage as its sinter stock and takes more ore for the rest.
""")


def render(page, sns, mns):
    {"Hot metal cost": lambda: page_hot_metal(sns), "Uploads & shared settings": lambda: page_settings(sns, mns),
     "Basicity scan": lambda: page_basicity(sns), "Price drivers": lambda: page_drivers(sns), "Saved scenarios": lambda: page_scenarios(sns),
     "Export": lambda: page_export(sns), "Glossary": page_glossary}.get(page, lambda: page_hot_metal(sns))()
