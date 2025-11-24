FROM python:3.10-slim

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create an empty log file to avoid permission issues if mounted
RUN touch log.log

# Run the application
# You can override the interval using CMD in docker-compose or docker run
# e.g., CMD ["python", "main.py", "--interval", "30"]
CMD ["python", "main.py"]

