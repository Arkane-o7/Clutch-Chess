# Stage 1: Build frontend
FROM node:18-alpine AS frontend-builder

WORKDIR /app/ui

# Copy package files
COPY ui/package*.json ./

# Install dependencies
RUN npm install

# Copy UI source
COPY ui/ ./

# Build production bundle
RUN npm run prod

# Stage 2: Python application
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/ui/dist ./ui/dist
COPY --from=frontend-builder /app/ui/index.html ./ui/index.html

# Create static directory for serving
RUN mkdir -p static
RUN cp ui/dist/* static/ 2>/dev/null || true
RUN cp -r ui/static/* static/ 2>/dev/null || true
RUN cp ui/index.html static/index.html 2>/dev/null || true
RUN cp favicon.ico static/favicon.ico 2>/dev/null || true

# Expose port
EXPOSE 10000

# Run with eventlet for WebSocket support
CMD ["python", "-c", "import os; from main import app, socketio; socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))"]
