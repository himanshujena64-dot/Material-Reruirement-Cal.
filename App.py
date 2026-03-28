import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM (Preserved) ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]:
        return True

    st.markdown("### 🔐 Production Planning Login")
    user = st.text_input("User ID", key="username")
    pas = st.text_input("Passcode", type="password", key="password")
    
    if st.button("Login"):
        if user in st.secrets["passwords"] and pas == st.secrets["passwords"][user]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("😕 Access Denied")
    return False

# --- 3. THE APP ---
if check_password():
    st.title("📊 MRP Shortage Analysis Dashboard")
    
    with st.sidebar:
        st.success("✅ Access Granted")
        if st.button("Logout"):
            st.session_state["password_correct"] = False
            st.rerun()
        st.markdown("---")
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

    def read_excel_safe(uploaded_file, sheet_name=None):
        uploaded_file.seek(0)
        for engine in ["openpyxl", "pyxlsb", "xlrd"]:
            try:
                return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
            except:
                continue
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

    if bom_file and req_file:
        if st.sidebar.button("🚀 Run MRP Engine"):
            with st.spinner("Processing All Components..."):
                # 1. LOAD DATA
                bom = read_excel_safe(bom_file, sheet_name=0)
                req = read_excel_safe(req_file, sheet_name="Requirement")
                stock = read_excel_safe(req_file, sheet_name="Stock")

                # 2. CLEAN & NORMALIZE
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]
                
                bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Special procurement": "SP"}, inplace=True)
                req.rename(columns={"Alt.": "Alt"}, inplace=True)

                def normalize(x):
                    if pd.isna(x): return ""
                    x = str(x).strip()
                    if x.endswith(".0"): x = x[:-2]
                    return x.zfill(10)

                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
                bom["Quantity"] = pd.to_numeric(bom.get("Required Qty", bom.get("Quantity", 0)), errors="coerce").fillna(0)
                
                stock = stock.rename(columns={"Quantity": "Stock"})
                stock["Stock"] = stock["Stock"].astype(str).str.replace(",", "")
                stock["Stock"] = pd.to_numeric(stock["Stock"], errors="coerce").fillna(0)

                # 3. BUILD PARENTS
                parents = []
                stack_tracker = {}
                for i in range(len(bom)):
                    lvl = bom.loc[i, "Level"]
                    comp = bom.loc[i, "Component"]
                    p_id = bom.loc[i, "BOM Header"] if lvl == 1 else stack_tracker.get(lvl - 1)
                    parents.append(p_id)
                    stack_tracker[lvl] = comp
                bom["Parent Component"] = parents

                # 4. EXPLOSION
                # Identify month columns (anything not Header or Alt)
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]
                
                req_long = req.melt(id_vars=id_cols, value_vars=month_cols, var_name="Month", value_name="Demand")
                req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
                req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Parent Component"})

                current = req_long.copy()
                results = []
                max_level = int(bom["Level"].max())

                for lvl in range(1, max_level + 1):
                    level_bom = bom[bom["Level"] == lvl]
                    merged = current.merge(level_bom, on=["Parent Component", "Alt"], how="inner")

                    if merged.empty: continue

                    merged["Gross_Req"] = merged["Demand"] * merged["Quantity"]
                    merged = merged.merge(stock, on="Component", how="left")
                    merged["Stock"] = merged["Stock"].fillna(0)
                    
                    # Logic: Phantom (50) ignores stock and passes full demand
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross_Req"] if str(x["SP"]) == "50" else max(0, x["Gross_Req"] - x["Stock"]),
                        axis=1
                    )

                    # Keep essential columns for the final report
                    results.append(merged[["Component", "Month", "Gross_Req"]])

                    # Prep next level
                    current = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent Component", "Shortage": "Demand"}
                    )

                # 5. FINAL REPORTING
                if results:
                    all_data = pd.concat(results, ignore_index=True)
                    # Check if 'Month' exists before grouping
                    if "Month" in all_data.columns:
                        summary = all_data.groupby(["Component", "Month"])["Gross_Req"].sum().unstack().fillna(0).reset_index()
                        final_pivot = summary.merge(stock, on="Component", how="left").fillna(0)
                        
                        extra_info = bom[["Component", "Component descriptio", "Procurement type", "SP"]].drop_duplicates(subset=["Component"])
                        final_pivot = final_pivot.merge(extra_info, on="Component", how="left")

                        st.success("Analysis Complete!")
                        st.dataframe(final_pivot, use_container_width=True)

                        output = BytesIO()
                        final_pivot.to_excel(output, index=False)
                        st.download_button("📥 Download Full Report", output.getvalue(), "MRP_Final_Report.xlsx")
                    else:
                        st.error("Data processing failed: 'Month' column lost during explosion.")
                else:
                    st.warning("No requirements found. Please check if BOM Headers and Levels match the Requirement file.")
    else:
        st.info("Please upload both files to begin.")
