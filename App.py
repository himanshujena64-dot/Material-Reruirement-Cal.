import streamlit as st
import pandas as pd
from io import BytesIO

st.title("📊 MRP Shortage Tool")

# =========================
# SAFE EXCEL READER
# =========================
def read_excel_safe(uploaded_file, sheet_name=None):
    try:
        data = BytesIO(uploaded_file.read())

        try:
            return pd.read_excel(data, sheet_name=sheet_name, dtype=str, engine="openpyxl")
        except:
            data.seek(0)
            return pd.read_excel(data, sheet_name=sheet_name, dtype=str)

    except Exception as e:
        st.error(f"Excel read failed: {e}")
        st.stop()

# =========================
# FILE UPLOAD
# =========================
bom_file = st.file_uploader("Upload BOM file", type=["xlsx"])
req_file = st.file_uploader("Upload Requirement + Stock file", type=["xlsx"])

if st.button("Run MRP"):

    if bom_file and req_file:

        # =========================
        # READ FILES (FIXED)
        # =========================
        bom = read_excel_safe(bom_file)

        req_file.seek(0)
        req = read_excel_safe(req_file, sheet_name="Requirement")

        req_file.seek(0)
        stock = read_excel_safe(req_file, sheet_name="Stock")

        # =========================
        # CLEAN
        # =========================
        bom.columns = bom.columns.str.strip()
        req.columns = req.columns.str.strip()
        stock.columns = stock.columns.str.strip()

        bom.rename(columns={"Alt.": "Alt"}, inplace=True)
        req.rename(columns={"Alt.": "Alt"}, inplace=True)

        # =========================
        # NORMALIZE MATERIAL
        # =========================
        def normalize(x):
            if pd.isna(x):
                return ""
            x = str(x).strip()
            if x.endswith(".0"):
                x = x[:-2]
            return x.zfill(10)

        bom["Component"] = bom["Component"].apply(normalize)
        bom["BOM Header"] = bom["BOM Header"].apply(normalize)
        stock["Component"] = stock["Component"].apply(normalize)
        req["BOM Header"] = req["BOM Header"].apply(normalize)

        # =========================
        # NUMERIC
        # =========================
        bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")

        if "Required Qty" in bom.columns:
            bom["Quantity"] = bom["Required Qty"]

        bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)

        # =========================
        # FIX STOCK
        # =========================
        stock = stock.rename(columns={"Quantity": "Stock"})
        stock["Stock"] = stock["Stock"].astype(str).str.replace(",", "")
        stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

        # =========================
        # CREATE PARENT
        # =========================
        parents = []
        stack = {}

        for i in range(len(bom)):
            lvl = bom.loc[i, "Level"]
            comp = bom.loc[i, "Component"]

            if lvl == 1:
                parent = bom.loc[i, "BOM Header"]
            else:
                parent = stack.get(lvl - 1)

            parents.append(parent)
            stack[lvl] = comp

        bom["Parent Component"] = parents

        # =========================
        # REQUIREMENT
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
        results = []

        max_level = int(bom["Level"].max())

        # =========================
        # MRP LOGIC
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

            # Phantom logic
            merged["Required"] = merged.apply(
                lambda x: x["Demand"] if str(x["Special procurement"]) == "50"
                else x["Demand"] * x["Quantity"],
                axis=1
            )

            grouped = merged.groupby(
                ["Component_y", "Month", "Alt"], as_index=False
            )["Required"].sum()

            grouped = grouped.rename(columns={"Component_y": "Component"})

            grouped = grouped.merge(stock, on="Component", how="left")
            grouped["Stock"] = grouped["Stock"].fillna(0)

            grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

            results.append(grouped)

            # 🔥 FIX: CONSOLIDATE DEMAND
            grouped["Demand"] = grouped["Shortage"]

            current = grouped.groupby(
                ["Component", "Month", "Alt"], as_index=False
            )["Demand"].sum()

        # =========================
        # FINAL OUTPUT
        # =========================
        final = pd.concat(results)

        pivot = final.pivot(index="Component", columns="Month", values="Required").fillna(0)
        pivot = pivot.reset_index()

        pivot = pivot.merge(stock, on="Component", how="left")
        pivot["Stock"] = pivot["Stock"].fillna(0)

        st.success("MRP Calculation Completed ✅")
        st.dataframe(pivot)

        # =========================
        # DOWNLOAD
        # =========================
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            pivot.to_excel(writer, index=False)

        st.download_button(
            label="Download Excel",
            data=output.getvalue(),
            file_name="MRP_Output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    else:
        st.warning("Please upload both files")