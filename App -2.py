import streamlit as st
import pandas as pd
from io import BytesIO

# --- 1. PAGE CONFIG ---
st.set_page_config(page_title="MRP Shortage Tool", page_icon="⚙️", layout="wide")

# --- 2. LOGIN SYSTEM ---
def check_password():
    def password_entered():
        if (st.session_state["username"] in st.secrets["passwords"] and 
            st.session_state["password"] == st.secrets["passwords"][st.session_state["username"]]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("### 🔐 Production Planning Login")
        st.text_input("User ID", on_change=password_entered, key="username")
        st.text_input("Passcode", type="password", on_change=password_entered, key="password")
        return False
    return st.session_state.get("password_correct", False)

# --- 3. THE APP ---
if check_password():
    st.title("⚙️ MRP Analysis (Phantom Logic Fixed)")
    
    with st.sidebar:
        st.header("📂 Data Upload")
        bom_file = st.file_uploader("1. BOM Master File", type=["xlsx", "xls", "xlsb"])
        req_file = st.file_uploader("2. Req & Stock File", type=["xlsx", "xls", "xlsb"])

    def read_excel_safe(uploaded_file, sheet_name=None):
        uploaded_file.seek(0)
        for engine in ["openpyxl", "pyxlsb", "xlrd"]:
            try: return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str, engine=engine)
            except: continue
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)

    if bom_file and req_file:
        if st.sidebar.button("🚀 Run MRP Engine"):
            # Load Data
            bom = read_excel_safe(bom_file, sheet_name=0)
            req = read_excel_safe(req_file, sheet_name="Requirement")
            stock = read_excel_safe(req_file, sheet_name="Stock")
            
            # Clean Columns
            bom.columns = bom.columns.str.strip()
            req.columns = req.columns.str.strip()
            stock.columns = stock.columns.str.strip()
            
            # Map column names based on your SAP Screenshot
            bom.rename(columns={"Alt.": "Alt", "SP type": "SP", "Component number": "Component"}, inplace=True)
            req.rename(columns={"Alt.": "Alt"}, inplace=True)

            # Normalize IDs
            def normalize(x):
                if pd.isna(x): return ""
                x = str(x).strip()
                if x.endswith(".0"): x = x[:-2]
                return x.zfill(10)

            bom["Component"] = bom["Component"].apply(normalize)
            bom["BOM Header"] = bom["BOM Header"].apply(normalize)
            stock["Component"] = stock.iloc[:, 0].apply(normalize) # Assume 1st col is component
            req["BOM Header"] = req["BOM Header"].apply(normalize)
            
            # Numeric conversion
            bom["Level"] = pd.to_numeric(bom["L.."], errors="coerce") # 'L..' from your image
            bom["Quantity"] = pd.to_numeric(bom["Comp. Qty (CUn)"], errors="coerce").fillna(0)
            stock["Stock"] = pd.to_numeric(stock.iloc[:, 1], errors="coerce").fillna(0) # Assume 2nd col is Stock

            # --- PHANTOM PRE-PROCESSING ---
            # We need to know if the Component ITSELF is a phantom to treat its children correctly
            phantom_map = bom.set_index("Component")["SP"].to_dict()

            # Build Parent Component mapping
            parents = []
            is_parent_phantom = []
            stack = {}
            for i in range(len(bom)):
                lvl = bom.loc[i, "Level"]
                comp = bom.loc[i, "Component"]
                
                # If level 1, parent is the Header. Otherwise, parent is the component from level-1
                p_comp = bom.loc[i, "BOM Header"] if lvl == 1 else stack.get(lvl - 1)
                parents.append(p_comp)
                
                # Check if this parent is a Phantom (SP 50)
                # We check the SP of the parent component
                p_sp = phantom_map.get(p_comp, "0")
                is_parent_phantom.append(True if str(p_sp) == "50" else False)
                
                stack[lvl] = comp

            bom["Parent Component"] = parents
            bom["Parent_Is_Phantom"] = is_parent_phantom

            # --- EXPLOSION ---
            req_long = req.melt(id_vars=["BOM Header", "Alt"], var_name="Month", value_name="Demand")
            req_long["Demand"] = pd.to_numeric(req_long["Demand"], errors="coerce").fillna(0)
            req_long = req_long[req_long["Demand"] > 0].rename(columns={"BOM Header": "Component"})

            current = req_long.copy()
            results = []
            max_level = int(bom["Level"].max())

            for lvl in range(1, max_level + 1):
                level_bom = bom[bom["Level"] == lvl]
                merged = current.merge(level_bom, left_on=["Component", "Alt"], right_on=["Parent Component", "Alt"], how="inner")
                
                if merged.empty: continue

                # THE CRITICAL LOGIC CHANGE:
                # If Parent is Phantom, we ignore the Parent's 'Quantity' and use 1.
                # Requirement = Demand (from level above) * (1 if Phantom else Quantity)
                merged["Gross_Req"] = merged.apply(
                    lambda x: x["Demand"] * 1 if x["Parent_Is_Phantom"] else x["Demand"] * x["Quantity"], 
                    axis=1
                )

                grouped = merged.groupby(["Component_y", "Month", "Alt"], as_index=False)["Gross_Req"].sum()
                grouped = grouped.rename(columns={"Component_y": "Component", "Gross_Req": "Required"})

                # Subtract Stock
                grouped = grouped.merge(stock, left_on="Component", right_on=stock.columns[0], how="left").fillna(0)
                grouped["Shortage"] = (grouped["Required"] - grouped["Stock"]).clip(lower=0)

                results.append(grouped)
                current = grouped[["Component", "Month", "Alt", "Shortage"]].rename(columns={"Shortage": "Demand"})

            if results:
                final_df = pd.concat(results, ignore_index=True)
                # Pivot and display results
                st.write("### MRP Results")
                st.dataframe(final_df.head(20))
            else:
                st.error("No data generated. Check BOM Levels and Alt matches.")