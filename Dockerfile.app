FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY database.py .
COPY pages/ ./pages/
COPY pipeline/ ./pipeline/
COPY invoice_agent.py .

# Data directories are mounted as volumes — not baked into the image
RUN mkdir -p data/invoices data/customer_invoices chroma_db

EXPOSE 8050

CMD ["python", "app.py"]
