"""
The single model: one page, one button, one objective, the lowest cost of one tonne of hot metal.

Sinter and furnace stay what they are underneath (the sinter model's spec tiers, tolerances, stock and ore-ratio rules, FeO band, heat
balance and every furnace rule); this page just runs them as one.  One press: it runs the combined loop on the current inputs when
that has not been done, then looks for the sinter recipe the furnace likes best (combined.cooptimise.optimise) and shows everything the
two plants report, today and optimised.  Two options change a condition and are off by default: holding the ores less strictly to
their stock shares, and letting the sinter use its approved tolerance.
"""
import streamlit as st

from combined import cooptimise as C
from combined import handoff as H
from combined import kpis as K
from combined import loop as L
from combined import page as CP
from impact import page as IP
from shared.theme import PALETTE, alert

W, S = CP.W, CP.S


def _open(page_name):
    S()["route_combined"] = page_name
    st.rerun()


def page(sns, mns):
    CP._init()
    st.markdown("<h1>Optimise hot metal cost</h1><div class='sub'>One objective: the lowest cost of one tonne of hot metal. The sinter and furnace models run as one; "
                "every sinter spec, tolerance, stock rule and furnace rule stays as set unless you tick an option below.</div>", unsafe_allow_html=True)
    problems = H.sinter_setup_problems(sns)
    if problems:
        st.markdown(alert("short", "Not set up yet: " + "; ".join(problems)), unsafe_allow_html=True)
        if st.button("Open Uploads & shared settings", key="single_open_up"):
            _open("Uploads & shared settings")
        return

    a, b, c = st.columns(3)
    a.number_input("Plan, tHM", min_value=100.0, max_value=1_000_000.0, step=500.0, format="%.0f", key="cmb_plan",
                   help="Hot metal planned over the sinter horizon. Sets the sinter tonnage the furnace needs.")
    min_gain = b.number_input("Smallest saving to accept, Rs per tHM", 0.0, 1000.0, 50.0, 10.0, key="cmb_op_gain",
                              help="Below this the result is treated as the loop's own noise and today's recipe is kept.")
    rounds = c.slider("Search rounds", 1, 4, 3, key="cmb_op_rounds", help="Each round re-measures what the furnace pays and tries again from the best recipe so far.")
    relax = st.checkbox("Also hold the ores less strictly to their stock shares (changes a condition)", key="cmb_op_relax",
                        help="Off by default. On, the search also tries inventory weights 0.5 and 0 and says what that rule costs.")
    tol = st.checkbox("Also let the sinter use its approved tolerance (changes a condition)", key="single_tol",
                      help="Off by default, so the sinter goals stay pinned to their spec. On, the spec windows are opened to the plant-approved tolerance edges and the furnace decides "
                           "where inside them the recipe sits. The result names every goal that leaves its spec. Most tolerances are placeholders except SiO2 6.2, so confirm them with the plant.")
    n_runs = rounds * (4 if not relax else 10) + 2 + (rounds * (5 if not relax else 12) + 3 if tol else 0)
    st.caption(f"One press runs both models and then up to about {n_runs} quick runs of the sinter-furnace loop: roughly {max(1, n_runs * 2 // 60)}-{max(2, n_runs * 4 // 60 + 1)} minute(s).")

    run = S().get("cmb_run")
    if st.button("Optimise hot metal cost", type="primary", key="single_go"):
        if not (run and run.get("ok") and run["fp"] == CP.current_fp(sns)):
            run = CP.execute_run(sns)                                    # no separate "run both models" step
        if run and run.get("ok"):
            bar = st.progress(0.0, text="Starting")
            res = C.optimise(run, rounds=int(rounds), min_gain=float(min_gain), relax_stock=bool(relax), use_tolerance=bool(tol),
                             progress=lambda f, msg: bar.progress(min(max(f, 0.0), 0.99), text=msg))
            bar.empty()
            res["fp"], res["run_time"] = run["fp"], run["time"]
            S()["cmb_opt"] = res
    run = S().get("cmb_run")
    if not run:
        st.markdown(alert("check", "Press <b>Optimise hot metal cost</b>. The sinter model runs first, its result replaces the furnace's Sinter row, the loop settles the sinter "
                                   "tonnage the furnace needs, and then the search looks for the recipe that gives the lowest hot metal cost."), unsafe_allow_html=True)
        return
    if not run["ok"]:
        st.markdown(alert("short", run["message"]), unsafe_allow_html=True)
        return
    if run["fp"] != CP.current_fp(sns):
        st.markdown(alert("check", "The inputs have changed since the last run. Press <b>Optimise hot metal cost</b> to run it again on the current inputs."), unsafe_allow_html=True)

    res = S().get("cmb_opt")
    new, head, chem, sin, fur, ok_res = IP._result_block(run, res)
    show = new if (ok_res and new is not None) else run
    sm = L.summary(show)
    st.markdown(f"<div class='sect'>Key numbers<small>{'optimised recipe' if show is not run else 'today' + chr(39) + 's recipe'}</small></div>", unsafe_allow_html=True)
    p1, p2 = st.columns([1.35, 1])
    p1.markdown(K.sinter_panel_html(K.sinter_kpis(sm, show["inputs"]["sdf"]), PALETTE["sinter"]), unsafe_allow_html=True)
    p2.markdown(K.furnace_panel_html(K.furnace_kpis(sm, show["furnace"]["df"]), PALETTE["furnace"]), unsafe_allow_html=True)
    IP._plants(run, new, now_only=not ok_res, spec_run=run if ok_res and res.get("tolerance_used") else None)
    if ok_res:
        IP._details(run, res, head, chem, sin, fur)
