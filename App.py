import streamlit as st
import pandas as pd

# --- CORE MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    total_gross_for_target = 0
    
    # Filter children: Match Header, Level, and Alt 10
    children = bom_df[(bom_df['BOM Header'].astype(str) == str(parent_pn)) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt.'] == 10)]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component'])
        # 'Required Qty' is the quantity needed per 1 unit of parent
        req_qty = float(row['Required Qty'])
        spec_proc = str(row.get('Special procurement', ''))
        
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            
            # Phantom Logic (SP 50): Pass demand through, ignore its own stock
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
st.set_page_config(page_title="App-2 MRP", layout="wide")
st.title("📦 App-2: MRP Requirement Calculator")

st.sidebar.header("Upload Files")
# Only 2 Uploaders now
bom_file = st.sidebar.file_uploader("1. Upload BOM (bom as on 1503.XLSX)", type=['xlsx'])
data_file = st.sidebar.file_uploader("2. Upload Req & Stock File", type=['xlsx'])

target_part = st.sidebar.text_input("Target Component PN", value="0010300601DEL")

if bom_file and data_file:
    try:
        # Read BOM
        df_bom = pd.read_excel(bom_file)
        
        # Read the Excel file with multiple sheets
        xls = pd.ExcelFile(data_file)
        sheet_names = xls.sheet_names
        
        # Find Requirement and Stock sheets automatically
        req_sheet = [s for s in sheet_names if 'Req' in s][0]
        stock_sheet = [s for s in sheet_names if 'Stock' in s][0]
        
        df_demand = pd.read_excel(data_file, sheet_name=req_sheet)
        df_stock = pd.read_excel(data_file, sheet_name=stock_sheet)

        # Map Stock (Clean whitespace from column names if any)
        df_stock.columns = df_stock.columns.str.strip()
        # Based on your file: 'Component' and 'Quantity' (or 'Avl. Stock')
        stock_col = 'Quantity' if 'Quantity' in df_stock.columns else 'Avl. Stock'
        stock_dict = pd.Series(df_stock[stock_col].values, 
                               index=df_stock['Component'].astype(str)).to_dict()

        if st.button("Calculate Result"):
            total_gross_req = 0
            
            # Look for the 'Jan-26' column in the Requirement sheet
            demand_col = [c for c in df_demand.columns if 'Jan-26' in str(c)][0]
            
            for _, row in df_demand.iterrows():
                header = str(row['BOM Header'])
                qty = float(row[demand_col])
                
                if qty > 0:
                    total_gross_req += calculate_recursive_demand(
                        header, qty, 0, target_part, df_bom, stock_dict
                    )
            
            # Final Stock for the Target Component
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
        st.error(f"Error: {e}")
        st.info("Check if sheet names contain 'Requirement' and 'Stock'.")
else:
    st.info("Please upload both Excel files (.xlsx) to proceed.")