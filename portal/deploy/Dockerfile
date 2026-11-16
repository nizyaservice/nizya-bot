# Образ портала. Контекст сборки — корень репозитория: копируем только пакет
# portal/, кода бота тут не нужно (портал общается с ним по HTTP через туннель).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY portal/deploy/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY portal/ ./portal/

# Непривилегированный пользователь: портал смотрит в интернет через Caddy,
# и рутом его гонять незачем.
RUN useradd --create-home --uid 10001 portal && chown -R portal:portal /srv
USER portal

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "portal.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
