FROM python:3.12-slim
WORKDIR /app
# 저장소에는 deploy_bundle.zip 하나로 올린다(웹 업로드 편의). 빌드 때 풀어서 쓴다.
COPY . .
RUN python -c "import zipfile; zipfile.ZipFile('deploy_bundle.zip').extractall('.')" && rm -f deploy_bundle.zip
# These tracked reporting files remain authoritative after the legacy bundle overlay.
COPY app/templates/about.html app/templates/about.html
COPY app/templates/base.html app/templates/base.html
COPY app/templates/index.html app/templates/index.html
COPY app/static/style.css app/static/style.css
COPY README.md README.md
COPY eval/heldout7/result_20261015.txt eval/heldout7/result_20261015.txt
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
ENV PUBLIC_DEPLOY=1 PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
CMD ["bash", "run.sh"]
