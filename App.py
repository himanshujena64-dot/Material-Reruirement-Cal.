import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM ---
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
            with st.spinner("Calculating..."):
                # 1. LOAD DATA
                bom = read_excel_safe(bom_file, sheet_name=0)
                req = read_excel_safe(req_file, sheet_name="Requirement")
                stock = read_excel_safe(req_file, sheet_name="Stock")

                # 2. CLEAN COLUMN NAMES
                bom.columns = [str(c).strip() for c in bom.columns]
                req.columns = [str(c).strip() for c in req.columns]
                stock.columns = [str(c).strip() for c in stock.columns]
                
                bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Special procurement": "SP"}, inplace=True)
                req.rename(columns={"Alt.": "Alt"}, inplace=True)

                # 3. NORMALIZE FUNCTION (Force text and remove .0)
                def normalize(x):
                    if pd.isna(x): return ""
                    x = str(x).strip()
                    if x.endswith(".0"): x = x[:-2]
                    return x.upper() # Use Uppercase to ensure matches

                bom["Component"] = bom["Component"].apply(normalize)
                bom["BOM Header"] = bom["BOM Header"].apply(normalize)
                stock["Component"] = stock["Component"].apply(normalize)
                req["BOM Header"] = req["BOM Header"].apply(normalize)

                # Numeric conversions
                bom["Level"] = pd.to_numeric(bom["Level"], errors="coerce")
                bom["Quantity"] = pd.to_numeric(bom.get("Required Qty", bom.get("Quantity", 0)), errors="coerce").fillna(0)
                stock = stock.rename(columns={"Quantity": "Stock"})
                stock["Stock"] = pd.to_numeric(stock["Stock"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

                # 4. BUILD PARENTS
                parents = []
                stack_tracker = {}
                for i in range(len(bom)):
                    lvl = bom.loc[i, "Level"]
                    comp = bom.loc[i, "Component"]
                    p_id = bom.loc[i, "BOM Header"] if lvl == 1 else stack_tracker.get(lvl - 1)
                    parents.append(p_id)
                    stack_tracker[lvl] = comp
                bom["Parent Component"] = parents

                # 5. EXPLOSION PREP
                id_cols = ["BOM Header", "Alt"]
                month_cols = [c for c in req.columns if c not in id_cols]
                
                req_long = req.melt(id_vars=id_cols, value_vars=month_cols, var_name="Month", value_name="Demand")
                req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
                req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Parent Component"})

                current = req_long.copy()
                results = []
                max_lvl = int(bom["Level"].max())

                # 6. ENGINE LOOP
                for lvl in range(1, max_lvl + 1):
                    level_bom = bom[bom["Level"] == lvl]
                    merged = current.merge(level_bom, on=["Parent Component", "Alt"], how="inner")

                    if merged.empty:
                        continue

                    merged["Gross_Req"] = merged["Demand"] * merged["Quantity"]
                    merged = merged.merge(stock, on="Component", how="left")
                    merged["Stock"] = merged["Stock"].fillna(0)
                    
                    # Shortage Logic: Phantom (50) ignores stock
                    merged["Shortage"] = merged.apply(
                        lambda x: x["Gross_Req"] if str(x["SP"]) == "50" else max(0, x["Gross_Req"] - x["Stock"]),
                        axis=1
                    )

                    # Only append if we actually have data
                    if not merged.empty:
                        results.append(merged[["Component", "Month", "Gross_Req"]])

                    # Prep next level
                    current = merged[["Component", "Month", "Alt", "Shortage"]].rename(
                        columns={"Component": "Parent Component", "Shortage": "Demand"}
                    )

                # 7. OUTPUT
                if results:
                    all_data = pd.concat(results, ignore_index=True)
                    if not all_data.empty and "Month" in all_data.columns:
                        summary = all_data.groupby(["Component", "Month"])["Gross_Req"].sum().unstack().fillna(0).reset_index()
                        
                        # Add metadata and stock for final view
                        final_pivot = summary.merge(stock, on="Component", how="left").fillna(0)
                        extra = bom[["Component", "Component descriptio", "Procurement type", "SP"]].drop_duplicates(subset=["Component"])
                        final_pivot = final_pivot.merge(extra, on="Component", how="left")

                        st.success("✅ MRP Run Successful")
                        st.dataframe(final_pivot, use_container_width=True)

                        output = BytesIO()
                        final_pivot.to_excel(output, index=False)
                        st.download_button("📥 Download Excel Report", output.getvalue(), "MRP_Final_Summary.xlsx")
                    else:
                        st.error("No valid data found in final aggregation.")
                else:
                    st.warning("No matches found between Requirement file and BOM Master.")

    else:
        st.info("Upload your BOM and Requirement files to start.")
