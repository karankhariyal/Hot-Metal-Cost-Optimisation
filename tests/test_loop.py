"""The combined loop on the real input files."""
import pytest

from helpers import KW, real_inputs, sinter_active
from combined import loop as L
from sinter import optimizer as s
from mbf import optimiser as m

PLAN = 9000.0


@pytest.fixture(scope="module")
def inputs():
    sdf, mdf = real_inputs()
    return sinter_active(sdf), mdf


@pytest.fixture(scope="module")
def base(inputs):
    sdf, mdf = inputs
    return L.run_loop(sdf, KW, s.TARGETS, 750.0, mdf, m.Config(), PLAN)


def test_row_is_dry_cost_plus_om():
    vals = L.row_values({"Fe": 52.7, "CaO": 11.5, "SiO2": 6.0, "Al2O3": 3.7, "MgO": 2.4}, 6907.94, 750.0)
    assert vals["Price_Rs_t"] == pytest.approx(7657.94)


def test_converges_and_reports(base):
    assert base["ok"] and base["converged"]
    assert base["capped"] and 11000 < base["cap_t"] < 12000          # the plant cannot make the 12,249 t the first pass asks for
    sm = L.summary(base)
    assert sm["furnace_status"] == "Optimal" and sm["sinter_status"] in ("Optimal", "Relaxed")
    assert 30800 < sm["hm_cost"] < 31200


def test_first_pass_reproduces_the_screenshot(base):
    p1 = base["passes"][0]
    assert p1["Sinter Rs/t"] == pytest.approx(7657.9, abs=0.2) and p1["Furnace Rs/tHM"] == pytest.approx(31038.7, abs=0.5)
    assert p1["Next t"] == pytest.approx(12248.9, abs=1.0)


def test_same_inputs_same_answer_whatever_the_furnace_row_held(inputs, base):
    sdf, mdf = inputs
    row = L.find_sinter_row(mdf)
    d = mdf.copy()
    for c in ("Price_Rs_t", "Fe", "SiO2"):
        d[c] = d[c].astype(float)
    d.loc[row, ["Price_Rs_t", "Fe", "SiO2"]] = [9999.0, 40.0, 12.0]        # a very different typed row
    again = L.run_loop(sdf, KW, s.TARGETS, 750.0, d, m.Config(), PLAN)
    assert L.summary(again)["hm_cost"] == pytest.approx(L.summary(base)["hm_cost"], abs=1.0)


def test_sinter_price_change_moves_hot_metal_cost(inputs, base):
    sdf, mdf = inputs
    d = sdf.copy()
    d.loc["KIOM", "Price_Rs_t"] *= 1.10
    up = L.run_loop(d, KW, s.TARGETS, 750.0, mdf, m.Config(), PLAN)
    b, u = L.summary(base), L.summary(up)
    assert u["sinter_price"] > b["sinter_price"] + 100
    assert u["hm_cost"] > b["hm_cost"] + 100


def test_failed_sinter_run_stops_cleanly(inputs):
    sdf, mdf = inputs
    d = sdf.copy()
    d.loc[d["Group"].astype(str) == "Iron_ore", "Available_Tonnes"] = 0.0     # no ore at all
    run = L.run_loop(d, KW, s.TARGETS, 750.0, mdf, m.Config(), PLAN)
    assert not run["ok"] and "sinter" in run["message"].lower()
    assert run["furnace"] is None                                            # nothing was passed to the furnace


def test_no_sinter_row_switched_on(inputs):
    sdf, mdf = inputs
    d = mdf.copy()
    d.loc[L.find_sinter_row(d), "Available"] = False
    run = L.run_loop(sdf, KW, s.TARGETS, 750.0, d, m.Config(), PLAN)
    assert not run["ok"] and "Sinter row" in run["message"]


def test_only_the_sinter_row_is_replaced(inputs):
    sdf, mdf = inputs
    row = L.find_sinter_row(mdf)
    vals = {"Price_Rs_t": 7000.0, "Fe": 55.0, "CaO": 10.0, "SiO2": 5.0, "Al2O3": 3.0, "MgO": 2.0}
    t = L.furnace_table(mdf, row, vals, 11000.0)
    assert t.loc[row, "Price_Rs_t"] == 7000.0 and t.loc[row, "RM_Stock"] == 11000.0
    assert t.loc[row, "Moisture_Pct"] == mdf.loc[row, "Moisture_Pct"]         # moisture stays from the MBF Excel
    other = [x for x in mdf.index if x != row and str(mdf.loc[x, "Group"]) != "Sinter"]
    assert (t.loc[other, "Price_Rs_t"] == mdf.loc[other, "Price_Rs_t"]).all()
