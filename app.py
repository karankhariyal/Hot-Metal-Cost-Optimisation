"""
Hospet Cost Studio: sinter model, MBF model, the combined hot metal cost model and the sinter-to-hot-metal impact model in one app.

  streamlit run app.py

Landing page: Combined cost model > Hot metal cost.  The original sinter dashboard (sinter/app.py) and MBF dashboard
(mbf/app.py) are drawn by their own code inside this app (see shared/nsrun.py for the few mechanical, in-memory
adjustments).  Navigation is one sidebar: four collapsible sections (one per model), a search box and a compact icon rail.
"""
import streamlit as st  # noqa: E402

st.set_page_config(page_title="Hospet Cost Studio", page_icon="⚙️", layout="wide", initial_sidebar_state="expanded")

from shared import nsrun, theme  # noqa: E402
from combined import handoff as H, page as CP  # noqa: E402
from impact import page as IP  # noqa: E402
from combined import single as SP  # noqa: E402

WORKSPACES = {
    "combined": ("Combined cost model", ":material/hub:", [("Setup", ["Uploads & shared settings"]), ("Results", ["Optimise hot metal cost", "Hot metal cost"]),
                                                          ("Analysis", ["Basicity scan", "Price drivers", "Saved scenarios"]), ("Reports", ["Export", "Glossary"])]),
    "impact": ("Sinter-to-hot-metal impact", ":material/timeline:", [("Setup", ["Baseline & changes"]), ("Results", ["Impact"]),
                                                                   ("Analysis", ["Optimise sinter inputs", "What each condition costs", "Impact scenarios"]), ("Reports", ["Export"])]),
    "sinter": ("Sinter model", ":material/local_fire_department:", [("Setup", ["Upload & Settings", "Inputs", "RM Stock & Materials"]),
                                                                    ("Results", ["Dashboard", "Recipe & Composition"]),
                                                                    ("Analysis", ["Inventory Usage", "Manual Burden Control", "Scenario Analysis", "Plant Run Validation", "Productivity", "Wet Specific Consumption"]),
                                                                    ("Reports", ["Reports"])]),
    "mbf": ("MBF model", ":material/factory:", [("Setup", ["Upload & settings", "Inputs", "Materials & stock"]), ("Results", ["Dashboard", "Burden & cost"]),
                                                ("Analysis", ["Slag oxides", "Trends", "Scenario analysis", "Heat audit"]), ("Reports", ["Reports & export"])]),
}
ICON = {
    "Uploads & shared settings": "upload_file", "Hot metal cost": "payments", "Optimise hot metal cost": "auto_fix_high", "Optimise sinter inputs": "auto_fix_high", "Basicity scan": "science", "Price drivers": "price_change",
    "Saved scenarios": "bookmark", "Export": "download", "Glossary": "menu_book",
    "Baseline & changes": "edit_note", "Impact": "timeline", "What each condition costs": "rule", "Impact scenarios": "bookmarks",
    "Upload & Settings": "upload_file", "Inputs": "tune", "RM Stock & Materials": "inventory_2", "Dashboard": "dashboard",
    "Recipe & Composition": "receipt_long", "Inventory Usage": "warehouse", "Manual Burden Control": "tune", "Scenario Analysis": "query_stats",
    "Plant Run Validation": "fact_check", "Productivity": "speed", "Wet Specific Consumption": "water_drop", "Reports": "description",
    "Upload & settings": "upload_file", "Materials & stock": "inventory_2", "Burden & cost": "layers", "Slag oxides": "science", "Trends": "trending_up",
    "Scenario analysis": "query_stats", "Heat audit": "local_fire_department", "Reports & export": "description",
}
DEFAULT_PAGE = {"combined": "Optimise hot metal cost", "impact": "Baseline & changes", "sinter": "Dashboard", "mbf": "Dashboard"}
W = CP.W

S = st.session_state
for k in WORKSPACES:
    S.setdefault(f"route_{k}", DEFAULT_PAGE[k])
S.setdefault("ws", "combined")
S.setdefault("mbf_sinter_mode", H.MODEL)
S.setdefault("nav_compact", False)

sns, mns = H.boot()            # both dashboards' state is created here (nothing is drawn)


# ---------------------------------------------------------------------------------- sidebar
def go(ws_key, page_name):
    S["ws"] = ws_key
    S[f"route_{ws_key}"] = page_name
    st.rerun()


