# Usa uma imagem leve do Python
FROM python:3.9-slim-bullseye

# Instala as dependências do sistema e o Oracle Instant Client (necessário para cx_Oracle)
WORKDIR /opt/oracle
RUN apt-get update && apt-get install -y libaio1 wget unzip \
    && wget https://download.oracle.com/otn_software/linux/instantclient/1920000/instantclient-basiclite-linux.x64-19.20.0.0.0dbru.zip \
    && unzip instantclient-basiclite-linux.x64-19.20.0.0.0dbru.zip \
    && rm -f instantclient-basiclite-linux.x64-19.20.0.0.0dbru.zip \
    && cd /opt/oracle/instantclient_19_20 \
    && echo /opt/oracle/instantclient_19_20 > /etc/ld.so.conf.d/oracle-instantclient.conf \
    && ldconfig

# Configura o diretório da aplicação
WORKDIR /app

# Copia os arquivos e instala as dependências do Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da sua API
COPY app.py .

# Expõe a porta 5000 (padrão do Flask)
EXPOSE 5000

# Variável para o Flask saber qual é o arquivo principal
ENV FLASK_APP=app.py

# Inicia a aplicação
CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]