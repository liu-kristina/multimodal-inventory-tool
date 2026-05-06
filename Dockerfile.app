FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY database.py .
COPY cli.py .
COPY agents/ ./agents/
COPY pages/ ./pages/
COPY pipeline/ ./pipeline/
COPY assets/ ./assets/
COPY scripts/ ./scripts/
COPY data/ ./data/

# Create mount points — actual data lives on Railway volumes, not in the image
RUN mkdir -p /app/data/invoices /app/data/customer_invoices /app/chroma_db

EXPOSE 8050

CMD ["python", "app.py"]
