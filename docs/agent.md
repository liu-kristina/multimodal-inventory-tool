# agent.md — Execution Contract
California Nutraceuticals · Multi-Agent Invoice Pipeline

---

## 1. Canonical Workflow

```
Trigger (CLI / scheduler)
        │
        ▼
   run_pipeline.py          ← orchestrator
        │
        ▼
  Extraction Agent          ← parse raw invoice → structured Invoice
        │
        ▼
  Inventory Agent           ← apply invoice to inventory state
        │
        ▼
  Procurement Agent         ← evaluate stock → generate reorder suggestions
        │
        ▼
  Logging / Output          ← emit structured log records, surface flags
```

Default path: **fully automatic end-to-end**.
Human intervention: **exception only** (see §4).

---

## 2. Role Boundaries

### CLI (`cli.py`)
**Responsibilities**
- Accept user commands and translate them into pipeline calls
- Route to `run_once()`, `run_loop()`, `init-db`, `app`, scenario/PDF modes
- Print top-level status to stdout

**MUST NOT**
- Contain business logic
- Make inventory or procurement decisions
- Duplicate orchestration logic from `run_pipeline.py`

---

### Orchestrator (`run_pipeline.py`)
**Responsibilities**
- Own the canonical execution sequence
- Pass data between agents
- Call `log_*` helpers at each stage
- Trigger human-intervention flags when an agent signals a problem

**MUST NOT**
- Implement extraction, inventory, or procurement logic itself
- Duplicate decision logic that belongs to an agent
- Access external services (Gmail, DB) directly — delegate to agents or helpers

---

### Extraction Agent (`extraction_agent()`)
**Responsibilities**
- Accept a raw invoice dict
- Parse and validate all required fields
- Return a typed `Invoice` dataclass

**MUST NOT**
- Read from or write to the database
- Make inventory decisions
- Call any other agent

---

### Inventory Agent (`inventory_agent()`)
**Responsibilities**
- Accept a validated `Invoice` and the current `inventory` dict
- Apply quantity changes: `purchase` → add stock, `sales` → subtract stock
- Return a list of `InventoryTransaction` records

**MUST NOT**
- Make procurement decisions
- Validate invoice structure (already done by Extraction Agent)
- Access the database directly

---

### Procurement Agent (`procurement_agent.py · ProcurementAgent`)
**Responsibilities**
- Read inventory state from `inventory.db` in **read-only** mode
- Evaluate each item against its reorder point using deterministic rules
- Write recommendation state to its own `recommendations.db`
- Be the **single source of truth** for all reorder decisions

**MUST NOT**
- Parse invoices or PDFs
- Modify inventory state directly (reads `inventory.db` read-only)
- Write procurement logic outside the agent
- Call Gmail or any external service
- Duplicate logic from any other layer

---

## 2b. Agent 2: Procurement Agent

### Goal
Generate procurement recommendations for items at risk of stockout.

### Inputs
- Inventory data read from the main database (`inventory.db`) — **read-only**

### Outputs
- Recommendations stored in `recommendations.db` (separate DB, no coupling to main DB)

### State / Memory
- `procurement_recommendations` table — one row per open recommendation
- `agent_runs` table — one row per agent execution (audit log)

### Decision Logic
- Fully deterministic rule-based threshold checks
- If `current_stock < reorder_point` → create a recommendation
- `shortage = reorder_point − current_stock`
- `suggested_order_qty = max(shortage × 2, reorder_point)`
- `urgency = "high"` if `current_stock <= 0`, else `"medium"`
- **The LLM is NOT used for any decision**

### LLM Usage
- Called only after all decisions are finalised, to generate `llm_reason` and `llm_summary`
- Cannot change `suggested_order_qty`, `urgency`, or decision outcome
- Falls back to deterministic template strings if the API call fails

### Trigger
- **Manual:** `python cli.py run-procurement`
- **Automatic:** triggered at the end of every `run_pipeline()` execution, after all inventory updates are applied

### Guardrails
- Does not modify inventory state
- Does not place purchase orders or contact suppliers
- All recommendations are written with `status = 'pending_review'` — human review required before action
- Deduplication: if an `item_name` already has an open `pending_review` recommendation, a new one is not created

### Current Status
- MVP complete
- Supports rule-based recommendations with optional LLM explanation
- Supplier selection not yet implemented



---

## 3. Autonomy Rules

The system MUST execute the following automatically, without human input:

| Condition | Automatic Action |
|---|---|
| Invoice fields fully populated | Continue to Inventory Agent |
| `document_type` is `purchase` or `sales` | Apply stock change |
| Invoice number not a duplicate | Save and process |
| Price within ±50% of historical | Continue without flag |
| All products recognized | Continue without flag |
| `current_stock >= reorder_point` | No action, log and continue |
| `current_stock < reorder_point` | Generate suggestion, log, continue |
| Procurement suggestion generated | Log and continue — no human approval needed |

