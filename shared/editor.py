"""
Editable material tables for the upload pages.

After a file is uploaded the user can change availability, price, stock and chemistry of every material (and add or
remove a material), and nothing reaches the model until "Confirm changes" is pressed.  Edits sit inside a form, so the
page does not re-run while typing; "Discard edits" throws them away.

  sinter_editor(sns)        -> the sinter master (st.session_state["sinter__master_df"] and ["sinter__available"])
  furnace_editor(mns)       -> the MBF materials table (the MBF dashboard's own state, through shared.nsrun)

Both only write the same state the dashboards' own pages write, so every other page and the combined run see the
edit straight away.
"""
import inspect

import numpy as np
import pandas as pd
import streamlit as st

from shared import nsrun
from sinter import optimizer as sopt
from mbf import optimiser as mopt

W = {"width": "stretch"} if "width" in inspect.signature(st.button).parameters else {"use_container_width": True}

SINTER_NUM = ["Available_Tonnes", "Price_Rs_t", "Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Moisture_Pct", "Tech_Min", "Tech_Max",
              "Fines_Pct", "CV_kcal_kg", "FC_Pct"]
SINTER_CHEM = ["Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI"]
SINTER_ORDER = ["Material", "Group", "On", "Available_Tonnes", "Price_Rs_t", "Fe", "SiO2", "Al2O3", "CaO", "MgO", "LOI", "Moisture_Pct",
                "Tech_Min", "Tech_Max", "Fines_Pct", "Material_Role", "CV_kcal_kg", "FC_Pct"]


def _ver(key):
    st.session_state.setdefault(key, 0)
    return st.session_state[key]


# ------------------------------------------------------------------------------------------------ sinter
def sinter_view(df, avail):
    v = df.copy()
    v.insert(0, "On", [bool(avail.get(m, True)) for m in df.index])
    return v.reset_index().rename(columns={df.index.name or "index": "Material"})


def sinter_from_view(edited, template):
    """Edited table -> (master DataFrame, available dict, problems).  Mirrors the rules of the master loader."""
    problems = []
    d = edited.copy()
    d["Material"] = d["Material"].astype(str).str.strip()
    d = d[d["Material"].ne("") & d["Material"].ne("nan") & d["Material"].ne("None")].copy()
    if d.empty:
        return None, None, ["The table has no materials."]
    dup = d.loc[d["Material"].duplicated(keep=False), "Material"].unique().tolist()
    if dup:
        problems.append(f"Duplicate material names: {', '.join(sorted(dup))}.")
    d["Group"] = d["Group"].astype(str).str.strip()
    bad = d.loc[d["Group"].isin(["", "nan", "None"]), "Material"].tolist()
    if bad:
        problems.append(f"Choose a group for: {', '.join(bad)}.")
    for c in SINTER_NUM:
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
        if (d[c] < 0).any():
            problems.append(f"{c} cannot be negative (see {', '.join(d.loc[d[c] < 0, 'Material'].tolist()[:4])}).")
    for c in SINTER_CHEM:
        if (d[c] > 100).any():
            problems.append(f"{c} cannot be above 100 % (see {', '.join(d.loc[d[c] > 100, 'Material'].tolist()[:4])}).")
    if (d["Moisture_Pct"] >= 100).any():
        problems.append("Moisture must be below 100 %.")
    if (d["Fines_Pct"] > 100).any():
        problems.append("Fines % must be between 0 and 100.")
    over = d[(d["Tech_Max"] > 0) & (d["Tech_Max"] < d["Tech_Min"]) & d["Group"].ne("Recycle")]
    if len(over):
        problems.append(f"Tech max is below Tech min for: {', '.join(over['Material'])}.")
    if problems:
        return None, None, problems
    rec = d["Group"].eq("Recycle")
    d.loc[rec, "Tech_Max"] = d.loc[rec, "Tech_Min"]                      # recycle = fixed rate, as in the loader
    avail = {m: bool(o) for m, o in zip(d["Material"], d["On"].fillna(False))}
    d["Material_Role"] = d["Material_Role"].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
    d = d.set_index("Material")
    if d["Material_Role"].eq("").any():                                   # new rows: let the engine assign the role
        keep = d["Material_Role"].copy()
        d2 = sopt._ensure_material_role(d.drop(columns=["Material_Role"]))
        d["Material_Role"] = [keep[m] if keep[m] else d2.loc[m, "Material_Role"] for m in d.index]
    d = sopt._ensure_material_role(d)
    cols = [c for c in template.columns if c in d.columns] + [c for c in d.columns if c not in template.columns and c != "On"]
    return d[cols], avail, []