def nav_button(ws_key, item, key_prefix="nav", label=None):
    active = S["ws"] == ws_key and S[f"route_{ws_key}"] == item
    if st.button(label or item, key=f"{key_prefix}_{ws_key}_{item}", icon=f":material/{ICON.get(item, 'chevron_right')}:",
                 type="primary" if active else "secondary", **W):
        go(ws_key, item)


with st.sidebar:
    head, tog = st.columns([5, 1], vertical_alignment="center")
    compact = S["nav_compact"]
    if not compact:
        head.markdown("<div class='brand-row'><span class='brand-mark'></span><span class='brand'>Hospet Cost Studio</span></div>"
                      "<div class='brand-sub'>Sinter plant + blast furnace: cost of one tonne of hot metal</div>", unsafe_allow_html=True)
    else:
        head.markdown("<div class='brand-row'><span class='brand-mark'></span></div>", unsafe_allow_html=True)
    if tog.button("", key="nav_toggle", icon=":material/left_panel_open:" if compact else ":material/left_panel_close:", help="Icon rail / full menu"):
        S["nav_compact"] = not compact
        st.rerun()

    if compact:
        for k, (name, icon, _groups) in WORKSPACES.items():
            if st.button("", key=f"rail_ws_{k}", icon=icon, help=name, type="primary" if S["ws"] == k else "secondary", **W):
                go(k, S[f"route_{k}"])
        st.markdown("<div class='rail-sep'></div>", unsafe_allow_html=True)
        for _g, items in WORKSPACES[S["ws"]][2]:
            for it in items:
                if st.button("", key=f"rail_{S['ws']}_{it}", icon=f":material/{ICON.get(it, 'chevron_right')}:", help=it,
                             type="primary" if S[f"route_{S['ws']}"] == it else "secondary", **W):
                    go(S["ws"], it)
    else:
        q = st.text_input("Search pages", key="nav_q", placeholder="Search pages", label_visibility="collapsed", icon=":material/search:").strip().lower()
        if q:
            hits = [(k, it) for k, (_n, _i, groups) in WORKSPACES.items() for _g, items in groups for it in items if q in it.lower() or q in WORKSPACES[k][0].lower()]
            st.markdown("<div class='nav-group'>Results</div>", unsafe_allow_html=True)
            if not hits:
                st.markdown("<div class='small' style='padding:.3rem .5rem'>No page matches.</div>", unsafe_allow_html=True)
            for k, it in hits:
                nav_button(k, it, "navq", label=f"{it}  ·  {WORKSPACES[k][0]}")
        else:
            for k, (name, icon, groups) in WORKSPACES.items():
                with st.container(key=f"wsbox_{k}"):                      # a keyed box, so each model's section can carry its own colour (shared/theme.py)
                    with st.expander(name, expanded=(S["ws"] == k), icon=icon):
                        for group, items in groups:
                            st.markdown(f"<div class='nav-group'>{group}</div>", unsafe_allow_html=True)
                            for it in items:
                                nav_button(k, it)
    foot = st.container()

ws = S["ws"]
st.markdown(theme.css(ws, compact=S["nav_compact"]), unsafe_allow_html=True)
page = S[f"route_{ws}"]


def mbf_om_control():
    """Furnace O&M, Rs per tHM, editable at the top of every MBF page (the same setting as MBF > Inputs > Prices and the Dashboard's quick inputs)."""
    ms = nsrun.state("mbf")
    now = float(ms.cfg.om_rs_thm)
    a, b = st.columns([1.2, 3], vertical_alignment="center")
    new = a.number_input("Furnace O&M, Rs per tHM", min_value=0.0, max_value=100_000.0, value=now, step=50.0, format="%.0f", key=f"mbf_om_banner_{ms.cfg_ver}",
                         help="Operations and maintenance cost of the furnace, added to the raw-material cost. It is part of the hot metal cost and does not change the burden.")
    b.markdown("<div class='small'>Added to the raw-material cost in every cost per tonne of hot metal. The same setting is on <b>Inputs &rsaquo; Prices</b> and in the Dashboard's quick inputs; "
               "the combined and impact models use it too.</div>", unsafe_allow_html=True)
    if abs(new - now) > 1e-9:
        ms.cfg.om_rs_thm = float(new)
        ms.changed, ms.changed_source = True, "O&M cost"
        ms.cfg_ver += 1
        st.rerun()


