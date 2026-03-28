import streamlit as st
import pandas as pd
from io import BytesIO
import calendar

st.set_page_config(page_title="MRP Tool", layout="wide")
st.title("📊 MRP Shortage Tool (Final Clean Logic)")

# =========================
# SAFE READ
# =========================
def read_excel_safe(file, sheet_name=None):
    file.seek(0)
    for eng in ["openpyxl", "pyxlsb", "xlrd"]:
        try:
            return pd.read_excel(file, sheet_name=sheet_name, dtype=str, engine=eng)
        except:
            file.seek(0)
    return pd.read_excel(file, sheet_name=sheet_name, dtype=str)

# =========================
# NORMALIZE
# =========================
def normalize(x):
    if pd.isna(x): return ""
    x = str(x).strip()
    if x.endswith(".0"): x = x[:-2]
    return x.zfill(10)

# =========================
# MONTH SORT
# =========================
def month_sort_key(m):
    try:
        mon, yr = m.split("-")
        return (int("20"+yr), list(calendar.month_abbr).index(mon[:3]))
    except:
        return (9999, 12)

# =========================
# FILE UPLOAD
# =========================
st.sidebar.header("Upload Files")
bom_file = st.sidebar.file_uploader("Upload BOM", type=["xlsx"])
req_file = st.sidebar.file_uploader("Upload Requirement + Stock", type=["xlsx"])

# =========================
# RUN
# =========================
if st.button("Run MRP"):

    if bom_file and req_file:

        bom = read_excel_safe(bom_file, 0)
        req = read_excel_safe(req_file, "Requirement")
        stock = read_excel_safe(req_file, "Stock")

        # Clean columns
        bom.columns = bom.columns.str.strip()
        req.columns = req.columns.str.strip()
        stock.columns = stock.columns.str.strip()

        bom.rename(columns={"Alt.": "Alt"}, inplace=True)
        req.rename(columns={"Alt.": "Alt"}, inplace=True)

        # Normalize
        for col in ["Component","BOM Header"]:
            bom[col] = bom[col].apply(normalize)
        stock["Component"] = stock["Component"].apply(normalize)
        req["BOM Header"] = req["BOM Header"].apply(normalize)

        # Numeric
        bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
        bom["Required Qty"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)

        # Stock
        stock = stock.rename(columns={"Quantity":"Stock"})
        stock["Stock"] = stock["Stock"].astype(str).str.replace(",", "")
        stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

        # =========================
        # BUILD PARENT RELATION
        # =========================
        parents = []
        stack = {}

        for i in range(len(bom)):
            lvl = bom.loc[i, "Level"]
            comp = bom.loc[i, "Component"]

            if lvl == 1:
                parent = bom.loc[i, "BOM Header"]
            else:
                parent = stack.get(lvl - 1, bom.loc[i, "BOM Header"])

            parents.append(parent)
            stack[lvl] = comp

        bom["Parent Component"] = parents

        # =========================
        # REQUIREMENT PREP
        # =========================
        req_long = req.melt(
            id_vars=["BOM Header","Alt"],
            var_name="Month",
            value_name="Demand"
        )

        req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
        req_long = req_long[req_long["Demand"] > 0]
        req_long = req_long.rename(columns={"BOM Header":"Component"})

        current = req_long.copy()
        max_level = int(bom["Level"].max())

        trace_rows = []

        # =========================
        # 🔥 FINAL MRP ENGINE
        # =========================
        for lvl in range(1, max_level + 1):

            level_bom = bom[bom["Level"] == lvl]

            merged = current.merge(
                level_bom,
                left_on=["Component","Alt"],
                right_on=["Parent Component","Alt"],
                how="inner"
            )

            if merged.empty:
                continue

            merged["IsPhantom"] = merged["Special procurement"].astype(str).str.strip() == "50"

            # =========================
            # NORMAL COMPONENT
            # =========================
            normal = merged[~merged["IsPhantom"]].copy()

            normal["Required"] = normal["Demand"] * normal["Required Qty"]

            # TRACE
            for _, r in normal.iterrows():
                trace_rows.append({
                    "Level": lvl,
                    "Parent": r["Parent Component"],
                    "Component": r["Component_y"],
                    "Month": r["Month"],
                    "Demand_In": r["Demand"],
                    "Req_Qty": r["Required Qty"],
                    "Phantom": "No",
                    "Required": r["Required"]
                })

            normal = normal.merge(
                stock,
                left_on="Component_y",
                right_on="Component",
                how="left"
            )

            normal["Stock"] = normal["Stock"].fillna(0)

            normal["Shortage"] = (normal["Required"] - normal["Stock"]).clip(lower=0)

            normal_next = normal[[
                "Component_y","Month","Alt","Shortage"
            ]].rename(columns={
                "Component_y":"Component",
                "Shortage":"Demand"
            })

            # =========================
            # PHANTOM (PASS THROUGH)
            # =========================
            phantom = merged[merged["IsPhantom"]].copy()

            for _, r in phantom.iterrows():
                trace_rows.append({
                    "Level": lvl,
                    "Parent": r["Parent Component"],
                    "Component": r["Component_y"],
                    "Month": r["Month"],
                    "Demand_In": r["Demand"],
                    "Req_Qty": 1,
                    "Phantom": "Yes",
                    "Required": r["Demand"]
                })

            phantom_next = phantom[[
                "Component_y","Month","Alt","Demand"
            ]].rename(columns={"Component_y":"Component"})

            # =========================
            # COMBINE
            # =========================
            current = pd.concat([normal_next, phantom_next], ignore_index=True)

            current = current.groupby(
                ["Component","Month","Alt"], as_index=False
            )["Demand"].sum()

        # =========================
        # FINAL OUTPUT
        # =========================
        demand = current.groupby(["Component","Month"])["Demand"].sum().reset_index()

        pivot = demand.pivot(index="Component", columns="Month", values="Demand").fillna(0).reset_index()

        pivot = pivot.merge(stock,on="Component",how="left")
        pivot["Stock"] = pivot["Stock"].fillna(0)

        extra = bom[[
            "Component","Component descriptio",
            "Procurement type","Special procurement"
        ]].drop_duplicates()

        pivot = pivot.merge(extra,on="Component",how="left")

        # Sort months
        month_cols = sorted(
            [c for c in pivot.columns if "-" in c],
            key=month_sort_key
        )

        # Stock flow
        balance = pivot["Stock"].copy()

        for m in month_cols:
            net = balance - pivot[m]
            pivot[m] = net
            balance = net.clip(lower=0)

        pivot = pivot[[
            "Component","Component descriptio",
            "Procurement type","Special procurement","Stock"
        ] + month_cols]

        st.success("✅ FINAL CORRECT RESULT")
        st.dataframe(pivot, use_container_width=True)

        # =========================
        # TRACE VIEW
        # =========================
        trace_df = pd.DataFrame(trace_rows)

        trace_filtered = trace_df[
            (trace_df["Component"] == "0010300601DEL") &
            (trace_df["Month"] == "Jan-26")
        ].sort_values(by=["Level"])

        st.subheader("🔍 Trace (0010300601DEL - Jan-26)")
        st.dataframe(trace_filtered, use_container_width=True)

        # =========================
        # DOWNLOAD
        # =========================
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            pivot.to_excel(writer, index=False, sheet_name="MRP Output")
            trace_filtered.to_excel(writer, index=False, sheet_name="Trace_Jan")

        st.download_button("📥 Download Excel", output.getvalue(), "MRP_Final.xlsx")

    else:
        st.warning("Upload both files")