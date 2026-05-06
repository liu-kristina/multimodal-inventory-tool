# System Design

## 1. Project Goal

Build a minimal multi-agent system that processes invoices, updates inventory, and generates basic procurement recommendations.

The system supports:
- purchase invoices (increase inventory)
- sales invoices (decrease inventory)

The first milestone is to build a simple end-to-end pipeline using a synthetic day-level workflow.

## 2. System Overview

The system contains three core components:

- Extraction Agent: converts invoice input into structured JSON  
- Inventory Agent: updates inventory based on extracted data  
- Procurement Agent: generates a recommendation when stock is low  

The system will run on a small dataset representing one day of operations.

## 3. Pipeline

The minimal pipeline is:

invoice input  → extraction agent  → structured JSON  → inventory agent  → updated inventory  → low-stock check  → procurement agent  → recommendation  

For MVP, the pipeline runs on a one-day scenario.

## 4. Data Design

### 4.1 Structured Data

Used for all exact values and logic:
- prices
- quantities
- totals
- inventory levels

### 4.2 Text Data (for future)

Text data is reserved for retrieval or RAG in future extenstions. The data might include sources of :
- item descriptions  
- supplier descriptions  
- invoice summaries  

Structured data is used for computation.  
Text data may be used later for semantic tasks.

## 5. Data Minimal Schema

### Invoice

- invoice_id  
- document_type (purchase / sales, defined based on the seller/buyer on the invoice)  
- counterparty_name  
- invoice_date  
- total_amount  

### Line Item

- item_name_raw  
- quantity  
- unit_price  

### Inventory Transaction

- item_name  
- quantity_change  
- transaction_type  
- transaction_date  


## 6. Agent Responsibilities

### Extraction Agent

Input:
- invoice PDF (generated from synthetic structured data)

Output:
- structured JSON

Responsibilities:
- read invoice input  
- extract key fields  
- parse line items  
- determine document_type  


### Inventory Agent

Input:
- structured invoice JSON  
- current inventory  

Output:
- updated inventory  

Responsibilities:
- purchase → increase stock  
- sales → decrease stock  
- update inventory values  
- detect low-stock  


### Procurement Agent

Input:
- inventory state  

Output:
- recommendation  

Responsibilities:
- check for low-stock items  
- generate simple recommendation  

Baseline logic:
- if stock < threshold → recommend reorder  


## 7. Core Rules of the system

- purchase → stock increase 
- sales → stock decrease

- if stock < threshold → trigger procurement  


## 8. Day-Level Workflow Scenario

The MVP is demonstrated using a synthetic day-level workflow consisting of multiple invoice documents.

Initial inventory:
- Item A: current_stock = 20, reorder_point = 30
- Item B: current_stock = 60, reorder_point = 20

Day 1 invoices:

1. Purchase invoice from Supplier Alpha
   - Item A, quantity 50, unit_price 8.0

2. Sales invoice to Customer X
   - Item A, quantity 25, unit_price 12.0

3. Sales invoice to Customer Y
   - Item A, quantity 20, unit_price 12.5

4. Sales invoice to Customer Z
   - Item B, quantity 10, unit_price 5.0

Expected behavior:
- inventory is updated after each invoice
- Item A falls below its reorder point
- a low-stock alert is triggered for Item A
- the procurement agent generates a reorder recommendation


## 9. Example

Example invoice:

- document_type: sales  
- item: Item A  
- quantity: 20  

Example structured output:

{
  "document_type": "sales",
  "line_items": [
    {
      "item_name_raw": "Item A",
      "quantity": 20,
      "unit_price": 6.0
    }
  ]
}

Example inventory effect:
- Item A stock decreases by 20

If stock < threshold:
- procurement recommendation is triggered


## 10. MVP Scope

The MVP should include:

- runnable end-to-end pipeline  
- basic extraction logic
- inventory update logic  
- low-stock detection  
- baseline procurement recommendation  
- one day-level scenario  

The MVP does not require:

- perfect extraction  
- advanced optimization  
- full RAG pipeline  
- complex UI  


## 11. Implementation Strategy

1. build a simple pipeline skeleton  
2. run it on a day-level scenario  
3. replace placeholder logic with improved modules  
4. iterate quickly  

Focus on:
- simplicity  
- correctness  
- fast iteration  

## 12. Logging and Debugging (MVP)

The system should include simple logging to track how inventory changes over time.

After processing each invoice, the system should print:

- invoice id and type  
- processed line items  
- updated inventory state  

Example log:

Processing Invoice 1 (purchase)
- Item 1 +50
Current Inventory:
- Item 1: 70

Processing Invoice 2 (sales)
- Item 1 -25
Current Inventory:
- Item 1: 45

Processing Invoice 3 (sales)
- Item 1 -20
Current Inventory:
- Item 1: 25

LOW STOCK TRIGGERED:
- Item 1