def mbf_banner():
    mbf_om_control()
    mode = st.radio("Sinter row in the furnace table", [H.MODEL, H.TYPED], key="mbf_sinter_mode", horizontal=True,
                    help="'From sinter model' replaces the switched-on Sinter row with the sinter result (the combined run's when one is current, "
                         "else the sinter dashboard's last run). 'Typed row' is the MBF dashboard exactly as it was.")
    info = H.sync_mbf_sinter_row(sns, mode)
    st_ = info["state"]
    if st_ == "typed":
        st.markdown("<div class='notice w'><b>Typed row (original behaviour).</b> The furnace uses the sinter values in its materials table. The combined cost model does not use this setting: it always takes sinter from the sinter model.</div>", unsafe_allow_html=True)
    elif st_ == "norow":
        st.markdown("<div class='notice r'><b>No Sinter row is switched on.</b> Switch one on under Materials &amp; stock. The sinter model's result takes its place.</div>", unsafe_allow_html=True)
    elif st_ == "missing":
        st.markdown("<div class='notice r'><b>The sinter model has not been run.</b> The sinter row comes from it, so the furnace cannot run yet. Run the sinter model, or choose Typed row.</div>", unsafe_allow_html=True)
        if st.button("Open the sinter model", key="mbf_open_sinter"):
            go("sinter", "Dashboard")
    else:
        h = info["handoff"]
        v = h["vals"]
        extra = ""
        if h["stale"]:
            extra += " <b>Sinter inputs have changed since that run:</b> run the sinter model again."
        if h["status"] != "Optimal":
            extra += f" Sinter status is {h['status']}: read costs as an estimate."
        kind = "w" if (h["stale"] or h["status"] != "Optimal") else "g"
        if h.get("source") == "combined":
            head = (f"<b>Sinter row from the combined run</b> at {h['time']:%H:%M}: {h['tonnes']:,.0f} t of sinter, plan {h['plan']:,.0f} tHM"
                    + (f", sinter capped at {h['cap']:,.0f} t" if h.get("cap") else "") + ". This page and the combined page now give the same sinter share and cost.")
        else:
            head = (f"<b>Sinter row from the sinter dashboard's last run</b> at {h['time']:%H:%M}, {h['tonnes']:,.0f} t assumed. "
                    "The combined page re-runs the sinter model at the tonnage the furnace needs, so its sinter share and cost can differ until you press "
                    "<b>Run both models</b> there; after that this page follows the combined run.")
            kind = "w" if kind == "g" else kind
        st.markdown(f"<div class='notice {kind}'>{head}<br>₹{v['Price_Rs_t']:,.0f} per t, Fe {v['Fe']:.2f}, CaO {v['CaO']:.2f}, SiO2 {v['SiO2']:.2f}, "
                    f"Al2O3 {v['Al2O3']:.2f}, MgO {v['MgO']:.2f}; moisture, S and Mn stay from the MBF table.{extra}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------------- the page
st.markdown("<div class='page-wrap'></div>", unsafe_allow_html=True)
if ws == "combined":
    SP.page(sns, mns) if page == "Optimise hot metal cost" else CP.render(page, sns, mns)
elif ws == "impact":
    IP.render(page, sns, mns)
elif ws == "sinter":
    nsrun.load_app("sinter", render=True)
    H.track_sinter_run(sns)
else:
    mbf_banner()
    nsrun.load_app("mbf", render=True, extra_blockers=lambda: H.mbf_extra_blockers(sns))

# ---------------------------------------------------------------------------------- sidebar footer (after the page, so it is current)
with foot:
    if not S["nav_compact"]:
        sl, _ = sns["status_label"]()
        ml, _ = mns["status_label"]()
        run = S.get("cmb_run")
        cl = (f"run {run['time']:%H:%M}, {'knife-edge' if run.get('jump') else ('converged' if run['converged'] else 'not converged')}" if run and run.get("ok") else "not run")
        ir = S.get("imp_res")
        il = (f"{ir['total']:+,.0f} Rs/tHM" if ir and ir.get("ok") else "no result yet")
        mc = theme.MODEL_COLOR
        st.markdown(f"<div class='side-foot'><b style='color:{mc['sinter']}'>Sinter model</b><br>{sl.split(' — ')[0].title()}<br><br><b style='color:{mc['mbf']}'>MBF model</b><br>{ml}<br><br>"
                    f"<b style='color:{mc['combined']}'>Combined</b><br>{cl}<br><br><b style='color:{mc['impact']}'>Impact</b><br>{il}</div>",
                    unsafe_allow_html=True)
