FROM python:3.11-slim
WORKDIR /app
COPY x402-gate-serve.py consume.py ./
EXPOSE 8000
CMD ["python3", "x402-gate-serve.py", "--upstream", "$UPSTREAM", "--port", "8000", "--payto", "$PAYTO", "--free-tier", "5", "--free-window", "60"]
