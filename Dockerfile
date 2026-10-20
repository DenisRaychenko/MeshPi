FROM python:3.12-slim

WORKDIR /app
COPY webui/requirements.txt webui/constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY webui/app.py ./
COPY webui/templates ./templates
COPY webui/static ./static

ENV MESHPI_DB=/data/messages.db MESHPI_PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
