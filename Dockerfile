FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.138.0" \
    "uvicorn[standard]>=0.49.0" \
    "ortools>=9.14.0" \
    "openpyxl>=3.1.5"

COPY server.py progress.py ./
COPY solver/ solver/

ENV TIMETABLER_NO_AUTH=1

EXPOSE 8002

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
