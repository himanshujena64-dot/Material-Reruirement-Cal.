import streamlit as st
import pandas as pd

# --- CORE MRP LOGIC ---
def calculate_recursive_demand(parent_pn, current_demand, current_level, target_pn, bom_df, stock_dict):
    total_gross_for_target = 0
    
    # Filter children: Match Header, Level, and Alt 10
    # Ensure all comparisons are done as strings to avoid TypeErrors
    children = bom_df[(bom_df['BOM Header'].astype(str) == str(parent_pn)) & 
                      (bom_df['Level'] == current_level + 1) & 
                      (bom_df['Alt.'] == 10)]
    
    for _, row in children.iterrows():
        child_pn = str(row['Component'])
        req_qty = float(row['Required Qty'])
        spec_proc = str(row.get('Special procurement', ''))
        
        # Multiply current demand by the quantity required per unit
        child_gross_req = current_demand * req_qty
        
        if child_pn == target_pn:
            total_gross_for_target += child_gross_req
        else:
            child_stock = float(stock_dict.get(child_pn, 0))
            
            # PHANTOM LOGIC: If Special Procurement is 50, ignore stock and pass all demand
            if spec_proc == "50":
                pass_down_qty = child_gross_req
            else:
                pass_down_qty = max(0, child_gross_req - child_stock)
            
            # Only recurse if there is still demand to fulfill
            if pass_down_qty > 0:
                total_gross_for_target += calculate_recursive_demand(
                    child_pn, pass_down_qty, current_level + 1, target_pn, bom_df, stock_dict
                )
                
    return total_gross_for_target

# --- STREAMLIT UI ---
st.set_page_config(page_title="App-2 MRP Calculator", layout="wide")
st.title("📊 App-2: MRP Requirement Calculation")

st.sidebar.header("Step 1: Upload Files")
# Only 2 Uploaders as per your requirement
bom_file = st.sidebar.file_uploader("Upload BOM File", type=['xlsx'])
data_file = st.sidebar.file_uploader("Upload Req & Stock File", type=['xlsx'])

target_part = st.sidebar.text_input("Target Component PN", value="0010300601DEL")

if bom_file and data_file:
    try:
        # Use pd.read_excel exclusively to avoid Unicode errors
        df_bom = pd.read_excel(bom_file)
        
        # Read the workbook to get sheet names
        xls = pd.ExcelFile(data_file)
        sheet_names = xls.sheet_names
        
        # Auto-detect Requirement and Stock sheets
        req_sheet = [s for s in sheet_names if 'Req' in s][0]
        stock_sheet = [s for s in sheet_names if 'Stock' in s][0]
        
        df_demand = pd.read_excel(data_file, sheet_name=req_sheet)
        df_stock = pd.read_excel(data_file, sheet_name=stock_sheet)

        # Map Stock (Your file uses 'Component' and 'Quantity')
        # Clean column names just in case there are hidden spaces
        df_stock.columns = df_stock.columns.str.strip()
        stock_dict = pd.Series(df_stock['Quantity'].values, 
                               index=df_stock['Component'].astype(str)).to_dict()

        if st.button("Calculate Requirement"):
            total_gross_req = 0
            
            # Using the exact 'Jan-26' column name from your file
            # We look for the column that contains 'Jan-26'
            demand_col = [c for c in df_demand.columns if 'Jan-26' in str(c)][0]
            
            for _, row in df_demand.iterrows():
                header = str(row['BOM Header'])
                qty = float(row[demand_col])
                
                if qty > 0:
                    total_gross_req += calculate_recursive_demand(
                        header, qty, 0, target_part, df_bom, stock_dict
                    )
            
            # Final shortage calculation for the target
            target_stock = float(stock_dict.get(target_part, 0))
            shortage = max(0, total_gross_req - target_stock)
            
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Gross Req", f"{total_gross_req:,.3f}")
            c2.metric("Available Stock", f"{target_stock:,.3f}")
            c3.metric("Net Shortage", f"{shortage:,.3f}")

            if shortage > 0:
                st.error(f"Order Required: {shortage:,.3f} units of {target_part}")
            else:
                st.success(f"Stock for {target_part} is sufficient.")

    except Exception as e:
        st.error(f"Encountered an error: {e}")
else:
    st.info("Please upload both Excel files to proceed.")
