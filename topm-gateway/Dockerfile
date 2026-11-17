FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY topm_gateway ./topm_gateway
COPY wsgi.py .
ENV GATEWAY_DB_PATH=/data/gateway.sqlite PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "--timeout", "120", "wsgi:app"]
