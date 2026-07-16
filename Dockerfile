FROM python:3.12-slim

WORKDIR /app

# Install pip dependencies first so we can cache the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application + committed database.
# .dockerignore keeps backups/, golden_set/, rulebooks/, tests/, and dev
# tooling out of the image.
COPY . .

# Streamlit binds to $PORT if set; default 8501 matches fly.toml.
ENV PORT=8501
EXPOSE 8501

# --server.headless=true skips the "welcome to streamlit" browser prompt
# --server.address=0.0.0.0 is required inside a container
CMD streamlit run app.py \
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