**Default: keep running. Stop only on explicit flag conditions (§4).**

---

## 4. Human Intervention Rules

The system MUST stop and flag for review under these conditions:

### Extraction
- Any required field is missing or unparseable: `invoice_number`, `invoice_type`, `line_items`
- `document_type` is not `purchase` or `sales`
- `grand_total` is zero or negative

### Inventory
- A stock update would produce a physically impossible state (e.g. negative stock after sales)
- An unrecognised product appears on the invoice and cannot be matched

### Invoice Integrity
- Duplicate `invoice_number` detected
- `invoice_type` cannot be determined

### Procurement
- A suggestion's `recommended_qty` exceeds a configurable safety ceiling (e.g. 10× reorder point)
- Inventory state is inconsistent (missing `reorder_point` for a tracked product)

### Price Anomalies (handled by `invoice_agent.py`)
- Unit price deviates > 50% from historical `stock.unit_price`

**Flagging mechanism**: write a record to `agent_flags` table with `reason`, `details`, `resolved=0`.  
**Pipeline behavior after flag**: log the flag and skip the affected item — do not halt the entire run.

---

## 5. Guardrails

### Single Source of Truth
- Reorder decision logic MUST exist **only** inside `ProcurementAgent.run()`
- `detect_low_stock()` in `run_pipeline.py` MUST NOT be used as a decision gate — it is retained for reference only
- Procurement decisions MUST be derived from `ProcurementAgent` output, not computed independently

### No Logic Duplication
- If two layers implement the same decision, one MUST be removed
- The orchestrator MUST NOT re-implement agent logic inline

### Agent Independence
- Each agent MUST be callable in isolation with only its required inputs
- Agents MUST NOT import each other
- Agents MUST NOT share mutable state

### Data Flow Direction
```
CLI → Orchestrator → Extraction → Inventory → Procurement → Logs
```
Data flows **forward only**. No agent may call a prior stage.

---

## 6. Execution Modes

### `run_once()`
- Execute exactly one full pipeline cycle
- Process all pending inputs (emails, PDFs, or scenario JSON)
- Emit logs and flags for that cycle
- Exit cleanly
- **Use for**: manual runs, CI, cron jobs, CLI `agent once`

### `run_loop()`
- Call `run_once()` repeatedly with a sleep interval (`POLL_INTERVAL`)
- Catch and log exceptions per cycle — a single failure MUST NOT stop the loop
- Continue until explicitly stopped (signal, `stop_watch()`, or Ctrl-C)
- **Use for**: always-on background agent, CLI `agent watch`

```python
while running:
    try:
        run_once()
    except Exception as e:
        log_error(e)
    sleep(POLL_INTERVAL)
```

---

## 7. Logging and State Requirements

Every autonomous run MUST emit structured log records.

### Required Log Events

| Event | When | Minimum Fields |
|---|---|---|
| `started` | Beginning of `run_once()` | `timestamp`, `mode`, `trigger` |
| `processing` | Each invoice being handled | `timestamp`, `invoice_id`, `document_type`, `counterparty` |
| `completed` | End of `run_once()` | `timestamp`, `invoices_processed`, `errors`, `suggestions_count` |
| `flagged` | Any human-intervention condition (§4) | `timestamp`, `reason`, `details`, `invoice_id` |
| `failed` | Unhandled exception in any agent | `timestamp`, `agent`, `error`, `traceback` |

### State Requirements
- `agent_flags` table MUST be writable during any run
- `agent_log` table MUST record at minimum `started`, `completed`, `failed` events
- `agent_state` table MUST reflect current `active` status and `last_run` timestamp
- A run MUST update `last_status` on completion or failure

### Observability Rule
Any autonomous action that modifies data (stock update, invoice save, embedding) MUST produce a corresponding log record.

---

## 8. Future Extensions

### Quote Agent *(planned)*
- Accepts a list of `ProcurementAgent` suggestions
- Queries supplier price lists or APIs
- Returns quoted prices and lead times per item
- MUST be a standalone module — no changes to existing agents
- Inserted into the pipeline **after** `ProcurementAgent`, before final logging
- MUST NOT make reorder decisions — that remains `ProcurementAgent`'s responsibility

### Scheduler / Self-Running Loop *(planned)*
- Replace manual `run_loop()` with a lightweight scheduler (e.g. `APScheduler` or system cron)
- Schedule `run_once()` at configurable intervals
- MUST log each scheduled execution to `agent_log`
- MUST surface failures without silently swallowing exceptions
- MUST NOT change the internal pipeline flow or agent interfaces

---

*This document is an execution contract. Any deviation from the rules above requires explicit sign-off and an update to this file.*
