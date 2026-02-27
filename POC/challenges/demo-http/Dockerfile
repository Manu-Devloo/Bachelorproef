FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY server.py ./

RUN addgroup -S ctf && adduser -S ctf -G ctf && chown -R ctf:ctf /app
USER ctf

EXPOSE 8080
CMD ["python", "/app/server.py"]
