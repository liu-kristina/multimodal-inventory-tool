FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY database.py .
COPY agents/ ./agents/
COPY pages/ ./pages/
COPY pipeline/ ./pipeline/
COPY assets/ ./assets/
COPY data/ ./data/

# Ensure runtime directories exist; chroma_db can be backed by a volume.
RUN mkdir -p data/invoices data/customer_invoices chroma_db

EXPOSE 8050

CMD ["python", "app.py"]
