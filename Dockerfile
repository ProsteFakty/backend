FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Only dependencies live in the image. Code is bind-mounted from the git checkout (see docker-compose.yml).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
