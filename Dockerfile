FROM python:3.14-slim

RUN apt-get update && apt-get install -y wget unzip && \
    wget -q https://releases.hashicorp.com/terraform/1.10.5/terraform_1.10.5_linux_amd64.zip && \
    unzip terraform_1.10.5_linux_amd64.zip -d /usr/local/bin/ && \
    rm terraform_1.10.5_linux_amd64.zip && \
    apt-get clean

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
