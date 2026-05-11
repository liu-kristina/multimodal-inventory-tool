# Demo Steps

## Procurement Memory

The system stores human approval/rejection feedback as structured long-term procurement memory and uses it to add supplier memory scores and risk notes to future recommendations. Embedding-based similar-case retrieval is a future extension.

### How It Works

Each time a user approves, rejects, or redirects a procurement recommendation, the outcome is recorded in the `procurement_memory` table with fields including supplier, unit price, lead time, availability, and the user action taken.

When the system generates a new recommendation for a product, it queries past memory for the same supplier and computes a weighted score:

| Action            | Score Weight |
|-------------------|-------------|
| APPROVE           | +1.0        |
| APPROVE_ANYWAY    |  0.0        |
| CHANGE            | -1.0        |
| REJECT            | -2.0        |
| STOP_PURCHASE     | -3.0        |
| PROVIDE_NEW_QUOTE |  0.0        |

A positive score means the supplier has a good track record. A negative score signals prior negative feedback and triggers a risk note in the recommendation reason.

### Sample Recommendation Reason (with memory)

```
Reorder recommended for Collagen Powder.
Current stock: 150 units. Reorder point: 200 units.
Recommended supplier: Marine BioActives (marine@bioactives.com)
Quoted price: $18.50/unit | Lead time: 7 days | Shipping: $120.00

Memory signal:
  - Past approvals: 2
  - Past rejections: 1
  - Supplier memory score: 0.0
  - Memory note: this supplier has previous approvals.
  - Recent rejection reasons: price too high
```

### Sample Recommendation Reason (no history)

```
Memory signal:
  - Memory note: no prior supplier feedback found.
```

---

## Full Demo Workflow (California Nutraceuticals)

### Prerequisites

```bash
python cli.py demo-seed      # seed inventory + suppliers
python cli.py demo-check     # verify DB state
python scripts/generate_demo_invoices.py  # generate PDF invoices
```

### Step 1 — Trigger Reorder

Stock for Collagen Powder (CUST-DEMO-001) is seeded below the reorder point. The procurement agent detects this and sends an RFQ to the default supplier (Shanghai BioSupply International).

```bash
python cli.py run-once
```

### Step 2 — Supplier Sends Quote

The supplier replies with a quote email. The agent parses it and creates a procurement recommendation.

(In tests, this is simulated via `email-loop-test`.)

### Step 3 — Approval Email Sent to User

The agent emails the user an approval request with the quote details.

### Step 4 — User Approves (APPROVE)

User replies:
```
APPROVE
```

The agent places the order and records a positive memory entry for the supplier.

### Step 5 — User Redirects (CHANGE)

User replies:
```
CHANGE
Supplier: Marine BioActives
Email: marine@bioactives.com
Quantity: 130
Reason: better lead time
```

The agent:
1. Updates the draft status to `change_requested`
2. Upserts the new supplier into `product_supplier_alternates`
3. Sends an RFQ to Marine BioActives
4. Records a CHANGE memory entry for the original supplier

### Step 6 — New Supplier Quote + New Recommendation

Marine BioActives replies with a quote. The agent parses it and sends a new approval email.

### Step 7 — User Approves New Quote

User replies `APPROVE`. Order is placed with Marine BioActives.

---

## Test Commands

```bash
python cli.py memory-test          # procurement memory scoring + reason wording
python cli.py business-demo-test   # full 14-stage business workflow (no Gmail)
python cli.py email-loop-test      # 9-stage email loop (no Gmail)
python cli.py agent-watch-test     # agent watch loop (no Gmail)
python cli.py route-test           # email routing labels
```
