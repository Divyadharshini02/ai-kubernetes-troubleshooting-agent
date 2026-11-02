# The FastAPI backend. It holds no credentials: the restricted kubeconfig is mounted at run time.
FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

LABEL project="troubleshooting-agent" component="backend"

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Only needed to build the image. Removed because the application never uses it, and it carries
# its own copies of libraries that vulnerability scans correctly report.
RUN pip uninstall --yes pip && rm -rf /usr/local/lib/python3.13/ensurepip

COPY app ./app
# Compiled here because the running user cannot write to /app, and the backend starts faster.
RUN python -m compileall -q app

# The database directory is the only place the application writes.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin agent \
    && mkdir data \
    && chown agent:agent data

USER 10001

EXPOSE 8000

# Importing the Kubernetes client takes a while, so allow a slow first start.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
