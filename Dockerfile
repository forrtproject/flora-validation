FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# uid/gid 10001 matches the runAsUser/runAsGroup set in k8s/base/app.yaml, which
# is required for the cluster's hardened_default security profile.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER 10001:10001

EXPOSE 8000

# Railway builds from this Dockerfile (ignoring the Procfile) and routes traffic
# to the port it injects as $PORT, so a hard-coded port leaves the app running
# but unreachable. Kubernetes sets no PORT and gets 8000, matching k8s/base/app.yaml.
# One worker by default, as the Procfile always ran; k8s raises it via WEB_CONCURRENCY.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers \"${WEB_CONCURRENCY:-1}\""]
