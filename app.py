import streamlit as st
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
import pandas as pd
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def format_address(address_dict):
    """Format address dictionary into a readable string"""
    if not address_dict:
        return ""
        
    street = address_dict.get('streetAddress', '')
    if not street:
        parts = []
        if address_dict.get('houseNumber'):
            parts.append(address_dict['houseNumber'])
        if address_dict.get('road'):
            parts.append(address_dict['road'])
        if address_dict.get('unit'):
            parts.append(address_dict['unit'])
        street = ' '.join(parts)
    
    lines = [address_dict['poBox']] if address_dict.get('poBox') else [street]
    
    location_parts = []
    if address_dict.get('city'):
        location_parts.append(address_dict['city'])
    if address_dict.get('state'):
        location_parts.append(address_dict['state'])
    if location_parts:
        location_line = ', '.join(location_parts)
        if address_dict.get('postalCode'):
            location_line += f" {address_dict['postalCode']}"
        lines.append(location_line)
    
    return '\n'.join(lines)

def analyze_bank_statement(uploaded_file):
    """Analyze a single bank statement"""
    endpoint = os.getenv('AZURE_ENDPOINT')
    key = os.getenv('AZURE_KEY')
    
    if not endpoint or not key:
        raise ValueError("Azure credentials not found in environment variables. Please check your .env file.")
    
    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document("prebuilt-bankStatement.us", body=uploaded_file.getvalue())
    return poller.result()

def process_statements(results):
    """Process multiple bank statements and aggregate data"""
    all_transactions = []  # Store transactions from all statements
    daily_balances = []  # Store daily balances for average computation

    for idx, result in enumerate(results):
        for statement in result.documents:
            st.header(f"Statement {idx + 1}")

            with st.expander("Basic Information", expanded=True):
                col1, col2 = st.columns(2)
                with col1:
                    if statement.fields.get("AccountHolderName"):
                        st.metric("Account Holder", statement.fields["AccountHolderName"].value_string)
                    if statement.fields.get("BankName"):
                        st.metric("Bank Name", statement.fields["BankName"].value_string)
                with col2:
                    if statement.fields.get("StatementStartDate"):
                        st.metric("Statement Period", 
                                  f"{statement.fields['StatementStartDate'].value_date} to {statement.fields['StatementEndDate'].value_date}")

            if statement.fields.get("Accounts"):
                for account in statement.fields["Accounts"].value_array:
                    account_data = account.value_object
                    transactions_data = []

                    if account_data.get("Transactions"):
                        for transaction in account_data["Transactions"].value_array:
                            t_data = transaction.value_object
                            transactions_data.append({
                                "Date": t_data.get("Date").value_date if t_data.get("Date") else None,
                                "Deposit": t_data.get("DepositAmount").value_number if t_data.get("DepositAmount") else 0,
                                "Withdrawal": t_data.get("WithdrawalAmount").value_number if t_data.get("WithdrawalAmount") else 0,
                            })

                        df = pd.DataFrame(transactions_data)
                        df["Date"] = pd.to_datetime(df["Date"])
                        df = df.sort_values("Date", ascending=True)
                        all_transactions.append(df)

                        # Compute account balances per day
                        balance = account_data["BeginningBalance"].value_number if account_data.get("BeginningBalance") else 0
                        date_range = pd.date_range(start=df["Date"].min(), end=df["Date"].max(), freq='D')
                        daily_balance = pd.DataFrame(index=date_range, columns=["Balance"])
                        daily_balance["Balance"] = balance  # Initialize with starting balance

                        for date in df["Date"].unique():
                            daily_txns = df[df["Date"] == date]
                            balance += daily_txns["Deposit"].sum() - daily_txns["Withdrawal"].sum()
                            daily_balance.loc[date, "Balance"] = balance

                        daily_balance.fillna(method='ffill', inplace=True)  # Fill missing days
                        daily_balances.append(daily_balance)

    if all_transactions:
        aggregate_and_display_results(all_transactions, daily_balances)

def aggregate_and_display_results(all_transactions, daily_balances):
    """Aggregate data across all statements and display summary"""
    combined_df = pd.concat(all_transactions)
    combined_df["Month"] = combined_df["Date"].dt.to_period("M")  # Group transactions by month

    # Aggregate monthly deposit data
    monthly_summary = combined_df.groupby("Month").agg(
        Total_Deposits=pd.NamedAgg(column="Deposit", aggfunc="sum"),
        Number_of_Deposits=pd.NamedAgg(column="Deposit", aggfunc="count")
    ).reset_index()

    # Compute average daily balance across all statements
    full_daily_balance = pd.concat(daily_balances)
    avg_daily_balance = full_daily_balance["Balance"].mean()
    negative_days = (full_daily_balance["Balance"] < 0).sum()

    # Compute monthly average daily balance and negative days
    full_daily_balance["Month"] = full_daily_balance.index.to_period("M")
    monthly_balance_summary = full_daily_balance.groupby("Month").agg(
        Average_Daily_Balance=pd.NamedAgg(column="Balance", aggfunc="mean"),
        Negative_Days=pd.NamedAgg(column="Balance", aggfunc=lambda x: (x < 0).sum())
    ).reset_index()

    # Merge transaction summary with balance summary
    final_summary = pd.merge(monthly_summary, monthly_balance_summary, on="Month", how="left")

    # Display overall summary
    st.subheader("Summary Across All Submitted Statements")
    col1, col2 = st.columns(2)
    col1.metric("Average Daily Balance", f"${avg_daily_balance:,.2f}")
    col2.metric("Average Negative Days", f"{negative_days}")

    # Display monthly breakdown
    st.subheader("Monthly Breakdown")
    st.dataframe(
        final_summary.style.format({
            "Total_Deposits": "${:,.2f}",
            "Average_Daily_Balance": "${:,.2f}",
            "Negative_Days": "{:,.0f}"
        }),
        use_container_width=True
    )

def main():
    st.set_page_config(page_title="Bank Statement Analyzer", page_icon="🏦", layout="wide")
    st.title("Bank Statement Analyzer")
    st.write("Upload bank statements to analyze their contents using Azure AI Document Intelligence")

    uploaded_files = st.file_uploader("Choose bank statements (PDFs)", type=['pdf'], accept_multiple_files=True)

    if uploaded_files:
        results = []
        for uploaded_file in uploaded_files:
            try:
                with st.spinner(f'Analyzing {uploaded_file.name}...'):
                    result = analyze_bank_statement(uploaded_file)
                results.append(result)
            except ValueError as ve:
                st.error(str(ve))
            except Exception as e:
                st.error(f"An error occurred while processing {uploaded_file.name}: {str(e)}")

        if results:
            process_statements(results)

if __name__ == "__main__":
    main()
