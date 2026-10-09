"""Sinter-to-hot-metal impact: the baseline is never touched, the two bridges add up to the total, and the condition prices make sense."""
import pandas as pd
import pytest

from helpers import KW, real_inputs, sinter_active
from combined import cooptimise as C, handoff as H, loop as L
from impact import engine as E
from sinter import optimizer as s
from mbf import optimiser as m


@pytest.fixture(scope="module")
def run():
    sdf, mdf = real_inputs()
    sdf = sinter_active(sdf)
    cfg = m.Config()
    r = L.run_loop(sdf, KW, s.TARGETS, 750.0, mdf, cfg, 9000.0)
    r["inputs"] = H.inputs_snapshot(sdf, KW, s.TARGETS, 750.0, mdf, cfg, 9000.0)
    return r


MULTI = {"price_pct": {"KIOM": 10.0}, "stock_pct": {"DIOM": -40.0}, "om": 900.0, "targets": {"Basicity_min": 1.8, "Basicity_max": 1.95}}


def test_no_change_set_says_so(run):
    r = E.run_impact(run, {})
    assert not r["ok"] and "No change" in r["message"] and r["groups"] == []


def test_groups_come_out_in_the_fixed_order():
    ch = {"inventory_weight": 0.5, "om": 800.0, "price_pct": {"KIOM": 5.0, "DIOM": 0.0}, "tolerances": {"SiO2_max": 6.4}}
    assert E.groups_changed(ch) == ["prices", "om", "tol", "ratio"]
    assert E.groups_changed({"price_pct": {"KIOM": 0.0}}) == []


def test_apply_works_on_copies_and_only_what_was_asked(run):
    inp = run["inputs"]
    before = (inp["sdf"].copy(), dict(inp["kw"]), dict(inp["targets"]), inp["om"])
    new = E.apply(inp, MULTI)
    assert new["sdf"].loc["KIOM", "Price_Rs_t"] == pytest.approx(inp["sdf"].loc["KIOM", "Price_Rs_t"] * 1.10)
    assert new["sdf"].loc["DIOM", "Available_Tonnes"] == pytest.approx(inp["sdf"].loc["DIOM", "Available_Tonnes"] * 0.6)
    assert new["om"] == 900.0 and new["targets"]["Basicity_max"] == 1.95
    assert inp["sdf"].equals(before[0]) and inp["kw"] == before[1] and inp["targets"] == before[2] and inp["om"] == before[3]     # originals untouched
    only = E.apply(inp, MULTI, only=["om"])
    assert only["om"] == 900.0 and only["targets"] == inp["targets"]
    pd.testing.assert_frame_equal(only["sdf"], inp["sdf"], check_dtype=False)
    tol = E.apply(inp, {"tolerances": {"SiO2_max": 6.5}})["kw"]["tolerances"]
    assert tol["SiO2_max"] == 6.5 and tol["Basicity_max"] == E.base_tolerances(inp)["Basicity_max"]
    ret = E.apply(inp, {"iol_pct": 6.0, "bfr_pct": 15.0, "coke_max": 90.0, "inventory_weight": 0.5})["kw"]
    assert ret["iol_nominal"] == pytest.approx(0.06) and ret["bf_nominal"] == pytest.approx(0.15) and ret["coke_max_rate"] == 90.0 and ret["inventory_weight"] == 0.5


def test_a_price_rise_costs_money_and_both_bridges_add_up(run):
    r = E.run_impact(run, MULTI)
    assert r["ok"] and r["total"] > 100.0
    assert r["total"] == pytest.approx(C.hm(r["new"]) - C.hm(run), abs=1e-6)
    assert r["chain"]["Change Rs/tHM"].sum() == pytest.approx(r["total"], abs=1e-6)             # path bridge adds up
    assert r["levers"]["Change Rs/tHM"].sum() == pytest.approx(r["total"], abs=1e-6)            # lever bridge adds up
    assert list(r["levers"]["Lever"][:4]) == ["Material prices", "Material stock", "Sinter O&M", "Spec windows"]
    price_step = r["chain"].set_index("Step").loc["Sinter price", "Change Rs/tHM"]
    assert price_step > 100.0                                                                    # the dearer sinter is what the furnace feels first
    assert run["inputs"]["sdf"].loc["KIOM", "Price_Rs_t"] == 5900.0                              # the baseline run still holds its own inputs


def test_one_lever_has_no_lever_bridge_but_the_path_still_adds_up(run):
    r = E.run_impact(run, {"om": 900.0})
    assert r["ok"] and r["levers"] is None
    assert r["chain"]["Change Rs/tHM"].sum() == pytest.approx(r["total"], abs=1e-6)
    assert r["total"] == pytest.approx(150.0 * run["furnace"]["kg"] / 1000.0, abs=15.0)           # Rs 150 per t, about 1.27 t of sinter per tHM


def test_the_same_result_as_running_the_combined_model_with_those_inputs(run):
    r = E.run_impact(run, {"om": 900.0})
    inp = E.apply(run["inputs"], {"om": 900.0})
    direct = L.run_loop(inp["sdf"], inp["kw"], inp["targets"], inp["om"], inp["mdf"], inp["mcfg"], inp["plan"], tol=run["tol"], max_pass=run["max_pass"])
    assert C.hm(r["new"]) == pytest.approx(C.hm(direct), abs=1e-6)


def test_describe_lists_every_change(run):
    d = E.describe(run["inputs"], MULTI)
    assert set(d["Lever"]) == {"Material prices", "Material stock", "Sinter O&M", "Spec windows"} and len(d) == 5


def test_every_default_condition_has_a_valid_change(run):
    conds = E.default_conditions(run["inputs"])
    assert len(conds) >= 10
    for c in conds:
        ch = E.condition_changes(run["inputs"], c["key"], c["step"])
        assert E.groups_changed(ch), c["key"]
        E.apply(run["inputs"], ch)


def test_condition_prices_find_the_sio2_tolerance_and_rank_savings_first(run):
    tbl, base_hm = E.condition_sweep(run, E.default_conditions(run["inputs"]))
    assert len(tbl) == len(E.default_conditions(run["inputs"])) and base_hm == pytest.approx(C.hm(run), abs=60)
    col = "Hot metal change Rs/tHM"
    assert list(tbl[col].dropna()) == sorted(tbl[col].dropna())                                  # most saving first
    sio2 = tbl.set_index("Condition").loc["SiO2 tolerance edge", col]
    assert sio2 < -30.0                                                                          # the binding rule on the sample inputs


def test_furnace_om_is_a_lever_that_adds_exactly_its_amount(run):
    assert E.groups_changed({"furnace_om": 1500.0}) == ["fom"]
    r = E.run_impact(run, {"furnace_om": 1500.0})
    om0 = run["inputs"]["mcfg"].om_rs_thm
    assert r["ok"] and r["total"] == pytest.approx(1500.0 - om0, abs=1e-6)                      # fixed Rs per tHM on top of the raw materials
    step = r["chain"].set_index("Step")
    assert step.loc["Furnace O&M", "Change Rs/tHM"] == pytest.approx(1500.0 - om0, abs=1e-6)
    assert abs(step.loc["Loop settling and rounding", "Change Rs/tHM"]) < 1e-6                  # nothing is left over for the settling row
    assert r["chain"]["Change Rs/tHM"].sum() == pytest.approx(r["total"], abs=1e-6)
    assert run["inputs"]["mcfg"].om_rs_thm == om0                                                # the baseline's own settings are untouched
    d = E.describe(run["inputs"], {"furnace_om": 1500.0})
    assert list(d["Lever"]) == ["Furnace O&M"]
