# PharmaCam Hub — çoklu eczane merkezi sunucusu
# docker build -f deploy/hub/Dockerfile -t pharmacam-hub .
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PHARMACAM_HUB_DB=/data/hub.db
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pyyaml numpy opencv-python-headless matplotlib
COPY pharmacam ./pharmacam
VOLUME ["/data"]
EXPOSE 9000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9000/api/v1/health')"
CMD ["uvicorn", "--factory", "pharmacam.hub.app:create_hub", "--host", "0.0.0.0", "--port", "9000", "--proxy-headers"]
