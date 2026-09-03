"""
generate_data.py
Generates two synthetic datasets that simulate a real reconciliation problem:
  - bank_statement.csv : what the bank/payment gateway says happened
  - internal_ledger.csv: what the company's internal books say happened

The two are deliberately made to disagree in realistic ways:
  - exact matches (majority of rows)
  - amount drift (paise/rupee rounding, partial refunds)
  - date drift (settlement lag of 1-2 days)
  - missing on one side (in-flight or unrecorded transactions)
  - duplicates (double-entry errors)

Run: python3 generate_data.py
Output: bank_statement.csv, internal_ledger.csv (55 records each side, before drops/dupes)
"""

import csv
import random
from datetime import datetime, timedelta

random.seed(42)

N = 55
START_DATE = datetime(2026, 8, 1)
DESCRIPTIONS = [
    "UPI/Merchant Payout", "NEFT Settlement", "Card Settlement",
    "Subscription Renewal", "Refund Processed", "Vendor Payment",
    "Payout to Seller", "Wallet Topup Settlement"
]

def make_base_records(n):
    records = []
    for i in range(1, n + 1):
        txn_id = f"TXN{1000 + i}"
        date = START_DATE + timedelta(days=random.randint(0, 29))
        amount = round(random.uniform(500, 85000), 2)
        desc = random.choice(DESCRIPTIONS)
        records.append({
            "txn_id": txn_id,
            "date": date.strftime("%Y-%m-%d"),
            "amount": amount,
            "description": desc
        })
    return records

def build_bank_and_ledger(base):
    bank = [dict(r) for r in base]
    ledger = [dict(r) for r in base]

    # 1. Amount drift on a handful of ledger rows (rounding / partial refund)
    for r in random.sample(ledger, 6):
        r["amount"] = round(r["amount"] + random.choice([-2.5, -1.0, 1.0, 3.75, -15.0]), 2)

    # 2. Date drift on the bank side (settlement lag)
    for r in random.sample(bank, 5):
        d = datetime.strptime(r["date"], "%Y-%m-%d") + timedelta(days=random.choice([1, 2]))
        r["date"] = d.strftime("%Y-%m-%d")

    # 3. Drop a few records from ledger (unrecorded/in-flight)
    drop_from_ledger = random.sample([r["txn_id"] for r in ledger], 4)
    ledger = [r for r in ledger if r["txn_id"] not in drop_from_ledger]

    # 4. Drop a few different records from bank (bank-side settlement pending)
    remaining_ids = [r["txn_id"] for r in bank if r["txn_id"] not in drop_from_ledger]
    drop_from_bank = random.sample(remaining_ids, 3)
    bank = [r for r in bank if r["txn_id"] not in drop_from_bank]

    # 5. Duplicate a couple of ledger entries (double-entry error)
    dup_candidates = [r for r in ledger if r["txn_id"] not in drop_from_bank]
    for r in random.sample(dup_candidates, 2):
        dup = dict(r)
        ledger.append(dup)

    random.shuffle(bank)
    random.shuffle(ledger)
    return bank, ledger

def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

if __name__ == "__main__":
    base = make_base_records(N)
    bank, ledger = build_bank_and_ledger(base)
    fields = ["txn_id", "date", "amount", "description"]
    write_csv("bank_statement.csv", bank, fields)
    write_csv("internal_ledger.csv", ledger, fields)
    print(f"bank_statement.csv: {len(bank)} rows")
    print(f"internal_ledger.csv: {len(ledger)} rows")
    print("Done. Ground truth is intentionally messy — that's the point.")
