# multimodal-inventory-tool

## Overview

This project builds a multi-agent, human-in-the-loop system that converts commercial invoices into structured transaction data and uses it to support inventory tracking and procurement decisions.

The system processes both purchase and sales invoices, transforming unstructured documents into actionable insights through a pipeline of agents with visual interfaces.


## Problem


Small and medium-sized businesses often rely on manual workflows to:

* Process purchase and sales invoices
* Update inventory records
* Track stock levels and demand
* Provide quotes

This leads to:

* Inefficiency and manual effort
* Data entry errors
* Lack of real-time inventory visibility
* Poor coordination between purchasing and sales


## Solution

We propose a multi-agent system that automates invoice understanding and inventory tracking while keeping humans in the loop.

## Data Schema

The system data model may include the following components:

* **raw_documents**
  Stores uploaded source files and metadata such as document ID, filename, file path, file type, and processing status.

* **extracted_invoices**
  Stores invoice-level fields extracted from each document, such as invoice ID, document type, counterparty information, invoice date, currency, and total amount.

* **extracted_line_items**
  Stores line-item level information, including product name, quantity, unit, unit price, and line total.

* **shipping_logistics**
  Stores logistics-related information when available, such as shipping method, origin, destination, shipment date, expected delivery date, actual delivery date, and lead time.

* **inventory_transactions**
  Stores inventory movement records derived from invoices. Purchase invoices create positive stock changes, while sales invoices create negative stock changes.

* **inventory_master**
  Stores the current inventory state for each normalized item, including current stock, reorder point, safety stock, and related inventory control fields.

* **supplier_catalog**
  Stores supplier-side product information, such as supplier name, item description, quoted price, lead time, MOQ, and packaging details.

* **knowledge_documents**
  Stores text documents used for retrieval, such as invoice summaries, item descriptions, supplier descriptions, procurement notes, and policy text.

## Structured Data and Embedding Knowledge

The system uses both structured data and embedding-based knowledge.

### Structured Data

Structured data includes all exact values needed for operational logic and decision-making, such as:

* invoice fields
* line-item values
* quantities
* prices
* totals
* lead times
* inventory levels
* reorder thresholds

This data supports deterministic tasks such as inventory updates, stock calculations, and price comparison.

### Embedding Knowledge

Embedding-based knowledge is used for semantic retrieval and fuzzy matching. It may include:

* item descriptions
* invoice summaries
* supplier descriptions
* procurement notes
* policy text

This layer supports tasks such as item matching, supplier discovery, retrieval-augmented question answering, and decision explanation.

## Design Principle

The system follows a hybrid design:

* **Structured data** is used for precise calculations and business logic.
* **Embedding-based knowledge** is used for semantic search and contextual retrieval.

This separation helps ensure both accuracy and flexibility in downstream agent workflows.

### Agent 1: Data Extraction Agent

* Input: invoice (PDF)
* Performs OCR and parsing
* Extracts structured data:
  * invoice_id
  * Classifies document type:
        * purchase invoice
        * sales invoice
*Extracts structured data:
    * invoice_id
    * document_type
    * counterparty_name (supplier or customer)
    * invoice_date
    * line items (item, quantity, price)
    * ... (more to add)
Output: standardized JSON

Visual interface:
Users can review and edit extracted fields before confirming.


### Agent 2: Inventory Monitoring Agent

* Input: confirmed structured invoice data
* Converts invoices into inventory transactions:
    * purchase -> stock increase
    * sales -> stock decrease
* Updates inventory records
* Detects low-stock conditions

Visual interface:
Inventory dashboard with:

* Current stock levels
* Low-stock alerts
* Summary of recent inflow and outflow


### Agent 3: Procurement Recommendation Agent

* Input: low-stock items, historical purchase data, and supplier catalog
* Compares vendors based on:
  * price
  * lead time
  * historical purchasing patterns
* Recommends optimal purchasing options

Visual interface:
Vendor comparison table and recommendation view

### Knowledge and Retrieval Layer

* Extracted invoice data serves both operational and knowledge purposes.
    * Structured data supports inventory updates and decision logic
    * Selected records and summaries can be indexed for retrieval

* A hybrid approach is used:

    * Structured queries for:
        * price
        * quantity
        * lead time
    * Embedding-based retrieval for:
        * item descriptions
        * supplier offerings
        * historical procurement patterns

This enables question answering and decision explanation for procurement tasks.