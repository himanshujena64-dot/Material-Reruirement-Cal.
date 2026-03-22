import streamlit as st
import pandas as pd

st.title("📊 MRP Shortage Tool")

# =========================
# FILE UPLOAD
# =========================
bom_file = st.file_uploader("Upload BOM file", type=["xlsx"])
req_file = st.file_uploader("Upload Requirement + Stock file", type=["xlsx"])

if st.button("Run MRP"):

    if bom_file and req_file:

        # =========================
        # SAFE EXCEL LOAD (FIXED)
        # =========================
        try:
            bom = pd.read_excel(bom_file, dtype=str, engine="openpyxl")
            req = pd.read_excel(req_file, sheet_name="Requirement", dtype=str, engine="openpyxl")
            stock = pd.read_excel(req_file, sheet_name="Stock", dtype=str, engine="openpyxl")
        except Exception as e:
            st.error(f"Error reading Excel file: {e}")
            st.stop()

        # =========================
        # CLEAN COLUMNS
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
        # NUMERIC FIX
        # =========================
        bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")

        if "Required Qty" in bom.columns:
            bom["Quantity"] = bom["Required Qty"]

        bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)

        # =========================
        # FIX STOCK (COMMA ISSUE)
        # =========================
        stock = stock.rename(columns={"Quantity": "Stock"})
        stock["Stock"] = stock["Stock"].astype(str).str.replace(",", "")
        stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

        # =========================
        # CREATE PARENT COMPONENT
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
        # PREPARE REQUIREMENT
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

            # Phantom pass-through logic
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

            # 🔥 CRITICAL FIX: CONSOLIDATE DEMAND
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

        # Download button
        csv = pivot.to_csv(index=False).encode("utf-8")
        st.download_button("Download Output", csv, "MRP_Output.csv", "text/csv")

    else:
        st.warning("Please upload both files")