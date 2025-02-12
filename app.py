import streamlit as st
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
import pandas as pd
import os
import fitz  # PyMuPDF for PDF page count
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_pdf_page_count(uploaded_file):
    """Returns the number of pages in a PDF file"""
    with fitz.open(stream=uploaded_file.getvalue(), filetype="pdf") as doc:
        return len(doc)

def analyze_bank_statement(uploaded_file):
    """Analyze a single bank statement using Azure AI Document Intelligence"""
    endpoint = os.getenv('AZURE_ENDPOINT')
    key = os.getenv('AZURE_KEY')
    
    if not endpoint or not key:
        raise ValueError("Azure credentials not found in environment variables. Please check your .env file.")

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document("prebuilt-bankStatement.us", body=uploaded_file.getvalue())
    return poller.result()

def process_statements(results, uploaded_files):
    """Process multiple bank statements and display individual and aggregated data"""
    all_daily_balances = []
    all_negative_days = []
    
    for idx, result in enumerate(results):
        all_transactions = []
        daily_balances = []
        
        st.header(f"📑 Bank Statement {idx + 1}: {uploaded_files[idx].name}")

        for statement in result.documents:
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

                        balance = account_data["BeginningBalance"].value_number if account_data.get("BeginningBalance") else 0
                        date_range = pd.date_range(start=df["Date"].min(), end=df["Date"].max(), freq='D')
                        daily_balance = pd.DataFrame(index=date_range, columns=["Balance"])
                        daily_balance["Balance"] = balance  

                        for date in df["Date"].unique():
                            daily_txns = df[df["Date"] == date]
                            balance += daily_txns["Deposit"].sum() - daily_txns["Withdrawal"].sum()
                            daily_balance.loc[date, "Balance"] = balance

                        daily_balance.fillna(method='ffill', inplace=True)
                        daily_balances.append(daily_balance)
                        all_daily_balances.append(daily_balance)
                        all_negative_days.append((daily_balance["Balance"] < 0).sum())

        if all_transactions:
            display_individual_results(all_transactions, daily_balances)
    
    # Compute Summary Across All Statements
    if all_daily_balances:
        merged_balances = pd.concat(all_daily_balances)
        avg_daily_balance = merged_balances["Balance"].mean()
        total_negative_days = sum(all_negative_days)
        
        st.header("📊 Summary Across All Submitted Statements")
        col1, col2 = st.columns(2)
        col1.metric("Average Daily Balance", f"${avg_daily_balance:,.2f}")
        col2.metric("Average Negative Days", f"{total_negative_days}")

def display_individual_results(all_transactions, daily_balances):
    """Display results for a single bank statement"""
    combined_df = pd.concat(all_transactions)
    combined_df["Month"] = combined_df["Date"].dt.to_period("M")

    monthly_summary = combined_df.groupby("Month").agg(
        Total_Deposits=pd.NamedAgg(column="Deposit", aggfunc="sum"),
        Number_of_Deposits=pd.NamedAgg(column="Deposit", aggfunc="count")
    ).reset_index()

    full_daily_balance = pd.concat(daily_balances)
    avg_daily_balance = full_daily_balance["Balance"].mean()
    negative_days = (full_daily_balance["Balance"] < 0).sum()

    st.subheader("📊 Statement Summary")
    col1, col2 = st.columns(2)
    col1.metric("Total Deposits", f"${monthly_summary['Total_Deposits'].sum():,.2f}")
    col2.metric("Total No. of Deposits", f"{monthly_summary['Number_of_Deposits'].sum()}")

    col3, col4 = st.columns(2)
    col3.metric("Average Daily Balance", f"${avg_daily_balance:,.2f}")
    col4.metric("Negative Days", f"{negative_days}")

    st.subheader("📅 Monthly Breakdown")
    st.dataframe(
        monthly_summary.style.format({
            "Total_Deposits": "${:,.2f}",
            "Number_of_Deposits": "{:,.0f}"
        }),
        use_container_width=True
    )

    st.divider()  # Add a visual separator between statements

def main():
    st.set_page_config(page_title="Bank Statement Analyzer", page_icon="🏦", layout="wide")
    st.title("🏦 Bank Statement Analyzer")
    st.write("Upload bank statements to analyze their contents using Azure AI Document Intelligence")

    uploaded_files = st.file_uploader("Choose bank statements (PDFs)", type=['pdf'], accept_multiple_files=True)

    if uploaded_files:
        results = []
        for uploaded_file in uploaded_files:
            try:
                num_pages = get_pdf_page_count(uploaded_file)
                st.write(f"📄 **{uploaded_file.name}** - {num_pages} pages")

                with st.spinner(f'Analyzing {uploaded_file.name}...'):
                    result = analyze_bank_statement(uploaded_file)
                results.append(result)

            except ValueError as ve:
                st.error(str(ve))
            except Exception as e:
                st.error(f"An error occurred while processing {uploaded_file.name}: {str(e)}")

        if results:
            process_statements(results, uploaded_files)

if __name__ == "__main__":
    main()
