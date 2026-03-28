import streamlit as st
import pandas as pd

# --- CORE MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    total_gross_for_target = 0
    
    # Clean strings and filter children
    parent_pn_str = str(parent_pn).strip()
    children = bom_df[(bom_df['BOM Header'].astype(str).str.strip() == parent_pn_str) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt.'] == 10)]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component']).strip()
        req_qty = float(row['Required Qty'])
        spec_proc = str(row.get('Special procurement', '')).strip()
        
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            
            # PHANTOM LOGIC (SP 50): Ignore stock, pass demand 100%
            if spec_proc == "50":
                pass_down_qty = child_gross_req
            else:
                pass_down_qty = max(0, child_gross_req - child_stock)
            
            if pass_down_qty > 0:
                total_gross_for_target += calculate_recursive_demand(
                    child_pn, pass_down_qty, current_level + 1, target_pn, bom_df, stock_dict
                )
                
    return total_gross_for_target

# --- STREAMLIT UI ---
st.set_page_config(page_title="App-2 MRP Calculator", layout="wide")
st.title("📊 App-2: MRP Requirement Calculation")

st.sidebar.header("Upload Files")
bom_file = st.sidebar.file_uploader("1. Upload BOM File", type=['xlsx'])
data_file = st.sidebar.file_uploader("2. Upload Req & Stock File", type=['xlsx'])

target_part = st.sidebar.text_input("Target Component PN", value="0010300601DEL")

if bom_file and data_file:
    try:
        # Load BOM and clean columns
        df_bom = pd.read_excel(bom_file)
        df_bom.columns = df_bom.columns.str.strip()
        
        # Load Sheets
        xls = pd.ExcelFile(data_file)
        s_names = xls.sheet_names
        req_s = [s for s in s_names if 'Req' in s][0]
        stk_s = [s for s in s_names if 'Stock' in s][0]
        
        df_demand = pd.read_excel(data_file, sheet_name=req_s)
        df_stock = pd.read_excel(data_file, sheet_name=stk_s)
        
        # Clean Dataframe Columns
        df_demand.columns = df_demand.columns.str.strip()
        df_stock.columns = df_stock.columns.str.strip()

        # Map Stock (Checks for 'Quantity' or 'Avl. Stock')
        stock_col = [c for c in df_stock.columns if 'Quantity' in c or 'Stock' in c][0]
        stock_dict = pd.Series(df_stock[stock_col].values, 
                               index=df_stock['Component'].astype(str).str.strip()).to_dict()

        if st.button("Calculate Requirement"):
            total_gross_req = 0
            
            # Find the correct Demand column (targeting 'Jan-26')
            demand_col = [c for c in df_demand.columns if 'Jan-26' in str(c)][0]
            
            for _, row in df_demand.iterrows():
                header = str(row['BOM Header']).strip()
                qty = float(row[demand_col])
                
                if qty > 0:
                    total_gross_req += calculate_recursive_demand(
                        header, qty, 0, target_part, df_bom, stock_dict
                    )
            
            # Final shortage math
            target_stock = float(stock_dict.get(target_part, 0))
            shortage = max(0, total_gross_req - target_stock)
            
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Gross Req", f"{total_gross_req:,.3f}")
            c2.metric("Available Stock", f"{target_stock:,.3f}")
            c3.metric("Net Shortage", f"{shortage:,.3f}")

            if shortage > 0:
                st.error(f"Requirement: {shortage:,.3f} units of {target_part} needed.")
            else:
                st.success(f"Stock for {target_part} is sufficient.")

    except Exception as e:
        st.error(f"Logic Error: {e}")
else:
    st.info("Please upload both .xlsx files.")
