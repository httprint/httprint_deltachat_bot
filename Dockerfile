FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libcairo2

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app
COPY app/ .

VOLUME ["/app/data"]

ENTRYPOINT [ "python", "app.py" ]