def sinter_editor(S=None, on_change=None):
    """Editable sinter master with a Confirm step.  `S` is the dashboard's state: pass `st.session_state` from inside the sinter
    dashboard (inside the combined app that name is its prefixed view, so the same call works in both places).
    `on_change(text)` is called after a confirmed edit (the dashboard's own 'rerun required' marker)."""
    S = st.session_state if S is None else S
    if "master_df" not in S:
        return
    df, avail = S["master_df"], S["available"]
    ver = int(S.get("_sed_ver", 0))
    st.markdown("<div class='sect'>Edit the uploaded materials<small>availability, price, stock and chemistry; nothing changes until you confirm</small></div>", unsafe_allow_html=True)
    nc = st.column_config.NumberColumn
    groups = sorted(set(df["Group"].astype(str)) | {"Iron_ore", "Recycle", "Flux", "Fuel"})
    cc = {"Material": st.column_config.TextColumn("Material", required=True),
          "Group": st.column_config.SelectboxColumn("Group", options=groups, required=True),
          "On": st.column_config.CheckboxColumn("On", help="Untick to take the material out of the sinter model."),
          "Available_Tonnes": nc("Stock, t", min_value=0.0, format="%.0f"), "Price_Rs_t": nc("Price Rs/t", min_value=0.0, format="%.0f"),
          **{c: nc(f"{c} %", min_value=0.0, max_value=100.0, format="%.3f") for c in SINTER_CHEM},
          "Moisture_Pct": nc("Moisture %", min_value=0.0, max_value=99.9, format="%.2f"),
          "Tech_Min": nc("Tech min kg/t", min_value=0.0, format="%.0f"), "Tech_Max": nc("Tech max kg/t", min_value=0.0, format="%.0f"),
          "Fines_Pct": nc("Fines %", min_value=0.0, max_value=100.0, format="%.1f"),
          "Material_Role": st.column_config.TextColumn("Role"), "CV_kcal_kg": nc("CV kcal/kg", min_value=0.0, format="%.0f"),
          "FC_Pct": nc("FC %", min_value=0.0, max_value=100.0, format="%.1f")}
    view = sinter_view(df, avail)
    order = [c for c in SINTER_ORDER if c in view.columns]
    with st.form(f"sed_form_{ver}", border=False):
        edited = st.data_editor(view, key=f"sed_{ver}", num_rows="dynamic", hide_index=True, column_config=cc, column_order=order, **W)
        a, b, c = st.columns([1, 1, 4])
        ok = a.form_submit_button("Confirm changes", type="primary", **W)
        drop = b.form_submit_button("Discard edits", **W)
    if drop:
        S["_sed_ver"] = ver + 1
        st.rerun()
    if ok:
        newdf, newav, probs = sinter_from_view(edited, df)
        if probs:
            st.markdown("<div class='notice r'><b>Not applied.</b><br>" + "<br>".join(f"- {p}" for p in probs[:8]) + "</div>", unsafe_allow_html=True)
        else:
            same = (list(newdf.index) == list(df.index) and newdf.round(9).equals(df.reindex(newdf.index).round(9))
                    and newav == {m: bool(avail.get(m, True)) for m in df.index})
            if same:
                st.markdown("<div class='notice'>No changes to apply.</div>", unsafe_allow_html=True)
            else:
                S["master_df"] = newdf
                S["available"] = newav
                src = str(S.get("source", ""))
                if not src.endswith("(edited)"):
                    S["source"] = src + " (edited)"
                S["_sed_ver"] = ver + 1
                S["_sed_done"] = "Sinter materials updated. Run the sinter model again to use them."
                for k in ("rm_editor", "merged_master_editor"):          # the dashboard's other material tables start again from the new master
                    S.pop(k, None)
                if on_change:
                    on_change("Upload & Settings (materials edited)")
                st.rerun()
    if S.get("_sed_done"):
        st.markdown(f"<div class='notice g'>{S.pop('_sed_done')}</div>", unsafe_allow_html=True)
    st.caption("Add a row at the bottom for a new material. Stock is tonnes available over the planning horizon; Tech min/max (kg per t sinter) only matter "
               "for fluxes and fixed recycle rates (recycle uses Tech min for both).")


