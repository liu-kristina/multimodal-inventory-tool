# Multimodal Inventory Tool

A multi-agent, human-in-the-loop system for small nutraceutical distributors that automates invoice processing, inventory tracking, and procurement decisions — built as a capstone project for the NLP and GenAI program at Easy Learning AI.

**Live demo:** [multimodal-inventory-tool-production.up.railway.app](https://multimodal-inventory-tool-production.up.railway.app)

---

## Background

Small nutraceutical distributors like California Nutraceuticals — a fictional company used for demo purposes — act as the link between overseas manufacturers and American supplement brands. They receive dozens of supplier invoices a month, issue sales invoices to customers, and manually track inventory across spreadsheets and email threads.

The pain points are real: invoices arrive as PDFs in Gmail, stock levels are updated manually, procurement decisions are made from memory, and suppliers and customers are managed manually by paperwork. One missed reorder or wrong unit price can directly impact margins and fulfillment.

This project automates that entire workflow — from invoice arrival to procurement decision — while keeping a human in the loop at every critical step.

---

## Stack

| Layer | Tool |
|-------|------|
| Frontend | Dash + Dash Bootstrap Components |
| AI reasoning | Claude (Anthropic) via `claude-sonnet-4-6` |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (local, no data leaves the server) |
| Vector store | ChromaDB |
| Structured data | PostgreSQL (Railway) / SQLite (local) |
| PDF extraction | pdfplumber + PyMuPDF |
| Email | Gmail IMAP + SMTP |
| Deployment | Railway (Docker) |
| CI/CD | GitHub Actions |

---

## Agents

### Invoice Agent
Monitors a Gmail inbox for unread emails with PDF attachments. When an invoice arrives, it extracts the PDF text using pdfplumber, sends it to Claude for structured extraction (invoice number, counterparty, line items, totals, lead times), and saves the result to Postgres. It also classifies each document as a supplier invoice (purchase) or customer invoice (sales) based on the roles of the parties — purchase invoices increase stock, sales invoices decrease it. The agent runs as a background thread toggled from the Agent Control page, or as a standalone Docker service via `cli.py agent watch`.

### Procurement Agent
Monitors stock levels against reorder thresholds. When a product falls below its reorder point, it drafts a Request for Quote (RFQ) email to the supplier and saves it as a pending draft. Drafts are reviewed and approved by a human on the Agent Control page before being sent. Once sent, the agent waits for the supplier's quote reply, parses the pricing and lead time, builds a recommendation, and sends an approval email to the business owner. The owner replies with `APPROVE`, `REJECT`, or `CHANGE` — and the agent handles each path, including fallback supplier logic on rejection.

### Email Feedback Agent
Handles the reply parsing layer for the procurement loop. It reads unread Gmail replies, filters by sender (only approved internal users can approve orders), extracts structured commands from the email body, and routes them back to the procurement agent. Supplier quote replies and internal approval replies are handled through separate Gmail label folders (`procurement/quote` and `procurement/approval`) to prevent misrouting.

---

## Invoice Chat (RAG)

The Invoice Assistant lets users ask natural language questions about the company's invoice history. It uses a Retrieval-Augmented Generation (RAG) pipeline: invoice text is embedded locally using sentence-transformers and stored in ChromaDB. When a question comes in, the most relevant invoices are retrieved and passed to Claude as context. Intent detection routes supplier questions to supplier invoices and customer questions to customer invoices automatically.

Example questions:
- *Who supplies shark cartilage powder?*
- *What is the lead time from Jiaxing Natural Products?*
- *Which customers buy collagen from us?*
- *What is the best price we have paid for fish collagen peptides?*
- *Draft a reorder email for shark cartilage powder*

Invoice text is never sent to a third-party embedding service — all embeddings run locally on the server.

---

## Pages

- **Inventory** — live stock dashboard with low-stock alerts and recent inflow/outflow
- **Invoice Chat** — natural language Q&A over the full invoice history
- **Agent Control** — toggle the invoice agent, run commands, review procurement drafts, and view full approval history
- **About** — team, business context, and privacy notice

---

## Privacy

Questions and relevant invoice excerpts are sent to the Claude API (Anthropic) to generate answers. Invoice text is never sent to a third-party embedding service — semantic search runs locally on the server using sentence-transformers.

---

## Local Setup

```bash
# 1. Clone and install
git clone https://github.com/liu-kristina/multimodal-inventory-tool
cd multimodal-inventory-tool
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Add: ANTHROPIC_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, ALLOWED_USER_EMAILS

# 3. Initialise database and embeddings
python database.py
python pipeline/generate_embeddings.py --rebuild

# 4. Run the app
python app.py
```

---

## Deployment (Railway)

The app is deployed on [Railway](https://multimodal-inventory-tool-production.up.railway.app/) using two Docker services:

- `Dockerfile.app` — the Dash frontend
- `Dockerfile.agent` — the background invoice watch agent

Railway volumes persist ChromaDB (`/app/chroma_db`) and uploaded data (`/app/data`) across deploys. After first deploy:

```bash
railway run python pipeline/generate_embeddings.py --rebuild
```

---

## Slack Agent (Optional Demo Interaction Layer)

A lightweight Slack bot that reads the same database as the CLI, Gmail workflow, and Dash UI — no duplicate logic, no separate database.

**Setup:**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new Slack app.
2. Under **Settings → Socket Mode**, enable Socket Mode.
3. Under **Settings → Basic Information → App-Level Tokens**, create a token with the `connections:write` scope. Copy the `xapp-...` token.
4. Under **Features → OAuth & Permissions**, add these bot token scopes:
   - `app_mentions:read`
   - `chat:write`
5. Install the app to your workspace and copy the `xoxb-...` bot token.
6. Set environment variables:
   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_APP_TOKEN=xapp-...
   ```
7. Run:
   ```bash
   python cli.py slack-agent
   ```
8. In Slack, mention the bot:
   ```
   @Hermes help
   @Hermes inventory
   @Hermes low stock
   @Hermes procurement
   @Hermes memory
   @Hermes demo seed
   ```

### Procurement Approval Reminders (Optional)

When a procurement approval email is created, Hermes can post a one-way reminder to a Slack channel. **Email remains the approval source of truth** — the Slack message is informational only and does not accept replies or buttons.

Set one additional environment variable:

```
SLACK_APPROVAL_CHANNEL=C0ABC1234    # Slack channel ID (not channel name)
```

If `SLACK_APPROVAL_CHANNEL` is not set, the reminder is silently skipped and the procurement workflow continues normally.

To verify the setup:

```bash
python cli.py slack-notify-test
```

This sends a sample reminder to `SLACK_APPROVAL_CHANNEL` without touching the database.

---

## Team

Built by Ying Huang, Kristina Liang, and Moxi Liang as the capstone project for the NLP and GenAI program at [Easy Learning AI](https://easylearning.ai).

*All companies referenced in this application, including California Nutraceuticals and its suppliers and customers, are fictional and created for demo purposes only.*
