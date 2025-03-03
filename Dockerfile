# Use an official Python runtime as the base image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the entire project directory into the container
COPY . /app

# Install the dependencies listed in requirements.txt
RUN pip install -r requirements.txt

# Expose port 8000 to allow external access (optional, for documentation)
EXPOSE 8000

# Command to run the FastAPI application using Uvicorn with dynamic port
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT"]