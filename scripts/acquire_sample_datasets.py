"""Acquire and prepare 6 diverse public, legally reusable messy datasets across:
1. HR / Employees (Adult Census Income - UCI)
2. E-commerce / Sales (Chipotle Transactions - Markham)
3. Education (Student Performance - UCI)
4. Healthcare (Heart Disease Clinical Data - UCI)
5. Urban Planning / Smart Cities (Capital Bikeshare - UCI)
6. Finance / Banking (Bank Marketing - UCI)
"""

import io
import urllib.request
from pathlib import Path
import pandas as pd

DEST_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_datasets"
DEST_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def acquire_all():
    print(f"Acquiring 6 domain datasets into {DEST_DIR}...")

    # 1. HR: Adult Census Income (UCI)
    print("- Fetching HR (Adult Census Income)...")
    hr_url = "https://cdn.jsdelivr.net/gh/jbrownlee/Datasets@master/adult-all.csv"
    hr_bytes = fetch_bytes(hr_url)
    hr_cols = [
        "age", "workclass", "fnlwgt", "education", "education_num",
        "marital_status", "occupation", "relationship", "race", "sex",
        "capital_gain", "capital_loss", "hours_per_week", "native_country", "salary"
    ]
    df_hr = pd.read_csv(io.BytesIO(hr_bytes), names=hr_cols, skipinitialspace=True)
    df_hr_sample = df_hr.head(1500)
    df_hr_sample.to_csv(DEST_DIR / "hr_employees.csv", index=False)
    print(f"  HR saved: {len(df_hr_sample)} rows, {len(df_hr_sample.columns)} cols")

    # 2. E-commerce: Chipotle Orders (Transactions)
    print("- Fetching E-commerce (Chipotle Order Transactions)...")
    ecom_url = "https://cdn.jsdelivr.net/gh/justmarkham/DAT8@master/data/chipotle.tsv"
    ecom_bytes = fetch_bytes(ecom_url)
    df_ecom = pd.read_csv(io.BytesIO(ecom_bytes), sep="\t")
    # Clean price from string '$2.39' to float for analysis while preserving original item names
    df_ecom["price"] = df_ecom["item_price"].str.replace("$", "", regex=False).astype(float)
    df_ecom_sample = df_ecom.head(1500)
    df_ecom_sample.to_csv(DEST_DIR / "ecommerce_orders.csv", index=False)
    print(f"  E-commerce saved: {len(df_ecom_sample)} rows, {len(df_ecom_sample.columns)} cols")

    # 3. Education: Student Performance (UCI)
    print("- Fetching Education (Student Performance)...")
    edu_url = "https://cdn.jsdelivr.net/gh/guipsamora/pandas_exercises@master/04_Apply/Students_Alcohol_Consumption/student-mat.csv"
    edu_bytes = fetch_bytes(edu_url)
    df_edu = pd.read_csv(io.BytesIO(edu_bytes))
    df_edu.to_csv(DEST_DIR / "education_students.csv", index=False)
    print(f"  Education saved: {len(df_edu)} rows, {len(df_edu.columns)} cols")

    # 4. Healthcare: Heart Disease (UCI)
    print("- Fetching Healthcare (Heart Disease Clinical Data)...")
    health_url = "https://cdn.jsdelivr.net/gh/sharmaroshan/Heart-UCI-Dataset@master/heart.csv"
    health_bytes = fetch_bytes(health_url)
    df_health = pd.read_csv(io.BytesIO(health_bytes))
    df_health.to_csv(DEST_DIR / "healthcare_heart.csv", index=False)
    print(f"  Healthcare saved: {len(df_health)} rows, {len(df_health.columns)} cols")

    # 5. Urban Planning: Bike Sharing (UCI)
    print("- Fetching Urban Planning (Capital Bikeshare Mobility)...")
    urban_url = "https://cdn.jsdelivr.net/gh/udacity/deep-learning@master/first-neural-network/Bike-Sharing-Dataset/hour.csv"
    urban_bytes = fetch_bytes(urban_url)
    df_urban = pd.read_csv(io.BytesIO(urban_bytes))
    df_urban_sample = df_urban.head(1500)
    df_urban_sample.to_csv(DEST_DIR / "urban_mobility.csv", index=False)
    print(f"  Urban saved: {len(df_urban_sample)} rows, {len(df_urban_sample.columns)} cols")

    # 6. Finance: Bank Marketing (UCI)
    print("- Fetching Finance (Bank Direct Marketing)...")
    fin_url = "https://cdn.jsdelivr.net/gh/madmashup/targeted-marketing-predictive-engine@master/banking.csv"
    fin_bytes = fetch_bytes(fin_url)
    df_fin = pd.read_csv(io.BytesIO(fin_bytes))
    df_fin_sample = df_fin.head(1500)
    df_fin_sample.to_csv(DEST_DIR / "finance_banking.csv", index=False)
    print(f"  Finance saved: {len(df_fin_sample)} rows, {len(df_fin_sample.columns)} cols")

    print("\nAll 6 domain datasets acquired successfully!")


if __name__ == "__main__":
    acquire_all()
