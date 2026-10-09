"""The single model: the Optimise hot metal cost page and combined.cooptimise.optimise (one objective, conditions as set)."""
import pandas as pd
import pytest

from helpers import KW, real_inputs, sinter_active
from combined import cooptimise as C
from combined import kpis as K
from combined import loop as L
from sinter import optimizer as s
from mbf import optimiser as m
from test_app_flow import fresh_app, go, press

PLAN = 9000.0


def _txt(at):
    return " ".join(x.value for x in at.markdown)


def _run(om=750.0):
    sdf, mdf = real_inputs()
    sdf = sinter_active(sdf)
    r = L.run_loop(sdf, KW, s.TARGETS, om, mdf, m.Config(), PLAN)
    r["inputs"] = {"sdf": sdf, "mdf": mdf, "om": om, "kw": KW, "targets": s.TARGETS, "mcfg": m.Config(), "plan": PLAN}
    return r


def test_tolerance_inputs_open_the_windows_but_never_past_the_tolerance():
    inp = _run()["inputs"]
    t = C.tolerance_inputs(inp)["targets"]
    assert t["SiO2_max"] == pytest.approx(6.2) and t["MgO_min"] == pytest.approx(2.0) and t["Basicity_max"] == pytest.approx(2.1)
    assert t["Fe_min"] <= inp["targets"]["Fe_min"] and t["Al2O3_max"] >= inp["targets"]["Al2O3_max"]
    assert inp["targets"]["SiO2_max"] == pytest.approx(5.8)                     # the original inputs are not touched


def test_tolerance_mode_is_honest_and_stays_inside_the_approved_tolerance():
    run = _run()
    res = C.optimise(run, rounds=1, min_gain=50.0, use_tolerance=True)
    assert res["ok"] and res["tolerance_used"] and res["conditions_changed"]
    assert res["accepted"] == (res["gain"] >= 50.0)                             # never accepts a saving below the noise limit
    if res["best"] is not None:
        cards = K.limit_cards(res["best"], L.summary(res["best"]), spec_run=run)["sinter"]
        assert all(c["status"] != "bad" for c in cards)                         # judged against TODAY's spec and tolerance: never outside the tolerance
        assert isinstance(C.tolerance_note(run, res["best"]), str) and C.tolerance_note(run, res["best"])
        assert res["gain"] == pytest.approx(C.hm(run) - C.hm(res["best"]))      # always measured against today's recipe under today's conditions


def test_default_mode_is_the_existing_search():
    run = _run()
    res = C.optimise(run, rounds=1, min_gain=50.0)
    assert res["ok"] and res["tolerance_used"] is False and not res["conditions_changed"]


def test_page_before_and_after_pressing_the_button():
    at = fresh_app()
    at.run()
    t = _txt(at)
    assert not at.exception, at.exception[0].value if at.exception else ""
    assert at.session_state["route_combined"] == "Optimise hot metal cost" and "Optimise hot metal cost" in t and "Press <b>Optimise hot metal cost</b>" in t
    at.session_state["cmb_op_rounds"] = 1
    press(at, "Optimise hot metal cost")                                         # runs both models, then the search, in one press
    assert not at.exception, at.exception[0].value if at.exception else ""
    t = _txt(at)
    res = at.session_state["cmb_opt"]
    assert res["ok"] and at.session_state["cmb_run"]["ok"]
    for needle in ("Key numbers", "Sinter plant", "Blast furnace", "Quality limits", "Mill scale used", "PCI used", "Iron ore used"):
        assert needle in t
    assert "Stock check" not in t


def test_page_with_a_saving_that_used_the_tolerance_says_so():
    at = fresh_app()
    go(at, "combined", "Hot metal cost")
    press(at, "Run both models")
    run = at.session_state["cmb_run"]
    inp = run["inputs"]
    new = L.run_loop(inp["sdf"], inp["kw"], inp["targets"], 700.0, inp["mdf"], inp["mcfg"], run["plan"])
    new["inputs"] = dict(inp, om=700.0)
    at.session_state["cmb_opt"] = {"ok": True, "accepted": True, "best": new, "gain": C.hm(run) - C.hm(new), "message": "Saves.", "conditions_changed": True, "tolerance_used": True,
                                   "fp": run["fp"], "run_time": run["time"], "values": C.furnace_values(run, inp), "trail": pd.DataFrame([{"Round": 1, "Rs/tHM": 1.0}])}
    go(at, "combined", "Optimise hot metal cost")
    t = _txt(at)
    assert not at.exception, at.exception[0].value if at.exception else ""
    assert "approved tolerance" in t and "Now | Optimised" in t and "What moved the hot metal cost" in t and "optimised recipe" in t
