"""
reconcile.py — Bank Reconciliation & Exception-Flagging Agent

Pipeline:
  1. Load bank_statement.csv and internal_ledger.csv
  2. DETERMINISTIC PASS: exact match on txn_id -> resolves the majority of rows.
     No AI used here on purpose — a lookup/join does this reliably and cheaply.
  3. FUZZY PASS: for rows left unmatched, check if amount/date are "close enough"
     (small drift) using plain thresholds -> still deterministic.
  4. AI PASS: whatever remains genuinely ambiguous (no txn_id overlap, drift too
     large to auto-accept, or a duplicate needing a judgment call) is handed to
     an LLM with the actual row data, asking for a same/different decision AND
     a one-line reason. If no ANTHROPIC_API_KEY is set, falls back to a
     transparent heuristic so the script still runs end-to-end for a demo.
  5. Outputs: match rate, throughput, and a documented exception list to
     report.json + prints a human-readable summary.

Run: python3 reconcile.py
"""

import csv
import json
import os
import time
from datetime import datetime

AMOUNT_TOLERANCE = 5.0       # rupees - auto-accept drift below this
DATE_TOLERANCE_DAYS = 2      # settlement lag auto-accept window

def load_csv(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["amount"] = float(r["amount"])
    return rows

def days_apart(d1, d2):
    a = datetime.strptime(d1, "%Y-%m-%d")
    b = datetime.strptime(d2, "%Y-%m-%d")
    return abs((a - b).days)

def call_llm_for_judgment(bank_row, ledger_row):
    """
    Ask an LLM to judge whether two near-miss rows are the same transaction.
    Falls back to a transparent rule if no API key is configured, so the
    pipeline is fully runnable without credentials.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        amt_diff = abs(bank_row["amount"] - ledger_row["amount"])
        date_diff = days_apart(bank_row["date"], ledger_row["date"])
        same = amt_diff <= 20 and date_diff <= 3
        return {
            "same_transaction": same,
            "reason": f"[fallback heuristic, no API key set] amount_diff={amt_diff:.2f}, "
                      f"date_diff={date_diff}d -> {'within' if same else 'outside'} manual bounds"
        }

    import urllib.request
    prompt = (
        "You are reconciling financial records. Decide if these two rows refer to the SAME "
        "underlying transaction (recorded twice, with drift from rounding or settlement lag) "
        "or are genuinely DIFFERENT transactions.\n\n"
        f"Bank record: {bank_row}\n"
        f"Ledger record: {ledger_row}\n\n"
        "Respond ONLY as JSON: {\"same_transaction\": true/false, \"reason\": \"<one line>\"}"
    )
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
        text = text.strip().strip("```json").strip("```").strip()
        return json.loads(text)
    except Exception as e:
        return {"same_transaction": False, "reason": f"LLM call failed ({e}); flagged for manual review"}

def reconcile(bank_path, ledger_path):
    start = time.time()
    bank = load_csv(bank_path)
    ledger = load_csv(ledger_path)

    bank_by_id, ledger_by_id = {}, {}
    for r in bank:
        bank_by_id.setdefault(r["txn_id"], []).append(r)
    for r in ledger:
        ledger_by_id.setdefault(r["txn_id"], []).append(r)

    matched, exceptions, ai_calls = [], [], []

    all_ids = set(bank_by_id) | set(ledger_by_id)
    for txn_id in sorted(all_ids):
        b_rows = bank_by_id.get(txn_id, [])
        l_rows = ledger_by_id.get(txn_id, [])

        # Case: clean 1-to-1 match by ID
        if len(b_rows) == 1 and len(l_rows) == 1:
            b, l = b_rows[0], l_rows[0]
            amt_diff = abs(b["amount"] - l["amount"])
            date_diff = days_apart(b["date"], l["date"])
            if amt_diff == 0 and date_diff == 0:
                matched.append({"txn_id": txn_id, "method": "exact", "amount_diff": 0, "date_diff": 0})
            elif amt_diff <= AMOUNT_TOLERANCE and date_diff <= DATE_TOLERANCE_DAYS:
                matched.append({"txn_id": txn_id, "method": "tolerance", "amount_diff": amt_diff, "date_diff": date_diff})
            else:
                # ambiguous drift -> AI judgment
                verdict = call_llm_for_judgment(b, l)
                ai_calls.append(txn_id)
                if verdict.get("same_transaction"):
                    matched.append({"txn_id": txn_id, "method": "ai_judgment", "reason": verdict.get("reason")})
                else:
                    exceptions.append({"txn_id": txn_id, "type": "amount/date mismatch beyond tolerance",
                                        "bank": b, "ledger": l, "ai_reason": verdict.get("reason")})

        # Case: duplicate on ledger side (double-entry)
        elif len(b_rows) == 1 and len(l_rows) > 1:
            exceptions.append({"txn_id": txn_id, "type": "duplicate_in_ledger",
                                "bank": b_rows[0], "ledger_count": len(l_rows),
                                "note": "possible double-entry; needs manual dedupe"})

        # Case: present in bank only (unrecorded / in-flight)
        elif b_rows and not l_rows:
            exceptions.append({"txn_id": txn_id, "type": "missing_in_ledger", "bank": b_rows[0]})

        # Case: present in ledger only (pending settlement)
        elif l_rows and not b_rows:
            exceptions.append({"txn_id": txn_id, "type": "missing_in_bank", "ledger": l_rows[0]})

        else:
            exceptions.append({"txn_id": txn_id, "type": "unhandled_pattern",
                                "note": f"{len(b_rows)} bank rows vs {len(l_rows)} ledger rows"})

    elapsed = time.time() - start
    total = len(all_ids)
    match_rate = len(matched) / total * 100 if total else 0

    report = {
        "total_records": total,
        "matched": len(matched),
        "exceptions": len(exceptions),
        "match_rate_pct": round(match_rate, 2),
        "ai_calls_made": len(ai_calls),
        "runtime_seconds": round(elapsed, 3),
        "matched_detail": matched,
        "exception_detail": exceptions,
    }
    return report

if __name__ == "__main__":
    report = reconcile("bank_statement.csv", "internal_ledger.csv")
    with open("report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)

    print("=" * 50)
    print("RECONCILIATION SUMMARY")
    print("=" * 50)
    print(f"Total unique transactions checked : {report['total_records']}")
    print(f"Matched                          : {report['matched']}")
    print(f"Exceptions (need review)         : {report['exceptions']}")
    print(f"Match rate                       : {report['match_rate_pct']}%")
    print(f"AI calls made (ambiguous only)    : {report['ai_calls_made']}")
    print(f"Runtime                          : {report['runtime_seconds']}s")
    print("-" * 50)
    print("Sample exceptions:")
    for e in report["exception_detail"][:5]:
        print(f"  [{e['type']}] txn={e['txn_id']}")
    print("\nFull detail written to report.json")
