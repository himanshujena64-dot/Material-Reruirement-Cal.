import streamlit as st
import pandas as pd
from io import BytesIO
import calendar

st.set_page_config(page_title="MRP Shortage Tool", layout="wide")
st.title("📊 MRP Shortage Tool (Correct SAP Logic)")

# =========================
# SAFE EXCEL READER
# =========================
def read_excel_safe(uploaded_file, sheet_name=None):
    uploaded_file.seek(0)
    engines = ["openpyxl", "pyxlsb", "xlrd"]
    for engine in engines:
        try:
            return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
        except:
            uploaded_file.seek(0)
    return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

# =========================
# NORMALIZE
# =========================
def normalize(x):
    if pd.isna(x):
        return ""
    x = str(x).strip()
    if x.endswith(".0"):
        x = x[:-2]
    return x.zfill(10)

# =========================
# MONTH SORT
# =========================
def month_sort_key(m):
    try:
        mon, yr = m.split("-")
        return (int("20" + yr), list(calendar.month_abbr).index(mon[:3]))
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

        bom = read_excel_safe(bom_file, sheet_name=0)
        req = read_excel_safe(req_file, sheet_name="Requirement")
        stock = read_excel_safe(req_file, sheet_name="Stock")

        # Clean
        bom.columns = bom.columns.str.strip()
        req.columns = req.columns.str.strip()
        stock.columns = stock.columns.str.strip()

        bom.rename(columns={"Alt.": "Alt"}, inplace=True)
        req.rename(columns={"Alt.": "Alt"}, inplace=True)

        # Normalize
        bom["Component"] = bom["Component"].apply(normalize)
        bom["BOM Header"] = bom["BOM Header"].apply(normalize)
        stock["Component"] = stock["Component"].apply(normalize)
        req["BOM Header"] = req["BOM Header"].apply(normalize)

        # Numeric
        bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
        bom["Quantity"] = pd.to_numeric(bom["Required Qty"], errors="coerce").fillna(0)

        # Stock
        stock = stock.rename(columns={"Quantity": "Stock"})
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
            id_vars=["BOM Header", "Alt"],
            var_name="Month",
            value_name="Demand"
        )

        req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
        req_long = req_long[req_long["Demand"] > 0]
        req_long = req_long.rename(columns={"BOM Header": "Component"})

        current = req_long.copy()
        max_level = int(bom["Level"].max())

        # =========================
        # 🔥 MRP ENGINE (FINAL CORRECT)
        # =========================
        for lvl in range(1, max_level + 1):

            level_bom = bom[bom["Level"] == lvl]

            merged = current.merge(
                level_bom,
                left_on=["Component", "Alt"],
                right_on=["Parent Component", "Alt"],
                how="inner"
            )

            if merged.empty:
                continue

            # Identify phantom
            merged["IsPhantom"] = merged["Special procurement"].astype(str).str.strip() == "50"

            # SPLIT LOGIC (CRITICAL FIX)
            phantom_df = merged[merged["IsPhantom"]].copy()
            normal_df = merged[~merged["IsPhantom"]].copy()

            # Normal → multiply
            normal_df["Required"] = normal_df["Demand"] * normal_df["Quantity"]

            # Phantom → pass demand only
            phantom_df["Required"] = phantom_df["Demand"]

            merged = pd.concat([normal_df, phantom_df], ignore_index=True)

            # Aggregate
            grouped = merged.groupby(
                ["Component_y", "Month", "Alt"], as_index=False
            )["Required"].sum()

            grouped = grouped.rename(columns={"Component_y": "Component"})

            # Merge stock
            grouped = grouped.merge(stock, on="Component", how="left")
            grouped["Stock"] = grouped["Stock"].fillna(0)

            # Attach phantom flag
            phantom_map = merged[["Component_y", "IsPhantom"]].drop_duplicates()
            grouped = grouped.merge(
                phantom_map,
                left_on="Component",
                right_on="Component_y",
                how="left"
            )

            grouped["IsPhantom"] = grouped["IsPhantom"].fillna(False)

            # SHORTAGE LOGIC
            grouped["Shortage"] = grouped.apply(
                lambda x: x["Required"] if x["IsPhantom"]
                else max(x["Required"] - x["Stock"], 0),
                axis=1
            )

            # Pass to next level
            grouped["Demand"] = grouped["Shortage"]

            current = grouped.groupby(
                ["Component", "Month", "Alt"], as_index=False
            )["Demand"].sum()

        # =========================
        # FINAL OUTPUT (CORRECT)
        # =========================
        demand = current.groupby(
            ["Component", "Month"]
        )["Demand"].sum().reset_index()

        pivot = demand.pivot(
            index="Component",
            columns="Month",
            values="Demand"
        ).fillna(0).reset_index()

        pivot = pivot.merge(stock, on="Component", how="left")
        pivot["Stock"] = pivot["Stock"].fillna(0)

        extra = bom[[
            "Component",
            "Component descriptio",
            "Procurement type",
            "Special procurement"
        ]].drop_duplicates()

        pivot = pivot.merge(extra, on="Component", how="left")

        # Sort months
        month_cols = sorted(
            [c for c in pivot.columns if "-" in c],
            key=month_sort_key
        )

        # STOCK FLOW
        balance = pivot["Stock"].copy()

        for m in month_cols:
            net = balance - pivot[m]
            pivot[m] = net
            balance = net.clip(lower=0)

        pivot = pivot[[
            "Component",
            "Component descriptio",
            "Procurement type",
            "Special procurement",
            "Stock"
        ] + month_cols]

        st.success("✅ MRP Calculation Complete (Correct Logic)")
        st.dataframe(pivot, use_container_width=True)

        # Download
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            pivot.to_excel(writer, index=False)

        st.download_button(
            "📥 Download Output",
            output.getvalue(),
            "MRP_Output_Final.xlsx"
        )

    else:
        st.info("Please upload both files")