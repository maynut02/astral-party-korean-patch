FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY tools/patch_watcher_requirements.txt /tmp/patch_watcher_requirements.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/patch_watcher_requirements.txt \
    && useradd --create-home --uid 10001 watcher

COPY --chown=watcher:watcher tools/patch_watcher.py /app/tools/patch_watcher.py

USER watcher

CMD ["python", "tools/patch_watcher.py", "--interval-seconds", "300"]
