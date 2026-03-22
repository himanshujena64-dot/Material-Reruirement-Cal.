import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="MRP Shortage Tool", layout="wide")
st.title("📊 MRP Shortage Tool")

# =========================
# UTILITY: SAFE EXCEL READER
# =========================
def read_excel_safe(uploaded_file, sheet_name=None):
    """Handles pointer resets and multiple engines for Streamlit Cloud."""
    uploaded_file.seek(0)
    engines = ["openpyxl", "pyxlsb", "xlrd"]
    for engine in engines:
        try:
            return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
        except:
            uploaded_file.seek(0)
            continue
    return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

# =========================
# 1. FILE UPLOADERS
# =========================
st.sidebar.header("Data Input")
bom_file = st.sidebar.file_uploader("Upload BOM file (e.g., bom as on 1503.XLSX)", type=["xlsx", "xls", "xlsb"])
req_file = st.sidebar.file_uploader("Upload Req and Stock file", type=["xlsx", "xls", "xlsb"])

if st.button("Run MRP Analysis"):
    if bom_file and req_file:
        with st.spinner("Executing MRP Logic..."):
            # =========================
            # 1. LOAD FILES
            # =========================
            bom = read_excel_safe(bom_file)
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")

            # =========================
            # 2. CLEAN COLUMN NAMES
            # =========================
            bom.columns = bom.columns.str.strip()
            req.columns = req.columns.str.strip()
            stock.columns = stock.columns.str.strip()

            bom.rename(columns={"Alt.": "Alt"}, inplace=True)
            req.rename(columns={"Alt.": "Alt"}, inplace=True)

            # =========================
            # 3. NORMALIZE MATERIAL
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
            # 4. NUMERIC FIX
            # =========================
            bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")

            if "Required Qty" in bom.columns:
                bom["Quantity"] = bom["Required Qty"]

            bom["Quantity"] = pd.to_numeric(bom["Quantity"], errors="coerce").fillna(0)

            # =========================
            # 5. FIX STOCK (COMMA ISSUE)
            # =========================
            stock = stock.rename(columns={"Quantity": "Stock"})
            stock["Stock"] = stock["Stock"].astype(str).str.replace(",", "")
            stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

            # =========================
            # 6. CREATE PARENT COMPONENT
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
            # 7. REQUIREMENT PREP
            # =========================
            req_long = req.melt(
                id_vars=["BOM Header", "Alt"],
                var_name="Month",
                value_name="Demand"
            )

            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0]
            req_long = req_long.rename(columns={"BOM Header": "Component"})

            # =========================
            # 8. INITIAL DEMAND
            # =========================
            current = req_long.copy()
            results = []
            max_level = int(bom["Level"].max())

            # =========================
            # 9. MRP ENGINE (KEEPING YOUR LOGIC)
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

                # Remove duplicate paths
                merged = merged.drop_duplicates(
                    subset=["Parent Component", "Component_y", "Month", "Alt"]
                )

                # Aggregate
                grouped = merged.groupby(
                    ["Component_y", "Month", "Alt"], as_index=False
                )["Required"].sum()

                grouped = grouped.rename(columns={"Component_y": "Component"})

                # Merge stock
                grouped = grouped.merge(stock, on="Component", how="left")
                grouped["Stock"] = grouped["Stock"].fillna(0)

                # Shortage
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                results.append(grouped)

                # 🔥 CONSOLIDATE DEMAND BEFORE NEXT LEVEL
                grouped["Demand"] = grouped["Shortage"]

                current = grouped.groupby(
                    ["Component", "Month", "Alt"], as_index=False
                )["Demand"].sum()

            # =========================
            # 10. COMBINE
            # =========================
            if results:
                all_req = pd.concat(results, ignore_index=True)
                demand = all_req.groupby(["Component", "Month"])["Required"].sum().reset_index()

                # =========================
                # 11. PIVOT
                # =========================
                pivot = demand.pivot(index="Component", columns="Month", values="Required").fillna(0)
                pivot = pivot.reset_index()

                # =========================
                # 12. MERGE STOCK
                # =========================
                pivot = pivot.merge(stock, on="Component", how="left")
                pivot["Stock"] = pivot["Stock"].fillna(0)

                # =========================
                # 13. ADD MASTER DATA
                # =========================
                extra = bom[[
                    "Component",
                    "Component descriptio",
                    "Procurement type",
                    "Special procurement"
                ]].drop_duplicates()

                pivot = pivot.merge(extra, on="Component", how="left")

                # =========================
                # 14. MONTH ORDER
                # =========================
                month_order = ["Jan-26", "Feb-26", "Mar-26", "Apr-26", "May-26"]
                month_cols = [m for m in month_order if m in pivot.columns]

                # =========================
                # 15. CUMULATIVE
                # =========================
                for i, m in enumerate(month_cols):
                    if i == 0:
                        pivot[m] = pivot["Stock"] - pivot[m]
                    else:
                        pivot[m] = pivot[month_cols[i-1]] - pivot[m]

                # =========================
                # 16. FINAL OUTPUT
                # =========================
                pivot = pivot.groupby("Component", as_index=False).agg({
                    "Stock": "first",
                    **{m: "sum" for m in month_cols},
                    "Component descriptio": "first",
                    "Procurement type": "first",
                    "Special procurement": "first"
                })

                pivot = pivot[
                    ["Component", "Component descriptio", "Procurement type", "Special procurement", "Stock"]
                    + month_cols
                ]

                # =========================
                # DISPLAY & EXPORT
                # =========================
                st.success("MRP Calculation Complete!")
                st.dataframe(pivot, use_container_width=True)

                output = BytesIO()
                with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                    pivot.to_excel(writer, index=False)
                
                st.download_button(
                    label="📥 Download MRP_Final_Output.xlsx",
                    data=output.getvalue(),
                    file_name="MRP_Final_Output.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("No requirements were processed. Check your input data.")
    else:
        st.info("Please upload both files to start the calculation.")