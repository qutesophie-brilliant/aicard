FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PUBLIC_DEPLOY=1 PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
CMD ["bash", "run.sh"]