# ------------------------------------------------------------------------------------------------ furnace
def furnace_editor(mns):
    ms = nsrun.state("mbf")
    ver = _ver("_fed_ver")
    df = ms.df
    st.markdown("<div class='sect'>Edit the uploaded materials<small>on/off, price, moisture, stock and chemistry; nothing changes until you confirm</small></div>", unsafe_allow_html=True)
    nc = st.column_config.NumberColumn
    cc = {"Material": st.column_config.TextColumn("Material", required=True),
          "Group": st.column_config.SelectboxColumn("Group", options=mopt.GROUPS, required=True),
          "Available": st.column_config.CheckboxColumn("On", help="Untick to take the material out of the furnace model."),
          "Price_Rs_t": nc("Price Rs/t", min_value=0.0, format="%.0f"), "Moisture_Pct": nc("Moisture %", min_value=0.0, max_value=59.9, format="%.2f"),
          "RM_Stock": nc("RM stock, t", min_value=0.0, format="%.0f", help="Blank = unlimited. 0 = treated as unavailable."),
          "Fe": nc("Fe %", min_value=0.0, max_value=100.0, format="%.3f"), "CaO": nc("CaO %", min_value=0.0, max_value=100.0, format="%.3f"),
          "MgO": nc("MgO %", min_value=0.0, max_value=100.0, format="%.3f"), "SiO2": nc("SiO2 %", min_value=0.0, max_value=100.0, format="%.3f"),
          "Al2O3": nc("Al2O3 %", min_value=0.0, max_value=100.0, format="%.3f"), "Mn": nc("Mn %", min_value=0.0, format="%.3f"),
          "S": nc("S %", min_value=0.0, format="%.3f"), "FC": nc("FC %", min_value=0.0, max_value=100.0, format="%.2f"),
          "Fines_Pct": nc("Fines %", min_value=0.0, max_value=99.9, format="%.1f"), "Fines_Credit_Rs_t": nc("Fines credit Rs/t", min_value=0.0, format="%.0f")}
    order = ["Material", "Group", "Available", "Price_Rs_t", "Moisture_Pct", "RM_Stock", "Fe", "CaO", "MgO", "SiO2", "Al2O3", "Mn", "S", "FC", "Fines_Pct", "Fines_Credit_Rs_t"]
    with st.form(f"fed_form_{ver}", border=False):
        edited = st.data_editor(mopt.ensure_columns(df).reset_index(), key=f"fed_{ver}", num_rows="dynamic", hide_index=True,
                                column_config=cc, column_order=order, **W)
        a, b, c = st.columns([1, 1, 4])
        ok = a.form_submit_button("Confirm changes", type="primary", **W)
        drop = b.form_submit_button("Discard edits", **W)
    if drop:
        st.session_state["_fed_ver"] = ver + 1
        st.rerun()
    if ok:
        try:
            newdf = mns["frame_to_master"](edited)
            errs = mopt.validate_df(newdf)
        except Exception as e:                                           # a half-typed cell
            newdf, errs = None, [f"The table could not be read: {e}"]
        if errs:
            st.markdown("<div class='notice r'><b>Not applied.</b><br>" + "<br>".join(f"- {e}" for e in errs[:8]) + "</div>", unsafe_allow_html=True)
        elif mopt.fingerprint(newdf) == mopt.fingerprint(df):
            st.markdown("<div class='notice'>No changes to apply.</div>", unsafe_allow_html=True)
        else:
            ms.df = newdf
            if not str(ms.source).endswith("(edited)"):
                ms.source = f"{ms.source} (edited)"
            ms.mat_ver += 1
            ms.changed, ms.changed_source = True, "Materials"
            st.session_state["_fed_ver"] = ver + 1
            st.session_state["_fed_done"] = "Furnace materials updated. Run the combined model again to use them."
            st.rerun()
    if st.session_state.get("_fed_done"):
        st.markdown(f"<div class='notice g'>{st.session_state.pop('_fed_done')}</div>", unsafe_allow_html=True)
    st.caption("The switched-on Sinter row is replaced by the sinter model's result at every run (price, Fe, CaO, SiO2, Al2O3, MgO); "
               "its moisture, S and Mn stay as typed here. A stock of 0 removes a material.")
